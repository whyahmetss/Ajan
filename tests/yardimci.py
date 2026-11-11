"""Testler için sentetik mum verisi ve sahte LLM. Sentetik veri sadece kodun doğruluğunu test eder;
stratejinin kârlılığı hakkında HİÇBİR şey söylemez."""
import json

import numpy as np
import pandas as pd

from tests.sahte_mt5 import MUM_DTYPE

PAZARTESI = int(pd.Timestamp("2024-01-01").timestamp())       # 2024-01-01 Pazartesi 00:00
STANDART_BILGI = {"point": 1e-5, "digits": 5, "trade_tick_size": 1e-5, "trade_tick_value": 1.0,
                  "volume_min": 0.01, "volume_step": 0.01, "volume_max": 100.0, "trade_stops_level": 0}


def rastgele_mumlar(n: int, baslangic: int = PAZARTESI, periyot: int = 3600, fiyat: float = 1.1,
                    oynaklik: float = 0.0012, tohum: int = 0, hafta_sonu: bool = True,
                    spread_pt: int = 12) -> np.ndarray:
    """Trend rejimli rastgele yürüyüş (strateji sinyal üretsin diye). hafta_sonu=False: Cmt/Paz yok."""
    rng = np.random.default_rng(tohum)
    zamanlar, t = [], baslangic
    while len(zamanlar) < n:
        if hafta_sonu or pd.Timestamp(t, unit="s").weekday() < 5:
            zamanlar.append(t)
        t += periyot
    egilim = np.repeat(rng.normal(0, oynaklik / 4, n // 300 + 1), 300)[:n]
    getiri = egilim + rng.standard_t(4, n) * oynaklik / np.sqrt(2)
    c = fiyat * np.exp(np.cumsum(getiri))
    o = np.r_[fiyat, c[:-1]]
    h = np.maximum(o, c) * (1 + np.abs(rng.normal(0, oynaklik * 0.6, n)))
    l = np.minimum(o, c) * (1 - np.abs(rng.normal(0, oynaklik * 0.6, n)))
    a = np.zeros(n, MUM_DTYPE)
    a["time"], a["open"], a["high"], a["low"], a["close"] = zamanlar, o, h, l, c
    a["tick_volume"], a["spread"] = 100, spread_pt
    return a


def ust_mumlar(alt: np.ndarray, periyot: int = 14400) -> np.ndarray:
    """Alt zaman dilimi mumlarından üst zaman dilimi (sunucu saatine hizalı)."""
    df = pd.DataFrame(alt)
    df["g"] = df["time"] // periyot * periyot
    g = df.groupby("g").agg(open=("open", "first"), high=("high", "max"), low=("low", "min"),
                            close=("close", "last"), spread=("spread", "min")).reset_index()
    a = np.zeros(len(g), MUM_DTYPE)
    a["time"] = g["g"]
    for k in ("open", "high", "low", "close", "spread"):
        a[k] = g[k]
    a["tick_volume"] = 100
    return a


def elle_mumlar(satirlar, baslangic: int = PAZARTESI, periyot: int = 3600, spread_pt: int = 0) -> np.ndarray:
    """[(open, high, low, close), ...] -> ardışık mumlar."""
    a = np.zeros(len(satirlar), MUM_DTYPE)
    for i, (o, h, l, c) in enumerate(satirlar):
        a[i] = (baslangic + i * periyot, o, h, l, c, 100, spread_pt, 0)
    return a


class SahteLLM:
    """Gerçek API çağırmaz, para harcamaz. cevaplar: {(rol_adi): dict | Exception | callable}.
    rol_adi: 'boga','ayi','makro','risk' (sistem metninden bulunur) ya da 'yargic:<saglayici>'."""

    def __init__(self, cevaplar: dict):
        self.cevaplar = cevaplar
        self.cagrilar = []
        self.cagri_sayisi = 0

    def _rol(self, saglayici, sistem):
        if "baş karar verici" in sistem:
            return f"yargic:{saglayici}"
        for ad, anahtar in (("boga", "BOĞA"), ("ayi", "AYI ANALİSTİ"), ("makro", "MAKRO"), ("risk", "RİSK ANALİSTİ")):
            if anahtar in sistem:
                return ad
        return "?"

    def sor_json(self, saglayici, sistem, kullanici, rol="ajan"):
        r = self._rol(saglayici, sistem)
        self.cagrilar.append(r)
        self.cagri_sayisi += 1
        c = self.cevaplar.get(r)
        if callable(c):
            c = c()
        if isinstance(c, Exception):
            raise c
        if c is None:
            raise RuntimeError(f"sahte LLM: {r} için cevap yok")
        return json.loads(json.dumps(c))

    def kullanim_kopya(self):
        return {"sahte": {"cagri": self.cagri_sayisi, "hata": 0, "giris": 100 * self.cagri_sayisi,
                          "cikis": 10 * self.cagri_sayisi}}

    def maliyet_usd(self, kullanim=None):
        return 0.0, []

    def ozet(self):
        return f"{self.cagri_sayisi} sahte çağrı"


def onaylayan_llm(**degisiklik):
    c = {"boga": {"oy": "DESTEK", "guven": 80, "gerekceler": ["trend"]},
         "ayi": {"oy": "NOTR", "guven": 40, "gerekceler": ["risk az"]},
         "makro": {"oy": "DESTEK", "guven": 70, "gerekceler": ["takvim temiz"]},
         "risk": {"oy": "DESTEK", "guven": 75, "gerekceler": ["R:R iyi"]},
         "yargic:claude": {"karar": "ONAY", "guven": 80, "ozet": "temiz kurulum"},
         "yargic:grok": {"karar": "ONAY", "guven": 75, "ozet": "onay"}}
    c.update(degisiklik)
    return SahteLLM(c)
