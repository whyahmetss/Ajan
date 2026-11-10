"""İSTEĞE BAĞLI: TradingAgents (TauricResearch) ile derin analiz.
config.yaml -> derin_analiz.aktif: true yaparsan, hızlı ajanların ONAYLADIĞI adaylar
ek olarak TradingAgents'a da sorulur. İkisi de onaylamazsa işlem açılmaz.
Yavaştır (dakikalar) ve pahalıdır (aday başına onlarca LLM çağrısı) — bu yüzden sadece
son elemeye kalan adaylar için çalışır.

Kurulum:  pip install git+https://github.com/TauricResearch/TradingAgents.git
"""
import logging
import re
from datetime import date

log = logging.getLogger("derin")

# MT5 sembolü -> Yahoo Finance karşılığı (broker eki temizlenir: EURUSD.m -> EURUSD)
ENDEKS_YAHOO = {
    "US30": "^DJI", "US100": "^NDX", "USTEC": "^NDX", "NAS100": "^NDX", "US500": "^GSPC",
    "SPX500": "^GSPC", "GER40": "^GDAXI", "DE40": "^GDAXI", "UK100": "^FTSE", "JP225": "^N225",
    "FRA40": "^FCHI", "EU50": "^STOXX50E", "AUS200": "^AXJO", "HK50": "^HSI",
}
EMTIA_YAHOO = {"XAUUSD": "GC=F", "XAGUSD": "SI=F", "USOIL": "CL=F", "WTI": "CL=F",
               "UKOIL": "BZ=F", "BRENT": "BZ=F"}


def yahoo_sembol(sembol: str, sinif: str) -> str | None:
    s = re.sub(r"[^A-Z0-9]", "", sembol.upper())
    for k, v in {**ENDEKS_YAHOO, **EMTIA_YAHOO}.items():
        if s.startswith(k):
            return v
    if sinif == "kripto":
        baz = s.replace("USDT", "").replace("USD", "")
        return f"{baz}-USD" if baz else None
    if sinif == "forex" and len(s) >= 6:
        return f"{s[:6]}=X"
    return None


class DerinAnaliz:
    def __init__(self, cfg):
        self.cfg = cfg.get("derin_analiz", {}) or {}
        self.aktif = bool(self.cfg.get("aktif"))
        self._ta = None

    def _graf(self):
        if self._ta is None:
            from tradingagents.graph.trading_graph import TradingAgentsGraph
            from tradingagents.default_config import DEFAULT_CONFIG
            c = DEFAULT_CONFIG.copy()
            c["llm_provider"] = self.cfg.get("saglayici", "anthropic")
            if self.cfg.get("derin_model"):
                c["deep_think_llm"] = self.cfg["derin_model"]
            if self.cfg.get("hizli_model"):
                c["quick_think_llm"] = self.cfg["hizli_model"]
            c["max_debate_rounds"] = int(self.cfg.get("tartisma_turu", 1))
            self._ta = TradingAgentsGraph(debug=False, config=c)
        return self._ta

    def onayliyor_mu(self, aday: dict) -> tuple[bool, str]:
        """True: TradingAgents da aynı yönü söylüyor. Hata/eşleşme yoksa ayara göre karar."""
        ys = yahoo_sembol(aday["sembol"], aday["sinif"])
        if ys is None:
            return bool(self.cfg.get("eslesme_yoksa_gecir", False)), "Yahoo karşılığı yok"
        try:
            _, karar = self._graf().propagate(ys, date.today().isoformat())
            k = str(karar).upper()
            if aday["yon"] == "AL":
                ok = "BUY" in k or "OVERWEIGHT" in k
            else:
                ok = "SELL" in k or "UNDERWEIGHT" in k
            return ok, f"TradingAgents({ys}): {str(karar)[:200]}"
        except Exception as ex:
            log.warning("TradingAgents hata: %s", ex)
            return False, f"TradingAgents hata: {ex}"
