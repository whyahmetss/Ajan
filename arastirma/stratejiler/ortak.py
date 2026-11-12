"""Aday stratejiler için ortak altyapı: veri serisi, esnek işlem simülatörü, uygun mum maskesi.

Bu paket SADECE araştırma içindir; bot/ altındaki hiçbir dosya bunu kullanmaz.

Dolum kuralları arastirma/motor.py:_sim ile aynıdır (kayma = 0 iken K2 için birebir aynı sonuç verir;
tests/test_stratejiler.py doğrular). Eklenenler:
  - kayma: piyasa emirlerinde (giriş, stop, zaman/sinyal çıkışı) aleyhte kayma = kay x spread (+ stopta kay_atr x ATR)
  - mutlak stop fiyatı (ORB: aralığın karşı kenarı), işlem başına zorunlu çıkış mumu (seans sonu / zaman stopu)
  - sinyalle çıkış (ters sinyal), hareketli hedef (ör. SMA20), günlük kapanışa göre takip eden stop

Gelecek verisi kuralı: sinyal i. mumun KAPANIŞINDA bilinen değerlerden hesaplanır, giriş i+1. mumun
açılışındadır. Mum başına verilen diziler (hedef, iz_ref, iz_mes) k. mumun AÇILIŞINDA bilinen değerler olmalıdır.
"""
from dataclasses import dataclass, field

import numpy as np
import pandas as pd
from numba import njit

from arastirma import motor
from bot import indikator

SL, TP, TRAIL, SON, ZAMAN, SINYAL = 0, 1, 3, 4, 5, 6
KOD_AD = {SL: "SL", TP: "TP", TRAIL: "TRAIL", SON: "SON", ZAMAN: "ZAMAN", SINYAL: "SINYAL"}
SONSUZ = 1e18


@dataclass
class Seri:
    """Tek sembol, tek zaman dilimi. f: mum başına diziler (t, o, h, l, c, sp, atr, ardisik, ...)."""
    ad: str
    sinif: str
    bilgi: dict
    f: dict
    periyot: int
    isinma: int
    hafta_sonu: bool = False

    def __getattr__(self, k):
        if k == "f" or k.startswith("__"):
            raise AttributeError(k)
        try:
            return self.f[k]
        except KeyError:
            raise AttributeError(k) from None

    @property
    def n(self) -> int:
        return len(self.f["t"])


def seri_kur(ad, sinif, bilgi, df: pd.DataFrame, periyot: int, isinma: int, gunluk: bool = True) -> Seri:
    """df: time, open, high, low, close, spread(point) [, hacim]. Bütün göstergeler geriye bakar (nedensel)."""
    df = df.sort_values("time").drop_duplicates("time").reset_index(drop=True)
    d = indikator.ekle(df)
    t = df["time"].to_numpy(np.int64)
    f = {"t": t, "o": d["open"].to_numpy(float), "h": d["high"].to_numpy(float), "l": d["low"].to_numpy(float),
         "c": d["close"].to_numpy(float), "sp": d["spread"].to_numpy(float) * bilgi["point"],
         "atr": d["atr"].to_numpy(float), "adx": d["adx"].to_numpy(float), "rsi": d["rsi"].to_numpy(float)}
    ardisik = np.zeros(len(t), bool)
    ardisik[:-1] = (t[1:] - t[:-1]) == periyot
    f["ardisik"] = ardisik
    if gunluk:
        f.update(motor._gunluk(t, f["o"], f["h"], f["l"], f["c"]))
    if "hacim" in df:
        f["hacim"] = df["hacim"].to_numpy(float)
    cumartesi = (((t // 86400) + 3) % 7) == 5
    return Seri(ad, sinif, bilgi, f, periyot, isinma, bool(cumartesi.mean() > 0.01))


def uygun(s: Seri, bas: int, bit: int, veri_bit: int, max_spread_atr: float, spread_carpani: float = 1.0) -> tuple:
    """Sinyal mumu olabilecekler: ısınma sonrası, sonraki mum ardışık, giriş [bas, bit) içinde, spread makul."""
    f = s.f
    son = int(np.searchsorted(f["t"], veri_bit, side="left")) - 1
    n = s.n
    j = np.minimum(np.arange(n) + 1, n - 1)
    with np.errstate(invalid="ignore", divide="ignore"):
        m = (f["ardisik"] & (np.arange(n) >= s.isinma) & (np.arange(n) + 1 <= son)
             & (f["t"][j] >= bas) & (f["t"][j] < bit) & (f["atr"] > 0)
             & (f["sp"][j] * spread_carpani / f["atr"] <= max_spread_atr))
    return m, son


@dataclass
class Emirler:
    """Bir stratejinin aday işlemleri. i: sinyal mumu (giriş i+1 açılışı). Stop: sl_fiyat (mutlak) ya da
    sl_mesafe (girişten uzaklık); biri NaN olmalı. tp_r: hedef (R), SONSUZ = yok. kson: en geç bu mumun
    kapanışında çık. Mum başına isteğe bağlı diziler `dizi` içinde: cik_al, cik_sat (bool), hedef, iz_ref, iz_mes."""
    i: np.ndarray
    yon: np.ndarray
    sl_mesafe: np.ndarray
    sl_fiyat: np.ndarray
    tp_r: np.ndarray
    kson: np.ndarray
    skor: np.ndarray
    dizi: dict = field(default_factory=dict)

    def sec(self, maske) -> "Emirler":
        return Emirler(self.i[maske], self.yon[maske], self.sl_mesafe[maske], self.sl_fiyat[maske], self.tp_r[maske],
                       self.kson[maske], self.skor[maske], self.dizi)

    def __len__(self):
        return len(self.i)


def emirler(i, yon, kson, skor=None, sl_mesafe=None, sl_fiyat=None, tp_r=None, **dizi) -> Emirler:
    i = np.asarray(i, np.int64)
    n = len(i)
    nan = np.full(n, np.nan)
    return Emirler(i, np.asarray(yon, np.int8), nan if sl_mesafe is None else np.asarray(sl_mesafe, float),
                   nan if sl_fiyat is None else np.asarray(sl_fiyat, float),
                   np.full(n, SONSUZ) if tp_r is None else np.broadcast_to(np.asarray(tp_r, float), (n,)).copy(),
                   np.asarray(kson, np.int64), np.zeros(n) if skor is None else np.asarray(skor, float), dizi)


@njit(cache=True)
def _sim(o, h, l, c, sp, atr, idx, yon, mes, slf, tp_r, kson, cik_al, cik_sat, cik_var, hedef, hedef_var,
         iz_ref, iz_mes, iz_var, son, taban, kay, kay_atr):
    n = len(idx)
    R = np.full(n, np.nan)
    K = np.full(n, -1, np.int64)
    KOD = np.full(n, -1, np.int8)
    M = np.zeros(n)
    G = np.zeros(n)
    for a in range(n):
        i = idx[a]
        j = i + 1
        ks = min(kson[a], son)
        if j > son or ks < j:
            continue
        al = yon[a] > 0
        spj = sp[j]
        ref = o[j] + spj if al else o[j]
        if slf[a] == slf[a]:
            m = (ref - slf[a]) if al else (slf[a] - ref)
        else:
            m = max(mes[a], taban + spj)
        if not (m > 0.0) or spj > 0.25 * m:
            continue
        giris = ref + kay * spj if al else ref - kay * spj
        sl = ref - m if al else ref + m
        tp = ref + tp_r[a] * m if al else ref - tp_r[a] * m
        tp_var = tp_r[a] < 1e17
        tepe = iz_ref[j] if iz_var else 0.0
        kod = SL
        kc = -1
        cikis = 0.0
        for k in range(j, ks + 1):
            spk = sp[k]
            kayma = kay * spk + kay_atr * atr[k]
            if iz_var and k > j and iz_ref[k] == iz_ref[k] and iz_mes[k] == iz_mes[k]:
                if al:
                    if iz_ref[k] > tepe:
                        tepe = iz_ref[k]
                    yeni = tepe - iz_mes[k]
                    if yeni > sl:
                        sl = yeni; kod = TRAIL
                else:
                    if iz_ref[k] < tepe:
                        tepe = iz_ref[k]
                    yeni = tepe + iz_mes[k] + spk
                    if yeni < sl:
                        sl = yeni; kod = TRAIL
            hd = hedef[k] if hedef_var else np.nan
            if al:
                if k > j and o[k] <= sl:
                    cikis = o[k] - kayma; kc = k; break
                if l[k] <= sl:
                    cikis = sl - kayma; kc = k; break
                if tp_var:
                    if k > j and o[k] >= tp:
                        cikis = o[k]; kc = k; kod = TP; break
                    if h[k] >= tp:
                        cikis = tp; kc = k; kod = TP; break
                if hd == hd:
                    if o[k] >= hd:
                        cikis = o[k]; kc = k; kod = TP; break
                    if h[k] >= hd:
                        cikis = hd; kc = k; kod = TP; break
            else:
                ao = o[k] + spk
                if k > j and ao >= sl:
                    cikis = ao + kayma; kc = k; break
                if h[k] + spk >= sl:
                    cikis = sl + kayma; kc = k; break
                if tp_var:
                    if k > j and ao <= tp:
                        cikis = ao; kc = k; kod = TP; break
                    if l[k] + spk <= tp:
                        cikis = tp; kc = k; kod = TP; break
                if hd == hd:
                    if ao <= hd:
                        cikis = ao; kc = k; kod = TP; break
                    if l[k] + spk <= hd:
                        cikis = hd; kc = k; kod = TP; break
            sinyal = cik_var and ((al and cik_al[k]) or ((not al) and cik_sat[k]))
            if k == kson[a] or sinyal:
                cikis = c[k] - kay * spk if al else c[k] + spk + kay * spk
                kc = k
                kod = ZAMAN if k == kson[a] else SINYAL
                break
        if kc < 0:
            kc = ks
            kod = SON
            cikis = c[ks] if al else c[ks] + sp[ks]
        R[a] = ((cikis - giris) if al else (giris - cikis)) / m
        K[a] = kc
        KOD[a] = kod
        M[a] = m
        G[a] = giris
    return R, K, KOD, M, G


def simule(s: Seri, e: Emirler, mal: motor.Maliyet, son: int, kay: float = 0.0, kay_atr: float = 0.0) -> pd.DataFrame:
    """Her aday için bağımsız işlem sonucu (çakışma ayıklanmaz). Çıktı sütunları motor.simule ile uyumludur."""
    f, b = s.f, s.bilgi
    sp = f["sp"] * mal.spread_carpani
    taban = ((b.get("trade_stops_level") or 0) + 5) * b["point"]
    bos_b, bos_f = np.zeros(1, np.bool_), np.zeros(1)
    dz = e.dizi
    R, K, KOD, M, G = _sim(
        f["o"], f["h"], f["l"], f["c"], sp, f["atr"], e.i, e.yon, e.sl_mesafe, e.sl_fiyat, e.tp_r, e.kson,
        dz.get("cik_al", bos_b), dz.get("cik_sat", bos_b), "cik_al" in dz, dz.get("hedef", bos_f), "hedef" in dz,
        dz.get("iz_ref", bos_f), dz.get("iz_mes", bos_f), "iz_ref" in dz, son, taban, kay, kay_atr)
    ok = K >= 0
    idx, yon, R, K, KOD, M, G, skor = e.i[ok], e.yon[ok], R[ok], K[ok], KOD[ok], M[ok], G[ok], e.skor[ok]
    giris_t, cikis_t = f["t"][idx + 1], f["t"][K]
    maliyet = ((b.get("komisyon_yuzde") or 0.0) * mal.komisyon_carpani + mal.komisyon_yuzde) / 100 * G / M
    if mal.swap and b.get("swap_mode") == 5:
        oran = np.where(yon > 0, b.get("swap_long") or 0.0, b.get("swap_short") or 0.0)
        gece = motor._geceler(giris_t, cikis_t, int(b.get("swap_rollover3days", 3)), s.hafta_sonu)
        maliyet = maliyet - gece * oran * mal.swap_carpani / 100 / 360 * G / M
    return pd.DataFrame({"sembol": s.ad, "sinif": s.sinif, "i": idx, "k": K, "yon": yon, "giris_t": giris_t,
                         "cikis_t": cikis_t, "kod": KOD, "be_T": -1, "R_brut": R, "R": R - maliyet,
                         "maliyet_R": maliyet, "risk_yuzde": 0.5, "skor": skor, "G": G, "M": M,
                         "periyot": s.periyot})


def tek(s: Seri, e: Emirler, mal, son, kay=0.0, kay_atr=0.0) -> pd.DataFrame:
    """Sembol tek başına, aynı anda tek pozisyon."""
    df = simule(s, e, mal, son, kay, kay_atr)
    if df.empty:
        return df
    return df[motor._cakismasiz(df["i"].to_numpy(np.int64), df["k"].to_numpy(np.int64))].reset_index(drop=True)
