"""M1: yatay rejimde Bollinger bandından ortalamaya dönüş (freqtrade BbandRsi şablonunun rejim filtreli hali), H4.

Sinyal (i. H4 mumu kapanınca): ADX(14) < adx_esik ve kapanış Bollinger(bb_n, bb_k) bandının dışında -> banda ters yönde.
Çıkış: hedef = SMA(bb_n) (her mumda, bir ÖNCEKİ mumun kapanışına kadarki değerle güncellenir); SL sl_atr x ATR; max_mum."""
from types import SimpleNamespace

import numpy as np
import pandas as pd

from .ortak import Seri, emirler

EVREN = ("EURGBP", "AUDNZD", "EURAUD", "AUDJPY", "CADJPY", "USDCHF", "USDCAD")


def _bant(s: Seri, prm):
    c = pd.Series(s.c)
    n = int(prm["bb_n"])
    sma = c.rolling(n).mean().to_numpy()
    std = c.rolling(n).std(ddof=0).to_numpy()
    return sma, std


def _hedef(sma):
    return np.r_[np.nan, sma[:-1]]                         # k. mumun açılışında bilinen SMA


def _sinyal(s: Seri, prm):
    sma, std = _bant(s, prm)
    with np.errstate(invalid="ignore", divide="ignore"):
        yatay = s.adx < prm["adx_esik"]
        al = yatay & (s.c < sma - prm["bb_k"] * std)
        sat = yatay & (s.c > sma + prm["bb_k"] * std)
        guc = np.abs(s.c - sma) / std
    i = np.flatnonzero(al | sat)
    return emirler(i, np.where(sat[i], -1, 1), i + int(prm["max_mum"]), guc[i], sl_mesafe=prm["sl_atr"] * s.atr[i],
                   hedef=_hedef(sma))


def _havuz(s: Seri, prm):
    sma, _ = _bant(s, prm)
    sec = np.zeros(s.n, bool)
    sec[::2] = True
    sec &= (s.atr > 0) & np.isfinite(sma)
    i = np.flatnonzero(sec)
    ii = np.r_[i, i]
    yon = np.r_[np.ones(len(i), np.int8), -np.ones(len(i), np.int8)]
    return emirler(ii, yon, ii + int(prm["max_mum"]), None, sl_mesafe=prm["sl_atr"] * s.atr[ii], hedef=_hedef(sma))


M1 = SimpleNamespace(
    ZD="H4", PRM={"adx_esik": 20.0, "bb_n": 20, "bb_k": 2.0, "sl_atr": 2.0, "max_mum": 30},
    KOMSU={"adx_esik": [16.0, 24.0], "bb_n": [16, 24], "bb_k": [1.6, 2.4], "sl_atr": [1.6, 2.4], "max_mum": [24, 36]},
    evren=lambda s: s.ad in EVREN, sinyal=_sinyal, havuz=_havuz)
