"""O1 / O2: seans açılışı aralık kırılımı (ORB; day-trader-skill şablonu), M15, alış ve satış.

Aralık = açılıştan sonraki ilk `aralik_mum` M15 mumu. Tetik = açılıştan sonraki `pencere_dk` dakika içinde aralığın
dışında KAPANAN ilk mum; giriş bir sonraki mumun açılışı. Sembol başına günde tek işlem. Stop: aralığın karşı
kenarı. O1: hedef yok; O2: hedef tp_r. Seans sonunda (son mumun kapanışı) çıkılır.
Seans saatleri yerel saatten (yaz/kış saati dahil) hesaplanır; veri zamanı UTC'dir.
"""
from types import SimpleNamespace

import numpy as np
import pandas as pd

from .ortak import SONSUZ, Seri, emirler

# grup -> (saat dilimi, açılış dakikası, seans sonu dakikası)
SEANS = {"londra": ("Europe/London", 8 * 60, 16 * 60 + 30), "newyork": ("America/New_York", 9 * 60 + 30, 15 * 60 + 55)}
GRUP = {"EURUSD": "londra", "GBPUSD": "londra", "EURGBP": "londra", "EURJPY": "londra", "GBPJPY": "londra",
        "GER40": "londra", "GER40B": "londra", "UK100": "londra", "EU50": "londra", "EU50B": "londra",
        "US500": "newyork", "US100": "newyork", "XAUUSD": "newyork"}


def _gunler(s: Seri, prm):
    """Her gün için: aralık (rh, rl), pencere içindeki mum index'leri, seans sonu mumu. Geçerli günler döner."""
    tz, acilis, bitis = SEANS[GRUP[s.ad]]
    yerel = pd.to_datetime(s.t, unit="s", utc=True).tz_convert(tz)
    dk = (yerel.hour * 60 + yerel.minute).to_numpy()
    gun = yerel.normalize().tz_localize(None).to_numpy().astype("datetime64[D]").astype(np.int64)
    n_ar = int(prm["aralik_mum"])
    out = []
    sinir = np.flatnonzero(np.r_[True, gun[1:] != gun[:-1], True])
    for a, b in zip(sinir[:-1], sinir[1:]):
        d = dk[a:b]
        ar = a + np.flatnonzero((d >= acilis) & (d < acilis + 15 * n_ar))
        if len(ar) != n_ar:                                   # aralık mumlarından biri eksik: o gün işlem yok
            continue
        pen = a + np.flatnonzero((d >= acilis + 15 * n_ar) & (d + 15 <= acilis + prm["pencere_dk"]))
        son = a + np.flatnonzero(d + 15 <= bitis)
        if not len(pen) or not len(son):
            continue
        out.append((float(s.h[ar].max()), float(s.l[ar].min()), pen, int(son[-1])))
    return out


def _emir(s: Seri, satirlar, prm):
    """satirlar: (i, yon, rh, rl, kson). Stop: alışta aralık dibi, satışta aralık tepesi + giriş mumundaki spread."""
    if not satirlar:
        return emirler([], [], [])
    i, yon, rh, rl, kson = (np.array(x) for x in zip(*satirlar))
    i = i.astype(np.int64)
    j = np.minimum(i + 1, s.n - 1)
    slf = np.where(yon > 0, rl, rh + s.sp[j])
    genislik = rh - rl
    with np.errstate(invalid="ignore", divide="ignore"):
        skor = np.where(yon > 0, s.c[i] - rh, rl - s.c[i]) / genislik
    tp = prm.get("tp_r")
    return emirler(i, yon, kson, skor, sl_fiyat=slf, tp_r=SONSUZ if tp is None else tp)


def _sinyal(s: Seri, prm):
    satirlar = []
    for rh, rl, pen, kson in _gunler(s, prm):
        c = s.c[pen]
        disari = np.flatnonzero((c > rh) | (c < rl))
        if not len(disari):
            continue
        i = int(pen[disari[0]])
        if i + 1 > kson:
            continue
        satirlar.append((i, 1 if s.c[i] > rh else -1, rh, rl, kson))
    return _emir(s, satirlar, prm)


def _havuz(s: Seri, prm):
    """Giriş penceresindeki HER mumda, iki yönde giriş (aynı stop ve çıkış kuralları)."""
    satirlar = []
    for rh, rl, pen, kson in _gunler(s, prm):
        for i in pen:
            if i + 1 <= kson:
                satirlar.append((int(i), 1, rh, rl, kson))
                satirlar.append((int(i), -1, rh, rl, kson))
    return _emir(s, satirlar, prm)


def _evren(s: Seri) -> bool:
    return s.ad in GRUP


O1 = SimpleNamespace(ZD="M15", PRM={"aralik_mum": 2, "pencere_dk": 180, "tp_r": None},
                     KOMSU={"aralik_mum": [1, 3], "pencere_dk": [150, 210]}, evren=_evren, sinyal=_sinyal, havuz=_havuz)
O2 = SimpleNamespace(ZD="M15", PRM={"aralik_mum": 2, "pencere_dk": 180, "tp_r": 1.0},
                     KOMSU={"aralik_mum": [1, 3], "pencere_dk": [150, 210], "tp_r": [0.8, 1.2]},
                     evren=_evren, sinyal=_sinyal, havuz=_havuz)
