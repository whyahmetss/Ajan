"""Olay incelemesi (KEŞİF, sadece EĞİTİM dönemi): belirli koşullardan sonraki getiri, koşulsuz getiriden
farklı mı? Çıkış kuralından bağımsızdır; amaç veride öngörülebilir yapı olup olmadığını görmektir.

Getiri: sonraki mumun açılışından h mum sonraki açılışa, günlük ATR birimiyle (varlıklar arası
karşılaştırılabilir). t istatistiği haftalık kümelenmiş (aynı haftadaki gözlemler bağımsız sayılmaz).

Kullanım:  python -m arastirma.olay
"""
import sys

import numpy as np
import pandas as pd

from arastirma import deney

UFUKLAR = (4, 24, 72, 120)


def ileri_getiri(sem, h: int) -> np.ndarray:
    """i. mumun kapanışında karar -> (o[i+1+h] - o[i+1]) / günlük ATR. Veri yoksa NaN."""
    o, n = sem.o, sem.n
    r = np.full(n, np.nan)
    j = np.arange(n - h - 1) + 1
    with np.errstate(invalid="ignore", divide="ignore"):
        r[: n - h - 1] = (o[j + h] - o[j]) / sem.d_atr[: n - h - 1]
    return r


def kume_t(x: np.ndarray, hafta: np.ndarray) -> tuple:
    """(ortalama, haftalık kümelenmiş t istatistiği, gözlem sayısı)"""
    ok = ~np.isnan(x)
    x, hafta = x[ok], hafta[ok]
    if len(x) < 30:
        return np.nan, np.nan, len(x)
    ort = x.mean()
    kod, _ = pd.factorize(hafta)
    kume = np.bincount(kod, weights=x - ort)
    se = np.sqrt((kume ** 2).sum()) / len(x)
    return float(ort), float(ort / se) if se > 0 else np.nan, int(len(x))


def kosullar(sem) -> dict:
    """Her koşul: (maske, yön). Hepsi i. mumun kapanışında bilinen bilgilerle."""
    with np.errstate(invalid="ignore"):
        yukari, asagi = sem.d_c > sem.d_sma200, sem.d_c < sem.d_sma200
        gun_ilk = sem.gun_ilk
        return {
            "trend yukarı (D1 > SMA200), günün ilk mumu": (gun_ilk & yukari, 1),
            "trend aşağı (D1 < SMA200), günün ilk mumu [satış]": (gun_ilk & asagi, -1),
            "günlük dip: RSI2<10 & trend yukarı": (gun_ilk & yukari & (sem.d_rsi2 < 10), 1),
            "günlük dip: RSI2<10 (trend filtresi yok)": (gun_ilk & (sem.d_rsi2 < 10), 1),
            "günlük tepe: RSI2>90 & trend aşağı [satış]": (gun_ilk & asagi & (sem.d_rsi2 > 90), -1),
            "dün günlük 20 günün zirvesinde kapandı": (gun_ilk & (sem.d_c >= sem.d_maks20), 1),
            "dün günlük 20 günün dibinde kapandı [satış]": (gun_ilk & (sem.d_c <= sem.d_min20), -1),
            "H1 z<=-2 & trend yukarı": (yukari & (sem.z <= -2), 1),
            "H1 z>=+2 & trend yukarı (güç)": (yukari & (sem.z >= 2), 1),
            "mevcut sinyal skor>=60 (kendi yönünde)": (sem.skor0 >= 60, 0),
        }


def main():
    cfg, veriler, dn = deney.hazirla()
    bas, bit = dn["egitim"].bas, dn["egitim"].bit
    print(f"OLAY İNCELEMESİ — sadece eğitim: {pd.to_datetime(bas, unit='s').date()} → {pd.to_datetime(bit, unit='s').date()}")
    print("Getiri birimi: günlük ATR. 'fark' = koşullu − koşulsuz (aynı yön). |t| > 3 ve fark > maliyet aranır.\n")
    for v in veriler:                                   # ek günlük özellikler (dünkü değerler)
        gun = v.t // 86400
        g = pd.DataFrame({"gun": gun, "c": v.c}).groupby("gun")["c"].last()
        pos = np.searchsorted(g.index.to_numpy(), gun) - 1
        for ad, seri in (("d_maks20", g.rolling(20).max()), ("d_min20", g.rolling(20).min())):
            x = seri.to_numpy(float)
            v.f[ad] = np.where(pos >= 0, x[np.clip(pos, 0, None)], np.nan)
    siniflar = sorted({v.sinif for v in veriler})
    satirlar = []
    for sinif in siniflar:
        ev = [v for v in veriler if v.sinif == sinif]
        for h in UFUKLAR:
            havuz = {}                                   # koşul -> (getiriler, haftalar), ve koşulsuz
            for v in ev:
                r = ileri_getiri(v, h)
                don = (v.t >= bas) & (v.t < bit - h * 3600 - 86400) & (np.arange(v.n) >= v.isinma) & v.ardisik
                hafta = v.t // (7 * 86400)
                for ad, (m, yon) in kosullar(v).items():
                    y = v.yon0 if yon == 0 else yon
                    sec = don & m
                    havuz.setdefault(ad, []).append((r[sec] * (y[sec] if yon == 0 else y), hafta[sec]))
                havuz.setdefault("_al", []).append((r[don], hafta[don]))
            taban = np.concatenate([a for a, _ in havuz["_al"]])
            taban_ort = float(np.nanmean(taban))
            for ad, parca in havuz.items():
                if ad == "_al":
                    continue
                x = np.concatenate([a for a, _ in parca])
                hf = np.concatenate([b for _, b in parca])
                ort, t, n = kume_t(x, hf)
                yon_isaret = -1 if "[satış]" in ad else (0 if "kendi yönünde" in ad else 1)
                kosulsuz = taban_ort * yon_isaret
                satirlar.append({"sinif": sinif, "ufuk_mum": h, "kosul": ad, "n": n, "ort": ort, "t": t,
                                 "kosulsuz": kosulsuz, "fark": ort - kosulsuz if n >= 30 else np.nan})
    df = pd.DataFrame(satirlar)
    for sinif in siniflar:
        print(f"--- {sinif} ---")
        p = df[df.sinif == sinif].pivot_table(index="kosul", columns="ufuk_mum", values=["fark", "t"], sort=False)
        n = df[(df.sinif == sinif) & (df.ufuk_mum == UFUKLAR[0])].set_index("kosul")["n"]
        p.insert(0, "n", n)
        print(p.round(3).to_string(), "\n")


if __name__ == "__main__":
    sys.exit(main())
