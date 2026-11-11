"""Oy sayımı, Ayı vetosu, yargıç ('ikisi' modu), bozuk JSON / LLM hataları. Gerçek API çağrısı YOK."""
from types import SimpleNamespace as NS

import anthropic
import httpx2
import openai
import pytest

from bot import ajanlar, karar
from bot.llm import LLM, LLMHatasi, json_cikar, kullanim_farki
from tests.yardimci import onaylayan_llm


def _oy(ajan, oy, guven=70):
    return {"ajan": ajan, "model": "x", "oy": oy, "guven": guven, "gerekceler": ["g"]}


# ------------------------------------------------------------------ oy sayımı
def test_iki_destek_gecer_bir_destek_gecmez(cfg):
    oylar = [_oy("boga", "DESTEK"), _oy("ayi", "NOTR"), _oy("makro", "DESTEK"), _oy("risk", "NOTR")]
    assert karar.oy_sayimi(oylar, cfg)[0]
    oylar[2]["oy"] = "NOTR"
    assert not karar.oy_sayimi(oylar, cfg)[0]


def test_ayi_vetosu_esik(cfg):
    oylar = [_oy("boga", "DESTEK"), _oy("ayi", "KARSI", 85), _oy("makro", "DESTEK"), _oy("risk", "DESTEK")]
    ok, n = karar.oy_sayimi(oylar, cfg)
    assert not ok and "VETO" in n
    oylar[1]["guven"] = 84
    assert karar.oy_sayimi(oylar, cfg)[0]


def test_ayi_destegi_sayilmaz(cfg):
    oylar = [_oy("boga", "DESTEK"), _oy("ayi", "DESTEK", 99), _oy("makro", "NOTR"), _oy("risk", "NOTR")]
    assert not karar.oy_sayimi(oylar, cfg)[0]


def test_ayi_hata_verirse_red(cfg):
    """Y5 regresyonu: Ayı hata verince veto sessizce kalkmamalı."""
    oylar = [_oy("boga", "DESTEK"), _oy("ayi", "HATA", 0), _oy("makro", "DESTEK"), _oy("risk", "DESTEK")]
    ok, n = karar.oy_sayimi(oylar, cfg)
    assert not ok and "Ayı" in n
    assert not karar.oy_sayimi([o for o in oylar if o["ajan"] != "ayi"], cfg)[0]


def test_destekci_hatasi_destek_sayilmaz(cfg):
    oylar = [_oy("boga", "DESTEK"), _oy("ayi", "NOTR"), _oy("makro", "HATA", 0), _oy("risk", "HATA", 0)]
    assert not karar.oy_sayimi(oylar, cfg)[0]


# ------------------------------------------------------------------ ajan cevabı doğrulama
@pytest.mark.parametrize("cevap, beklenen", [
    ({"oy": "destek", "guven": 80, "gerekceler": ["a", "b"]}, ("DESTEK", 80, ["a", "b"])),
    ({"oy": "KARSI", "guven": "85%", "gerekceler": "tek gerekçe"}, ("KARSI", 85, ["tek gerekçe"])),   # D3
    ({"oy": "NOTR", "guven": 150.4, "gerekceler": None}, ("NOTR", 100, [])),
    ({"oy": "NOTR", "guven": -5, "gerekceler": [1, 2, 3, 4, 5, 6]}, ("NOTR", 0, ["1", "2", "3", "4"])),
])
def test_oy_ayikla(cevap, beklenen):
    assert ajanlar.oy_ayikla(cevap) == beklenen


@pytest.mark.parametrize("cevap", [{"oy": "VETO", "guven": 90}, {"guven": 90}, {"oy": "DESTEK", "guven": "çok"}])
def test_gecersiz_cevap_hata(cevap):
    llm = NS(sor_json=lambda *a, **k: cevap)
    assert ajanlar._ajan_calistir(llm, "boga", "claude_hizli", "{}")["oy"] == "HATA"


def test_llm_istisnasi_hata_oyu():
    def patla(*a, **k):
        raise LLMHatasi("bozuk")
    o = ajanlar._ajan_calistir(NS(sor_json=patla), "ayi", "grok_hizli", "{}")
    assert o["oy"] == "HATA" and o["guven"] == 0 and "bozuk" in o["gerekceler"][0]


@pytest.mark.parametrize("metin, beklenen", [
    ('```json\n{"oy": "DESTEK"}\n```', {"oy": "DESTEK"}),
    ('Analizim: {"karar": "RED", "guven": 10} bitti.', {"karar": "RED", "guven": 10}),
])
def test_json_cikar(metin, beklenen):
    assert json_cikar(metin) == beklenen


@pytest.mark.parametrize("metin", ["JSON yok", "[1, 2]", '{"oy": "DESTEK",}', "} ters {"])
def test_json_cikar_bozuk(metin):
    with pytest.raises(ValueError):
        json_cikar(metin)


# ------------------------------------------------------------------ yargıç
def _baglam():
    return '{"sembol": "EURUSD"}'


def test_ikisi_modu_ikisi_de_onaylamali(cfg):
    llm = onaylayan_llm()
    assert karar.karar_ver(llm, cfg, _baglam(), [])["onay"]
    llm = onaylayan_llm(**{"yargic:grok": {"karar": "RED", "guven": 90, "ozet": "hayır"}})
    assert not karar.karar_ver(llm, cfg, _baglam(), [])["onay"]


def test_dusuk_guvenli_onay_gecmez(cfg):
    llm = onaylayan_llm(**{"yargic:claude": {"karar": "ONAY", "guven": 64, "ozet": "emin değilim"}})
    assert not karar.karar_ver(llm, cfg, _baglam(), [])["onay"]


def test_yargic_hatasi_ve_bozuk_json_red(cfg):
    llm = onaylayan_llm(**{"yargic:grok": LLMHatasi("zaman aşımı")})
    s = karar.karar_ver(llm, cfg, _baglam(), [])
    assert not s["onay"] and "HATA" in s["ozet"]
    llm = onaylayan_llm(**{"yargic:claude": ValueError("JSON bulunamadı")})
    assert not karar.karar_ver(llm, cfg, _baglam(), [])["onay"]


def test_tek_yargic_modu(cfg):
    cfg["karar_modu"] = "claude"
    llm = onaylayan_llm(**{"yargic:grok": {"karar": "RED", "guven": 99, "ozet": "-"}})
    assert karar.karar_ver(llm, cfg, _baglam(), [])["onay"]
    assert "yargic:grok" not in llm.cagrilar


def test_yargic_YARIM_dese_de_carpan_yok(cfg):
    llm = onaylayan_llm(**{"yargic:claude": {"karar": "ONAY", "guven": 80, "boyut": "YARIM", "ozet": "-"}})
    s = karar.karar_ver(llm, cfg, _baglam(), [])
    assert s["onay"] and "carpan" not in s


# ------------------------------------------------------------------ LLM istemcisi (sahte SDK)
def _claude_cevap(metin='{"oy": "DESTEK"}', stop="end_turn", model="claude-haiku-5-5", giris=100, cikis=40):
    icerik = [NS(type="thinking", thinking=""), NS(type="text", text=metin)] if metin is not None else []
    return NS(stop_reason=stop, content=icerik, usage=NS(input_tokens=giris, output_tokens=cikis), model=model)


class SahteClaude:
    def __init__(self, sonuc):
        self.sonuc, self.cagrilar = sonuc, []
        self.messages = NS(create=self._create)
        self.beta = NS(messages=NS(create=self._beta))

    def _create(self, **kw):
        self.cagrilar.append(("normal", kw))
        return self._ver()

    def _beta(self, **kw):
        self.cagrilar.append(("beta", kw))
        return self._ver()

    def _ver(self):
        if isinstance(self.sonuc, Exception):
            raise self.sonuc
        return self.sonuc


def _llm(cfg, monkeypatch, istemci):
    llm = LLM(cfg)
    monkeypatch.setattr(llm, "_claude_istemci", lambda: istemci)
    return llm


def test_claude_basarili_cevap_ve_kullanim(cfg, monkeypatch):
    ist = SahteClaude(_claude_cevap())
    llm = _llm(cfg, monkeypatch, ist)
    assert llm.sor_json("claude_hizli", "sistem", "kullanıcı", rol="ajan") == {"oy": "DESTEK"}
    tur, kw = ist.cagrilar[0]
    assert tur == "normal" and kw["output_config"] == {"effort": "low"} and kw["max_tokens"] == 16000
    assert llm.kullanim["claude-haiku-5-5"] == {"cagri": 1, "hata": 0, "giris": 100, "cikis": 40}
    usd, bilinmeyen = llm.maliyet_usd()
    assert usd == pytest.approx((100 * 0.10 + 40 * 0.50) / 1e6) and bilinmeyen == []


def test_yargic_yedek_model_ve_efor(cfg, monkeypatch):
    ist = SahteClaude(_claude_cevap(model="claude-sonnet-5-5"))
    llm = _llm(cfg, monkeypatch, ist)
    llm.sor("claude", "sistem", "k", rol="yargic")
    tur, kw = ist.cagrilar[0]
    assert tur == "beta" and kw["fallbacks"] == "default" and kw["output_config"] == {"effort": "medium"}
    cfg["llm"]["claude_yedek_model"] = False
    llm2 = _llm(cfg, monkeypatch, ist)
    llm2.sor("claude", "sistem", "k", rol="yargic")
    assert ist.cagrilar[1][0] == "normal"


@pytest.mark.parametrize("cevap, mesaj", [
    (_claude_cevap(stop="max_tokens"), "max_token"),
    (_claude_cevap(metin="   "), "boş cevap"),
    (_claude_cevap(metin=None), "boş cevap"),
    (_claude_cevap(stop="refusal"), "reddetti"),
])
def test_claude_kesik_bos_ret(cfg, monkeypatch, cevap, mesaj):
    llm = _llm(cfg, monkeypatch, SahteClaude(cevap))
    with pytest.raises(LLMHatasi, match=mesaj):
        llm.sor("claude_hizli", "s", "k")


def test_kalici_hata_tekrar_denenmez(cfg, monkeypatch):
    istek = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")
    hata = anthropic.NotFoundError("model yok", response=httpx2.Response(404, request=istek), body=None)
    ist = SahteClaude(hata)
    llm = _llm(cfg, monkeypatch, ist)
    with pytest.raises(LLMHatasi, match="HTTP 404"):
        llm.sor("claude_hizli", "s", "k")
    assert len(ist.cagrilar) == 1                  # bizim kodumuz tekrar denemez
    assert llm.hata_sayisi == 1


def test_baglanti_hatasi(cfg, monkeypatch):
    ist = SahteClaude(anthropic.APIConnectionError(request=httpx2.Request("POST", "https://x")))
    llm = _llm(cfg, monkeypatch, ist)
    with pytest.raises(LLMHatasi, match="bağlantı"):
        llm.sor("claude_hizli", "s", "k")


def test_anahtar_yoksa_acik_hata(cfg):
    with pytest.raises(LLMHatasi, match="ANTHROPIC_API_KEY"):
        LLM(cfg).sor("claude_hizli", "s", "k")


def _grok_cevap(metin='{"oy": "NOTR"}', bitis="stop"):
    return NS(choices=[NS(message=NS(content=metin), finish_reason=bitis)],
              usage=NS(prompt_tokens=200, completion_tokens=900))


@pytest.mark.parametrize("cevap, mesaj", [(_grok_cevap(bitis="length"), "max_token"),
                                          (_grok_cevap(metin=None), "boş cevap")])
def test_grok_kesik_ve_bos(cfg, monkeypatch, cevap, mesaj):
    llm = LLM(cfg)
    monkeypatch.setattr(llm, "_grok_istemci", lambda: NS(chat=NS(completions=NS(create=lambda **kw: cevap))))
    with pytest.raises(LLMHatasi, match=mesaj):
        llm.sor("grok_hizli", "s", "k")
    assert llm.kullanim["grok-4-fast"]["hata"] == 1


def test_grok_kalici_hata(cfg, monkeypatch):
    istek = httpx2.Request("POST", "https://api.x.ai/v1/chat/completions")
    hata = openai.AuthenticationError("anahtar geçersiz", response=httpx2.Response(401, request=istek), body=None)
    cagri = []

    def create(**kw):
        cagri.append(kw)
        raise hata
    llm = LLM(cfg)
    monkeypatch.setattr(llm, "_grok_istemci", lambda: NS(chat=NS(completions=NS(create=create))))
    with pytest.raises(LLMHatasi, match="HTTP 401"):
        llm.sor("grok", "s", "k")
    assert len(cagri) == 1 and cagri[0]["max_tokens"] == 4000


def test_kullanim_farki():
    once = {"m": {"cagri": 2, "hata": 0, "giris": 100, "cikis": 10}}
    sonra = {"m": {"cagri": 5, "hata": 1, "giris": 400, "cikis": 70}, "n": {"cagri": 1, "hata": 0, "giris": 5, "cikis": 1}}
    assert kullanim_farki(once, sonra) == {"m": {"cagri": 3, "hata": 1, "giris": 300, "cikis": 60},
                                           "n": {"cagri": 1, "hata": 0, "giris": 5, "cikis": 1}}
