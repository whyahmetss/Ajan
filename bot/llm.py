"""Claude ve Grok için ortak arayüz. Ajanlar 'claude', 'claude_hizli', 'grok', 'grok_hizli' der, burası halleder."""
import json
import logging
import os
import re
import time

log = logging.getLogger("llm")


class LLM:
    def __init__(self, cfg):
        self.cfg = cfg["llm"]
        self._claude = None
        self._grok = None
        self.cagri_sayisi = 0

    def _claude_istemci(self):
        if self._claude is None:
            import anthropic
            if not os.getenv("ANTHROPIC_API_KEY"):
                raise RuntimeError("ANTHROPIC_API_KEY .env dosyasında yok")
            self._claude = anthropic.Anthropic(timeout=self.cfg["zaman_asimi_sn"])
        return self._claude

    def _grok_istemci(self):
        if self._grok is None:
            from openai import OpenAI
            if not os.getenv("XAI_API_KEY"):
                raise RuntimeError("XAI_API_KEY .env dosyasında yok")
            self._grok = OpenAI(api_key=os.environ["XAI_API_KEY"], base_url="https://api.x.ai/v1",
                                timeout=self.cfg["zaman_asimi_sn"])
        return self._grok

    def sor(self, saglayici: str, sistem: str, kullanici: str) -> str:
        model = self.cfg[saglayici]
        mt = self.cfg["max_token"]
        for deneme in range(3):
            try:
                self.cagri_sayisi += 1
                if saglayici.startswith("claude"):
                    r = self._claude_istemci().messages.create(
                        model=model, max_tokens=mt, system=sistem,
                        messages=[{"role": "user", "content": kullanici}])
                    return "".join(b.text for b in r.content if getattr(b, "type", "") == "text")
                else:
                    r = self._grok_istemci().chat.completions.create(
                        model=model, max_tokens=mt,
                        messages=[{"role": "system", "content": sistem},
                                  {"role": "user", "content": kullanici}])
                    return r.choices[0].message.content or ""
            except Exception as ex:
                log.warning("%s (%s) hata, deneme %d: %s", saglayici, model, deneme + 1, ex)
                time.sleep(2 * (deneme + 1))
        raise RuntimeError(f"{saglayici} yanıt vermedi")

    def sor_json(self, saglayici: str, sistem: str, kullanici: str) -> dict:
        metin = self.sor(saglayici, sistem + "\n\nSADECE geçerli JSON döndür. Başka metin yazma.", kullanici)
        return json_cikar(metin)


def json_cikar(metin: str) -> dict:
    metin = re.sub(r"```(?:json)?", "", metin)
    bas, son = metin.find("{"), metin.rfind("}")
    if bas == -1 or son == -1:
        raise ValueError(f"JSON bulunamadı: {metin[:200]}")
    return json.loads(metin[bas:son + 1])
