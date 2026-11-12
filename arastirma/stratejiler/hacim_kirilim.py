"""K2 (mevcut strateji, kıyas tabanı) ve V1: K2 + hacim onayı. Sadece kripto, sadece alış, H1 veri / günlük sinyal."""
from types import SimpleNamespace

import numpy as np
import pandas as pd

from .ortak import Seri, emirler


def _k2_maske(s: Seri, prm) -> np.ndarray:
    """Günün ilk mumu kapanınca: dünkü günlük kapanış son 20 günün en yüksek kapanışı ve > SMA200."""
    with np.errstate(invalid="ignore"):
        return s.gun_ilk & (s.d_c > s.d_sma200) & (s.d_c >= s.d_maks20) & (s.d_atr > 0)


def _havuz_maske(s: Seri) -> np.ndarray:
    """Rastgele giriş havuzu: yükseliş trendindeki HER günün ilk mumu (aynı saat, aynı rejim)."""
    with np.errstate(invalid="ignore"):
        return s.gun_ilk & (s.d_c > s.d_sma200) & (s.d_atr > 0)


def _emir(s: Seri, maske, prm):
    i = np.flatnonzero(maske)
    with np.errstate(invalid="ignore", divide="ignore"):
        skor = s.d_c[i] / s.d_sma200[i]
    return emirler(i, np.ones(len(i), np.int8), i + int(prm["max_mum"]), skor, sl_mesafe=prm["sl_gunluk_atr"] * s.d_atr[i])


def gunluk_hacim_orani(s: Seri, gun: int) -> np.ndarray:
    """Her mum için: DÜNKÜ günlük hacim / ondan ÖNCEKİ `gun` günün ortalaması. Bugünün hacmi kullanılmaz."""
    g = s.t // 86400
    gunluk = pd.Series(s.hacim).groupby(g).sum()
    onceki_ort = gunluk.shift(1).rolling(gun).mean()
    oran = (gunluk / onceki_ort).to_numpy(float)
    pos = np.searchsorted(gunluk.index.to_numpy(), g) - 1
    return np.where(pos >= 0, oran[np.clip(pos, 0, None)], np.nan)


def _kripto(s: Seri) -> bool:
    return s.sinif == "kripto"


K2 = SimpleNamespace(
    ZD="H1", PRM={"sl_gunluk_atr": 2.0, "max_mum": 240}, KOMSU={"sl_gunluk_atr": [1.6, 2.4], "max_mum": [192, 288]},
    evren=_kripto, sinyal=lambda s, prm: _emir(s, _k2_maske(s, prm), prm),
    havuz=lambda s, prm: _emir(s, _havuz_maske(s), prm))


def _v1_sinyal(s: Seri, prm):
    with np.errstate(invalid="ignore"):
        hacim_ok = gunluk_hacim_orani(s, int(prm["hacim_gun"])) >= prm["hacim_kat"]
    return _emir(s, _k2_maske(s, prm) & hacim_ok, prm)


V1 = SimpleNamespace(
    ZD="H1", PRM={"sl_gunluk_atr": 2.0, "max_mum": 240, "hacim_kat": 1.5, "hacim_gun": 20},
    KOMSU={"hacim_kat": [1.2, 1.8], "hacim_gun": [16, 24], "sl_gunluk_atr": [1.6, 2.4], "max_mum": [192, 288]},
    evren=_kripto, sinyal=_v1_sinyal, havuz=lambda s, prm: _emir(s, _havuz_maske(s), prm))
