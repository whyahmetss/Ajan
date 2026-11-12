"""ORB araştırması için M15 verisi (docs/ARASTIRMA_RAPORU_2.md, Bölüm 4.5).

Kaynaklar: FXCM m1 mum arşivi (forex; alış+satış -> spread GERÇEK), HistData M1 (endeks/altın; spread MODEL,
veri_topla.py ile aynı formül). Zaman UTC olarak saklanır (seans saatleri yerel saatten hesaplanır).

KİLİT: kilit sınırından sonraki mumlar AYRI klasöre (veri_m15/kilitli/) yazılır. Geliştirme sırasında
hiçbir kod o klasörü okumaz; sadece deney2 --kilitli okur.

Kullanım:  python -m arastirma.veri_m15 [--sadece fx|emtia]
"""
import argparse
import gzip
import io
import json
import re
import sys
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import numpy as np
import pandas as pd

from arastirma.veri_topla import (AKTIF_UTC, BOLUMLER, EMTIA_ENDEKS, FX_FINANSMAN, FX_TICK_USD, HAM, KOK, PASIF_CARPAN,
                                  _bilgi, _indir, _oturum)

CIKTI = KOK / "veri_m15"
FX_M15 = ["EURUSD", "GBPUSD", "EURGBP", "EURJPY", "GBPJPY"]
HD_M15 = ["SPXUSD", "NSXUSD", "XAUUSD", "GRXEUR", "UKXGBP", "ETXEUR"]
KOL = ["time", "open", "high", "low", "close", "tick_volume", "spread"]


def kilit_utc() -> int:
    """Kilit sınırı (sunucu saati kodlu) -> UTC. Sınır cumartesi 00:00 sunucu saati; 3 saat pay yeterli."""
    k = json.loads((KOK / "kayitlar" / "kilit.json").read_text(encoding="utf-8"))
    return int(k["kilit_baslangic"]) - 3 * 3600


def _m15(df: pd.DataFrame) -> pd.DataFrame:
    """df: time (UTC sn, M1), open, high, low, close, spread (fiyat). -> M15 (spread: mum içi ortalama)."""
    g = df.assign(g=df["time"] // 900 * 900).groupby("g")
    return g.agg(open=("open", "first"), high=("high", "max"), low=("low", "min"), close=("close", "last"),
                 tick_volume=("open", "size"), spread=("spread", "mean")).reset_index().rename(columns={"g": "time"})


def _yaz(ad: str, m15: pd.DataFrame, point: float) -> dict:
    m15 = m15.sort_values("time").drop_duplicates("time").reset_index(drop=True)
    m15 = m15[(m15.high >= m15.low) & (m15.open > 0)].copy()
    m15["spread"] = (m15["spread"] / point).round().astype(int).clip(lower=0)
    sinir = kilit_utc()
    (CIKTI / "kilitli").mkdir(parents=True, exist_ok=True)
    on, son = m15[m15.time < sinir], m15[m15.time >= sinir]
    on[KOL].to_csv(CIKTI / f"{ad}_M15.csv.gz", index=False)
    son[KOL].to_csv(CIKTI / "kilitli" / f"{ad}_M15.csv.gz", index=False)
    return {"dosya": f"{ad}_M15.csv.gz", "mum": int(len(on)), "kilitli_mum": int(len(son)),
            "ilk": str(pd.to_datetime(int(m15.time.iloc[0]), unit="s").date())}


def fx(bas_yil: int, bit_yil: int) -> list[dict]:
    meta = []
    for p in FX_M15:
        onbellek = HAM / "fxcm_m15" / f"{p}.csv.gz"
        if not onbellek.exists():
            isler = [(y, w) for y in range(bas_yil, bit_yil + 1) for w in range(1, 54)]

            def tek(x, p=p):
                y, w = x
                hedef = HAM / "fxcm_m1" / p / f"{y}_{w}.csv.gz"
                if not _indir(f"https://candledata.fxcorporate.com/m1/{p}/{y}/{w}.csv.gz", hedef):
                    return None
                try:
                    ham = gzip.decompress(hedef.read_bytes())
                    metin = ham.decode("utf-16") if ham[:2] in (b"\xff\xfe", b"\xfe\xff") else ham.decode("utf-8", "replace")
                    d = pd.read_csv(io.StringIO(metin)).dropna()
                    t = pd.to_datetime(d["DateTime"], format="%m/%d/%Y %H:%M:%S.%f", utc=True)
                    m1 = pd.DataFrame({"time": (t - pd.Timestamp("1970-01-01", tz="UTC")) // pd.Timedelta(seconds=1),
                                       "open": d["BidOpen"], "high": d["BidHigh"], "low": d["BidLow"], "close": d["BidClose"],
                                       "spread": d["AskOpen"] - d["BidOpen"]})
                    return _m15(m1)
                except Exception as ex:
                    print(f"  {p} {y}/{w}: {type(ex).__name__} {ex}", flush=True)
                    return None
            with ThreadPoolExecutor(6) as ex:
                parcalar = [x for x in ex.map(tek, isler) if x is not None and len(x)]
            if not parcalar:
                print(f"  {p}: VERİ YOK", flush=True)
                continue
            tum = pd.concat(parcalar, ignore_index=True)
            tum = tum.groupby("time").agg(open=("open", "first"), high=("high", "max"), low=("low", "min"),
                                          close=("close", "last"), tick_volume=("tick_volume", "sum"),
                                          spread=("spread", "mean")).reset_index()      # hafta sınırında bölünen mum
            onbellek.parent.mkdir(parents=True, exist_ok=True)
            tum.to_csv(onbellek, index=False)
        tum = pd.read_csv(onbellek)
        point, digits = (0.001, 3) if p.endswith("JPY") else (0.00001, 5)
        d = _yaz(p, tum, point)
        meta.append({"ad": p, "sinif": "forex", "kaynak": "FXCM m1 -> M15 (spread gerçek, ham/ECN)",
                     "bilgi": _bilgi(point, digits, FX_TICK_USD[p[3:6]], 0.01, FX_FINANSMAN, 3, 100000.0, 0.0), **d})
        print(f"  {p}: {d}", flush=True)
    return meta


def _hd_donem(sembol: str, yil: int, ay: int | None) -> pd.DataFrame | None:
    ad = f"{sembol}_{yil}" + (f"{ay:02d}" if ay else "")
    onbellek = HAM / "histdata_m15" / f"{ad}.csv.gz"
    yok = HAM / "histdata_m15" / f"{ad}.yok"
    if onbellek.exists():
        return pd.read_csv(onbellek)
    if yok.exists():
        return None
    onbellek.parent.mkdir(parents=True, exist_ok=True)
    sayfa = (f"https://www.histdata.com/download-free-forex-historical-data/?/metatrader/1-minute-bar-quotes/"
             f"{sembol.lower()}/{yil}" + (f"/{ay}" if ay else ""))
    s = _oturum()
    for _ in range(3):
        try:
            time.sleep(1.0)
            r = s.get(sayfa, timeout=60)
            m = re.search(r'id="tk" value="([0-9a-f]+)"', r.text)
            if r.status_code != 200 or not m:
                time.sleep(10)
                continue
            veri = {"tk": m.group(1), "date": str(yil), "datemonth": f"{yil}{ay:02d}" if ay else str(yil),
                    "platform": "MT", "timeframe": "M1", "fxpair": sembol}
            time.sleep(1.0)
            z = s.post("https://www.histdata.com/get.php", data=veri, headers={"Referer": sayfa}, timeout=300)
            if z.status_code != 200 or len(z.content) < 1000:
                time.sleep(10)
                continue
            with zipfile.ZipFile(io.BytesIO(z.content)) as zf:
                isim = next(n for n in zf.namelist() if n.lower().endswith(".csv"))
                m1 = pd.read_csv(zf.open(isim), header=None, names=["d", "s", "open", "high", "low", "close", "v"])
            t = pd.to_datetime(m1["d"] + " " + m1["s"], format="%Y.%m.%d %H:%M") + pd.Timedelta(hours=5)   # EST sabit -> UTC
            m1["time"] = (t - pd.Timestamp("1970-01-01")) // pd.Timedelta(seconds=1)
            m1["spread"] = 0.0
            out = _m15(m1)
            out.to_csv(onbellek, index=False)
            return out
        except Exception as ex:
            print(f"  {ad}: {type(ex).__name__} {ex}", flush=True)
            time.sleep(10)
    yok.touch()
    return None


def emtia(bas_yil: int, bit_yil: int, bit_ay: int) -> list[dict]:
    meta = []
    for sembol in HD_M15:
        parcalar = []
        for yil in range(bas_yil, bit_yil + 1):
            if yil < bit_yil:
                parcalar.append(_hd_donem(sembol, yil, None))
            else:
                parcalar.extend(_hd_donem(sembol, yil, ay) for ay in range(1, bit_ay + 1))
        parcalar = [p for p in parcalar if p is not None and len(p)]
        if not parcalar:
            print(f"  {sembol}: VERİ YOK", flush=True)
            continue
        tum = pd.concat(parcalar, ignore_index=True)
        for bolum_ad, bas, bit, prm_kaynak in BOLUMLER.get(sembol, [(EMTIA_ENDEKS[sembol][0], None, None, sembol)]):
            _, sinif, point, digits, sp_yuzde, sp_taban, tick_usd, min_lot, fin, uc_kat = EMTIA_ENDEKS[prm_kaynak]
            d = tum
            if bas:
                d = d[d["time"] >= pd.Timestamp(bas).timestamp()]
            if bit:
                d = d[d["time"] < pd.Timestamp(bit).timestamp()]
            d = d.copy()
            saat = (d["time"] // 3600) % 24
            baz = np.maximum(sp_taban, d["open"] * sp_yuzde / 100)
            d["spread"] = np.where((saat >= AKTIF_UTC[0]) & (saat < AKTIF_UTC[1]), baz, baz * PASIF_CARPAN)
            if not len(d):
                continue
            bilgi = _yaz(bolum_ad, d, point)
            meta.append({"ad": bolum_ad, "sinif": sinif, "kaynak": f"HistData {sembol} M1 -> M15 (spread MODEL)",
                         "bilgi": _bilgi(point, digits, tick_usd, min_lot, fin, uc_kat), **bilgi})
            print(f"  {bolum_ad}: {bilgi}", flush=True)
    return meta


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--bas", type=int, default=2015)
    ap.add_argument("--sadece", choices=["fx", "emtia"], action="append")
    a = ap.parse_args()
    simdi = datetime.now(timezone.utc)
    bit_yil, bit_ay = (simdi.year, simdi.month - 1) if simdi.month > 1 else (simdi.year - 1, 12)
    dosya = CIKTI / "semboller.json"
    meta = {m["ad"]: m for m in (json.loads(dosya.read_text(encoding="utf-8"))["semboller"] if dosya.exists() else [])}
    secim = a.sadece or ["fx", "emtia"]
    if "fx" in secim:
        meta.update({m["ad"]: m for m in fx(a.bas, simdi.year)})
    if "emtia" in secim:
        meta.update({m["ad"]: m for m in emtia(a.bas, bit_yil, bit_ay)})
    CIKTI.mkdir(parents=True, exist_ok=True)
    dosya.write_text(json.dumps({"indirme_utc": simdi.isoformat(timespec="seconds"), "zaman": "UTC",
                                 "kilit_utc": kilit_utc(), "semboller": list(meta.values())},
                                ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"Tamam: {len(meta)} sembol -> {CIKTI}", flush=True)


if __name__ == "__main__":
    sys.exit(main())
