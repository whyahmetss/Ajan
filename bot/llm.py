"""Claude ve Grok için ortak arayüz. Ajanlar 'claude', 'claude_hizli', 'grok', 'grok_hizli' der, burası halleder.

- Geçici hatalar (bağlantı, 408/409/429/5xx) SDK'ların kendi yeniden deneme mekanizmasıyla
  tekrarlanır (llm.max_yeniden_deneme). Kalıcı hatalar (400/401/403/404: yanlış model adı,
  geçersiz anahtar) hiç tekrarlanmaz, para ve zaman harcamaz.
- Claude 5.5 modellerinde düşünme (thinking) varsayılan olarak AÇIKTIR ve düşünme tokenları
  max_tokens sınırına dahildir. Bu yüzden efor seviyesi açıkça verilir (llm.claude_efor) ve
  cevap max_tokens'ta kesilirse ya da boş gelirse açık bir hata üretilir.
- Her çağrının token kullanımı model bazında toplanır (aday başına maliyet takibi).
"""
import json
import logging
import os
import re
import threading

log = logging.getLogger("llm")

# Sunucu tarafı yedek model (refusal durumunda) desteklenen Claude modelleri
YEDEKLI_MODELLER = {"claude-sonnet-5-5", "claude-opus-5-5", "claude-opus-5", "claude-fable-5-1",
                    "claude-fable-5-2"}
YEDEK_BETA = "server-side-fallback-2026-07-01"


class LLMHatasi(RuntimeError):
    pass


def _kisa(ex) -> str:
    return str(getattr(ex, "message", None) or ex)[:300]


class LLM:
    def __init__(self, cfg):
        self.cfg = cfg["llm"]
        self._claude = None
        self._grok = None
        self._kilit = threading.Lock()
        self.cagri_sayisi = 0
        self.hata_sayisi = 0
        self.kullanim: dict = {}        # model -> {"cagri", "hata", "giris", "cikis"}

    # ------------------------------------------------------------- istemciler
    def _claude_istemci(self):
        with self._kilit:
            if self._claude is None:
                import anthropic
                if not os.getenv("ANTHROPIC_API_KEY"):
                    raise LLMHatasi("ANTHROPIC_API_KEY .env dosyasında yok")
                self._claude = anthropic.Anthropic(timeout=self.cfg["zaman_asimi_sn"],
                                                   max_retries=self.cfg.get("max_yeniden_deneme", 2))
            return self._claude

    def _grok_istemci(self):
        with self._kilit:
            if self._grok is None:
                from openai import OpenAI
                if not os.getenv("XAI_API_KEY"):
                    raise LLMHatasi("XAI_API_KEY .env dosyasında yok")
                self._grok = OpenAI(api_key=os.environ["XAI_API_KEY"], base_url="https://api.x.ai/v1",
                                    timeout=self.cfg["zaman_asimi_sn"],
                                    max_retries=self.cfg.get("max_yeniden_deneme", 2))
            return self._grok

    # ------------------------------------------------------------- kullanım takibi
    def _kaydet(self, model: str, giris: int, cikis: int, hata: bool = False):
        with self._kilit:
            k = self.kullanim.setdefault(model, {"cagri": 0, "hata": 0, "giris": 0, "cikis": 0})
            k["cagri"] += 1
            k["giris"] += int(giris or 0)
            k["cikis"] += int(cikis or 0)
            self.cagri_sayisi += 1
            if hata:
                k["hata"] += 1
                self.hata_sayisi += 1

    def kullanim_kopya(self) -> dict:
        with self._kilit:
            return {m: dict(k) for m, k in self.kullanim.items()}

    def maliyet_usd(self, kullanim: dict | None = None) -> tuple[float, list[str]]:
        """(toplam USD, fiyatı bilinmeyen modeller). Fiyatlar config: llm.fiyat_usd_mtok."""
        fiyatlar = self.cfg.get("fiyat_usd_mtok") or {}
        toplam, bilinmeyen = 0.0, []
        for m, k in (kullanim if kullanim is not None else self.kullanim_kopya()).items():
            f = fiyatlar.get(m)
            if not f:
                if k["giris"] or k["cikis"]:
                    bilinmeyen.append(m)
                continue
            toplam += (k["giris"] * f[0] + k["cikis"] * f[1]) / 1e6
        return toplam, bilinmeyen

    def ozet(self) -> str:
        parcalar = [f"{m}: {k['cagri']} çağrı/{k['hata']} hata, {k['giris']}+{k['cikis']} token"
                    for m, k in sorted(self.kullanim_kopya().items())]
        usd, bilinmeyen = self.maliyet_usd()
        ek = f" | ~{usd:.4f} USD" + (f" (fiyatı bilinmeyen: {', '.join(bilinmeyen)})" if bilinmeyen else "")
        return ("; ".join(parcalar) or "çağrı yok") + ek

    # ------------------------------------------------------------- çağrılar
    def sor(self, saglayici: str, sistem: str, kullanici: str, rol: str = "ajan") -> str:
        model = self.cfg[saglayici]
        if saglayici.startswith("claude"):
            return self._claude_sor(model, sistem, kullanici, rol)
        return self._grok_sor(model, sistem, kullanici)

    def _claude_sor(self, model: str, sistem: str, kullanici: str, rol: str) -> str:
        import anthropic
        mt = self.cfg["max_token"]
        kw = dict(model=model, max_tokens=mt, system=sistem,
                  messages=[{"role": "user", "content": kullanici}])
        efor = (self.cfg.get("claude_efor") or {}).get(rol)
        if efor:
            kw["output_config"] = {"effort": efor}
        istemci = self._claude_istemci()
        try:
            if self.cfg.get("claude_yedek_model", True) and model in YEDEKLI_MODELLER:
                r = istemci.beta.messages.create(betas=[YEDEK_BETA], fallbacks="default", **kw)
            else:
                r = istemci.messages.create(**kw)
        except anthropic.APIStatusError as ex:
            self._kaydet(model, 0, 0, hata=True)
            raise LLMHatasi(f"{model} HTTP {ex.status_code}: {_kisa(ex)}") from None
        except anthropic.APIConnectionError as ex:          # zaman aşımı da buraya düşer
            self._kaydet(model, 0, 0, hata=True)
            raise LLMHatasi(f"{model} bağlantı/zaman aşımı: {type(ex).__name__}") from None
        sunan = getattr(r, "model", model) or model          # yedek model devreye girdiyse onun adı
        u = r.usage
        bozuk = r.stop_reason in ("refusal", "max_tokens")
        self._kaydet(sunan, u.input_tokens, u.output_tokens, hata=bozuk)
        if r.stop_reason == "refusal":
            raise LLMHatasi(f"{model} isteği reddetti (refusal)")
        if r.stop_reason == "max_tokens":
            raise LLMHatasi(f"{model} cevabı max_token={mt} sınırında kesildi (düşünme tokenları dahil); "
                            f"config: llm.max_token artırılmalı ya da efor düşürülmeli")
        metin = "".join(b.text for b in r.content if getattr(b, "type", "") == "text")
        if not metin.strip():
            raise LLMHatasi(f"{model} boş cevap döndü (stop_reason={r.stop_reason})")
        return metin

    def _grok_sor(self, model: str, sistem: str, kullanici: str) -> str:
        import openai
        mt = self.cfg.get("max_token_grok", self.cfg["max_token"])
        try:
            r = self._grok_istemci().chat.completions.create(
                model=model, max_tokens=mt,
                messages=[{"role": "system", "content": sistem}, {"role": "user", "content": kullanici}])
        except openai.APIStatusError as ex:
            self._kaydet(model, 0, 0, hata=True)
            raise LLMHatasi(f"{model} HTTP {ex.status_code}: {_kisa(ex)}") from None
        except openai.APIConnectionError as ex:
            self._kaydet(model, 0, 0, hata=True)
            raise LLMHatasi(f"{model} bağlantı/zaman aşımı: {type(ex).__name__}") from None
        u = getattr(r, "usage", None)
        secim = r.choices[0] if r.choices else None
        metin = (secim.message.content or "") if secim else ""
        kesik = secim is not None and secim.finish_reason == "length"
        self._kaydet(model, getattr(u, "prompt_tokens", 0), getattr(u, "completion_tokens", 0),
                     hata=kesik or not metin.strip())
        if kesik:
            raise LLMHatasi(f"{model} cevabı max_token={mt} sınırında kesildi (reasoning tokenları dahil "
                            f"olabilir); config: llm.max_token_grok artırılmalı")
        if not metin.strip():
            raise LLMHatasi(f"{model} boş cevap döndü")
        return metin

    def sor_json(self, saglayici: str, sistem: str, kullanici: str, rol: str = "ajan") -> dict:
        metin = self.sor(saglayici, sistem + "\n\nSADECE geçerli JSON döndür. Başka metin yazma.",
                         kullanici, rol)
        return json_cikar(metin)


def kullanim_farki(once: dict, sonra: dict) -> dict:
    """İki kullanım anlık görüntüsü arasındaki fark (ör. tek bir adayın harcaması)."""
    out = {}
    for m, k in sonra.items():
        o = once.get(m, {})
        d = {a: k[a] - o.get(a, 0) for a in ("cagri", "hata", "giris", "cikis")}
        if any(d.values()):
            out[m] = d
    return out


def json_cikar(metin: str) -> dict:
    metin = re.sub(r"```(?:json)?", "", metin)
    bas, son = metin.find("{"), metin.rfind("}")
    if bas == -1 or son == -1 or son < bas:
        raise ValueError(f"JSON bulunamadı: {metin[:200]}")
    veri = json.loads(metin[bas:son + 1])
    if not isinstance(veri, dict):
        raise ValueError("JSON nesne değil")
    return veri
