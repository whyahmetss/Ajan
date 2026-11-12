"""Üçüncü tur adayları (docs/ARASTIRMA_RAPORU_2.md Bölüm 9): S1 (K2'nin satış tarafı), B1 (BTC rejim filtresi),
X1 (K2 kuralı endeks/altın/gümüşte). Parametreler K2 ile aynıdır."""
from types import SimpleNamespace

import numpy as np

from .hacim_kirilim import K2, _emir, _havuz_maske, _k2_maske, _kripto
from .ortak import Seri, emirler

PRM = dict(K2.PRM)
KOMSU = dict(K2.KOMSU)


# ------------------------------------------------------------------ S1
def _s1_maske(s: Seri) -> np.ndarray:
    with np.errstate(invalid="ignore"):
        return s.gun_ilk & (s.d_c < s.d_sma200) & (s.d_c <= s.d_min20) & (s.d_atr > 0)


def _s1_havuz_maske(s: Seri) -> np.ndarray:
    with np.errstate(invalid="ignore"):
        return s.gun_ilk & (s.d_c < s.d_sma200) & (s.d_atr > 0)


def _satis(s: Seri, maske, prm):
    i = np.flatnonzero(maske)
    with np.errstate(invalid="ignore", divide="ignore"):
        skor = s.d_sma200[i] / s.d_c[i]
    return emirler(i, -np.ones(len(i), np.int8), i + int(prm["max_mum"]), skor, sl_mesafe=prm["sl_gunluk_atr"] * s.d_atr[i])


S1 = SimpleNamespace(ZD="H1", PRM=PRM, KOMSU=KOMSU, evren=_kripto,
                     sinyal=lambda s, prm: _satis(s, _s1_maske(s), prm), havuz=lambda s, prm: _satis(s, _s1_havuz_maske(s), prm))


# ------------------------------------------------------------------ B1
def btc_rejimi_ekle(seriler: list) -> None:
    """Her kripto serisine f["btc_ust"] ekler: o mumda bilinen (dünkü) BTC günlük kapanışı > BTC SMA200 mü."""
    btc = next(s for s in seriler if s.ad == "BTCUSD")
    with np.errstate(invalid="ignore"):
        ust = btc.d_c > btc.d_sma200
    for s in seriler:
        if s.sinif == "kripto":
            pos = np.searchsorted(btc.t, s.t, side="right") - 1
            s.f["btc_ust"] = np.where(pos >= 0, ust[np.clip(pos, 0, None)], False)


B1 = SimpleNamespace(ZD="H1", PRM=PRM, KOMSU=KOMSU, evren=_kripto,
                     sinyal=lambda s, prm: _emir(s, _k2_maske(s, prm) & s.btc_ust, prm),
                     havuz=lambda s, prm: _emir(s, _havuz_maske(s), prm))


# ------------------------------------------------------------------ X1
def _x1_evren(s: Seri) -> bool:
    return s.sinif == "endeks" or s.ad in ("XAUUSD", "XAGUSD")


X1 = SimpleNamespace(ZD="H1", PRM=PRM, KOMSU=KOMSU, evren=_x1_evren, sinyal=K2.sinyal, havuz=K2.havuz)
