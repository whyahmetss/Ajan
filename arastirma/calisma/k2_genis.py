"""K2'nin hiç bakılmamış 12 coinde doğrulanması (docs/ARASTIRMA_RAPORU_2.md Bölüm 8; ön kayıtlı, tek koşu).

Kullanım:  python -m arastirma.calisma.k2_genis
"""
import sys
import zipfile
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import numpy as np
import pandas as pd

from arastirma import deney2, motor
from arastirma.motor import Maliyet
from arastirma.stratejiler import ADAYLAR, ortak
from arastirma.veri_topla import HAM, KRIPTO_FINANSMAN, _bilgi, _indir, sunucu_zamani
from bot import backtest

YENI = ["BCH", "LINK", "DOT", "AVAX", "MATIC", "TRX", "XLM", "EOS", "ETC", "UNI", "ATOM", "NEAR"]
SPREAD_YUZDE = 0.30


def indir(coin: str) -> pd.DataFrame:
    s = f"{coin}USDT"
    simdi = datetime.now(timezone.utc)
    isler = [(y, m) for y in range(2017, simdi.year + 1) for m in range(1, 13) if (y, m) < (simdi.year, simdi.month)]
    with ThreadPoolExecutor(4) as ex:
        list(ex.map(lambda x: _indir(f"https://data.binance.vision/data/spot/monthly/klines/{s}/1h/{s}-1h-{x[0]}-{x[1]:02d}.zip",
                                     HAM / "binance" / s / f"{x[0]}-{x[1]:02d}.zip"), isler))
    parcalar = []
    for f in sorted((HAM / "binance" / s).glob("*.zip")):
        with zipfile.ZipFile(f) as zf:
            d = pd.read_csv(zf.open(zf.namelist()[0]), header=None, usecols=[0, 1, 2, 3, 4],
                            names=["t", "open", "high", "low", "close"])
        d = d[pd.to_numeric(d["t"], errors="coerce").notna()].astype({"t": "int64"})
        d["time"] = np.where(d["t"] > 10 ** 14, d["t"] // 10 ** 6, d["t"] // 1000)
        parcalar.append(d)
    d = pd.concat(parcalar, ignore_index=True).astype({"open": float, "high": float, "low": float, "close": float})
    d = d.sort_values("time").drop_duplicates("time")
    d["time"] = sunucu_zamani(d["time"].to_numpy(np.int64))
    return d.drop_duplicates("time", keep="first").reset_index(drop=True)


def seri(coin: str, isinma: int):
    d = indir(coin)
    fiyat = float(d["close"].median())
    digits = 2 if fiyat >= 5 else 5
    point = 10.0 ** -digits
    d["spread"] = (d["open"] * SPREAD_YUZDE / 100 / point).round().clip(lower=1)
    return ortak.seri_kur(f"{coin}USD", "kripto", _bilgi(point, digits, point, 1.0, KRIPTO_FINANSMAN, 3), d, 3600, isinma)


def main():
    cfg = deney2.cfg_yukle()
    dn = deney2.donemler(cfg)
    isinma = int(cfg["backtest"]["isinma_bar"])
    yeni = [seri(c, isinma) for c in YENI]
    for s in yeni:
        print(f"  {s.ad}: {pd.to_datetime(int(s.t[0]), unit='s').date()} → {pd.to_datetime(int(s.t[-1]), unit='s').date()}  {s.n} mum")
    mod = ADAYLAR["K2"][1]
    T0 = min(int(s.t[0]) for s in yeni)
    kes = {"kilit öncesi": backtest.Kesim("a", T0, dn["KILITLI"].bas, dn["KILITLI"].bas),
           "kilit sonrası": backtest.Kesim("b", dn["KILITLI"].bas, dn["KILITLI"].bit, dn["KILITLI"].veri_bit),
           "bütün dönem": backtest.Kesim("c", T0, dn["KILITLI"].bit, dn["KILITLI"].veri_bit)}
    print("\n1) Sembol başına tek pozisyon (12 yeni coin), maliyet + temel kayma dahil:")
    satir, sonuc = [], {}
    for ad, k in kes.items():
        df, hv = deney2.islemler(mod, yeni, mod.PRM, k, cfg, havuz=True)
        sonuc[ad] = df
        alt, ust = motor.blok_guven_araligi(df)
        pm = motor.permutasyon(df, hv, n=5000)
        satir.append({"donem": ad, **motor.ozet(df), "p_rastgele": pm["p"], "rastgele_ort_R": pm["rastgele_ort_R"],
                      "ort_R_%95": f"[{alt:+.3f}, {ust:+.3f}]"})
    print(deney2.tablo(satir))
    print("\n2) Coin coin (bütün dönem):")
    print(deney2.tablo([{"sembol": sn, **motor.ozet(g)} for sn, g in sonuc["bütün dönem"].groupby("sembol")]))
    print("\n3) Yıl yıl (bütün dönem):")
    print(motor.yil_yil(sonuc["bütün dönem"]).to_string(index=False))
    print("\n4) Maliyet stresi (bütün dönem):")
    satir = []
    for etiket, m, kay, kay_atr in deney2.STRES[:3] + deney2.STRES[4:5]:
        satir.append({"senaryo": etiket, **motor.ozet(deney2.islemler(mod, yeni, mod.PRM, kes["bütün dönem"], cfg, mal=m, kay=kay, kay_atr=kay_atr))})
    satir.append({"senaryo": "spread x2 (kayma 0.25 sp)",
                  **motor.ozet(deney2.islemler(mod, yeni, mod.PRM, kes["bütün dönem"], cfg, mal=Maliyet(spread_carpani=2.0)))})
    print(deney2.tablo(satir))
    print("\n5) Portföy kurallarıyla (en fazla 2 kripto pozisyonu): ilk 8 coin / 12 yeni coin / 20 coin birlikte")
    eski = [s for s in deney2.yukle("H1", cfg) if s.sinif == "kripto"]
    satir = []
    for ad, k in kes.items():
        for etiket, ev in (("ilk 8", eski), ("12 yeni", yeni), ("20 birlikte", eski + yeni)):
            d = deney2.islemler(mod, ev, mod.PRM, k, cfg, portfoy=True)
            o = motor.ozet(d)
            satir.append({"donem": ad, "evren": etiket, **o, "yillik_%": deney2._yillik(o, k)})
    print(deney2.tablo(satir))
    tum, per = sonuc["bütün dönem"], satir
    pozitif = int((tum.groupby("sembol")["R"].mean() > 0).sum())
    print(f"\nÖlçüt 4: bütün dönem ort R > 0 olan coin sayısı = {pozitif} / {tum['sembol'].nunique()}")


if __name__ == "__main__":
    sys.exit(main())
