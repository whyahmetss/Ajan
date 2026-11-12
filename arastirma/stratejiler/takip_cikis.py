"""T2: K2 sinyali + takip eden çıkış. Başlangıç stopu 2 günlük ATR; sonra girişten beri en yüksek GÜNLÜK KAPANIŞ
eksi 3 günlük ATR (sadece gün değişiminde, dünkü kapanış belli olunca güncellenir). Zaman stopu yok."""
from types import SimpleNamespace

import numpy as np

from .hacim_kirilim import _havuz_maske, _k2_maske, _kripto
from .ortak import Seri, emirler


def _emir(s: Seri, maske, prm):
    i = np.flatnonzero(maske)
    with np.errstate(invalid="ignore", divide="ignore"):
        skor = s.d_c[i] / s.d_sma200[i]
    # iz_ref[k], iz_mes[k]: k. mumun açılışında bilinen DÜNKÜ kapanış ve DÜNKÜ günlük ATR
    return emirler(i, np.ones(len(i), np.int8), np.full(len(i), s.n + 10), skor,
                   sl_mesafe=prm["sl_gunluk_atr"] * s.d_atr[i], iz_ref=s.d_c, iz_mes=prm["iz_gunluk_atr"] * s.d_atr)


T2 = SimpleNamespace(
    ZD="H1", PRM={"sl_gunluk_atr": 2.0, "iz_gunluk_atr": 3.0}, KOMSU={"sl_gunluk_atr": [1.6, 2.4], "iz_gunluk_atr": [2.4, 3.6]},
    evren=_kripto, sinyal=lambda s, prm: _emir(s, _k2_maske(s, prm), prm),
    havuz=lambda s, prm: _emir(s, _havuz_maske(s), prm))
