"""T1: oynaklık kırılımı (freqtrade VolatilitySystem şablonu), H4, alış ve satış, bütün sınıflar.

Sinyal (i. H4 mumu kapanınca): kapanış değişimi c[i]-c[i-1], bir ÖNCEKİ mumdaki esik x ATR(14)'ten büyükse o yönde gir.
Çıkış: SL sl_atr x ATR(H4); ters yönde sinyal gelince kapanışta çık; max_mum zaman stopu."""
from types import SimpleNamespace

import numpy as np

from .ortak import Seri, emirler


def _sinyaller(s: Seri, prm):
    c, atr = s.c, s.atr
    degisim = np.r_[np.nan, c[1:] - c[:-1]]
    onceki_atr = np.r_[np.nan, atr[:-1]]
    bitisik = np.r_[False, s.ardisik[:-1]]                 # i-1 ile i ardışık (boşluk üzerinden değişim sayılmaz)
    with np.errstate(invalid="ignore"):
        al = bitisik & (degisim > prm["esik_atr"] * onceki_atr)
        sat = bitisik & (degisim < -prm["esik_atr"] * onceki_atr)
        guc = np.abs(degisim) / onceki_atr
    return al, sat, guc


def _emir(s: Seri, al, sat, guc, prm, cik_al, cik_sat):
    i = np.flatnonzero(al | sat)
    return emirler(i, np.where(sat[i], -1, 1), i + int(prm["max_mum"]), guc[i], sl_mesafe=prm["sl_atr"] * s.atr[i],
                   cik_al=cik_al, cik_sat=cik_sat)


def _sinyal(s: Seri, prm):
    al, sat, guc = _sinyaller(s, prm)
    return _emir(s, al, sat, guc, prm, sat, al)


def _havuz(s: Seri, prm):
    """Her 2 mumdan birinde, iki yönde de giriş; çıkış kuralları (ters sinyal dahil) aynı."""
    al, sat, guc = _sinyaller(s, prm)
    sec = np.zeros(s.n, bool)
    sec[::2] = True
    sec &= s.atr > 0
    i = np.flatnonzero(sec)
    ii = np.r_[i, i]
    yon = np.r_[np.ones(len(i), np.int8), -np.ones(len(i), np.int8)]
    return emirler(ii, yon, ii + int(prm["max_mum"]), None, sl_mesafe=prm["sl_atr"] * s.atr[ii], cik_al=sat, cik_sat=al)


T1 = SimpleNamespace(
    ZD="H4", PRM={"esik_atr": 2.0, "sl_atr": 2.0, "max_mum": 60},
    KOMSU={"esik_atr": [1.6, 2.4], "sl_atr": [1.6, 2.4], "max_mum": [48, 72]},
    evren=lambda s: True, sinyal=_sinyal, havuz=_havuz)
