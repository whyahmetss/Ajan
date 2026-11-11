"""Araştırma verisi: halka açık kaynaklardan GERÇEK piyasa verisi indirir ve botun backtest
biçimine (veri/ klasörü, araclar.py backtest --veri ile aynı) çevirir.

Kaynaklar
  Forex            : FXCM açık mum arşivi (H1, alış + satış fiyatı -> spread GERÇEK)
  Altın/endeks/petrol : HistData.com ücretsiz arşivi (M1, sadece alış fiyatı -> spread MODELLENİR)
  Kripto           : Binance açık arşivi (H1 spot -> spread MODELLENİR, CFD maliyetine göre)

Bu, kullanıcının broker'ının verisi DEĞİLDİR: fiyatlar gerçek piyasa fiyatı ama spread, seans
saatleri ve swap broker'a göre değişir. Son doğrulama broker verisiyle (araclar.py veri_indir)
yapılmalıdır.

Zaman: bütün kaynaklar UTC'ye, oradan tipik MT5 "New York kapanışı" sunucu saatine çevrilir
(UTC+2, ABD yaz saatinde UTC+3). Böylece günlük ve H4 mumlar çoğu MT5 broker'ındaki gibi hizalanır.

Kullanım:  python -m arastirma.veri_topla [--bas 2015] [--sadece fx|emtia|kripto]
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
from pathlib import Path

import numpy as np
import pandas as pd
import requests

KOK = Path(__file__).resolve().parent.parent
HAM = KOK / "veri_ham"
CIKTI = KOK / "veri"

FX = ["EURUSD", "GBPUSD", "USDJPY", "AUDUSD", "USDCAD", "USDCHF", "NZDUSD",
      "EURJPY", "GBPJPY", "EURGBP", "AUDJPY", "EURAUD", "CADJPY", "AUDNZD"]
FX_TICK_USD = {"USD": 1.0, "JPY": 0.67, "CHF": 1.15, "CAD": 0.72, "GBP": 1.27, "AUD": 0.65, "NZD": 0.60}

# HistData sembolü -> (MT5 benzeri ad, sınıf, point, digits, spread: fiyatın yüzdesi, spread tabanı (fiyat),
#                      1 lotta 1 point'in USD değeri, min lot, yıllık finansman %, 3 kat swap günü)
# Spread modeli (aktif saatlerde): max(taban, fiyat x yüzde). 2015-2026 arasında fiyatlar birkaç katına
# çıktığı için sabit spread yerine yüzde kullanılır. Değerler tipik ECN/standart hesap toplam maliyetidir.
EMTIA_ENDEKS = {
    "XAUUSD": ("XAUUSD", "emtia", 0.01, 2, 0.012, 0.20, 1.0, 0.01, -4.0, 3),
    "XAGUSD": ("XAGUSD", "emtia", 0.001, 3, 0.10, 0.015, 5.0, 0.01, -4.0, 3),
    "SPXUSD": ("US500", "endeks", 0.01, 2, 0.012, 0.4, 0.01, 0.1, -5.0, 5),
    "NSXUSD": ("US100", "endeks", 0.01, 2, 0.012, 1.0, 0.01, 0.1, -5.0, 5),
    "GRXEUR": ("GER40", "endeks", 0.01, 2, 0.010, 1.0, 0.011, 0.1, -5.0, 5),
    "ETXEUR": ("EU50", "endeks", 0.01, 2, 0.035, 1.2, 0.011, 0.1, -5.0, 5),
    "UKXGBP": ("UK100", "endeks", 0.01, 2, 0.015, 1.0, 0.0127, 0.1, -5.0, 5),
    "JPXJPY": ("JP225", "endeks", 0.01, 2, 0.030, 7.0, 0.000067, 1.0, -5.0, 5),
}
# VERİ HATASI: HistData'nın GRXEUR (DAX) dosyaları 2020-06-15 → 2023-12-01 arasında başka bir endeksin
# (fiyat seviyesi ve seans saatleri Euro Stoxx 50 ile uyumlu) verisini içeriyor. Bu aralık atılır ve
# iki temiz bölüm ayrı semboller olarak kullanılır (indikatörler boşluğun üzerinden hesaplanmasın).
# WTIUSD (petrol) kullanılmıyor: kaynakta 2024-2025 yılları yok.
# Aradaki bölüm ayrı bir sembol (EU50B) olarak, Euro Stoxx 50 maliyet varsayımlarıyla kullanılır: fiyat
# seviyeleri Euro Stoxx 50'nin bilinen değerleriyle uyumlu (2020-10 dibi ~2916, 2023-12-01 ~4400).
# (ad, başlangıç, bitiş, maliyet parametreleri hangi sembolden)
BOLUMLER = {"GRXEUR": [("GER40", None, "2020-06-13", "GRXEUR"), ("EU50B", "2020-06-14", "2023-12-02", "ETXEUR"),
                       ("GER40B", "2023-12-02", None, "GRXEUR")],
            # ETXEUR dosyası 2018-12-17'den sonra ~8900 seviyesinde başka bir endeks içeriyor: kesilir
            "ETXEUR": [("EU50", None, "2018-12-15", "ETXEUR")]}
AKTIF_UTC = (7, 20)             # bu saatler dışında modellenen spread genişler
PASIF_CARPAN = 2.5

# Binance sembolü -> (ad, point, digits, spread % (CFD), min lot)
KRIPTO = {
    "BTCUSDT": ("BTCUSD", 0.01, 2, 0.03, 0.01), "ETHUSDT": ("ETHUSD", 0.01, 2, 0.05, 0.01),
    "LTCUSDT": ("LTCUSD", 0.01, 2, 0.15, 0.1), "XRPUSDT": ("XRPUSD", 0.00001, 5, 0.15, 10.0),
    "SOLUSDT": ("SOLUSD", 0.01, 2, 0.15, 0.1), "BNBUSDT": ("BNBUSD", 0.01, 2, 0.15, 0.01),
    "ADAUSDT": ("ADAUSD", 0.00001, 5, 0.15, 10.0), "DOGEUSDT": ("DOGEUSD", 0.00001, 5, 0.15, 100.0),
}
KRIPTO_FINANSMAN = -20.0        # yıllık %, her iki yön (CFD)
FX_FINANSMAN = -2.0
FX_KOMISYON = 0.007             # FXCM mum verisi ham (ECN) spread içerir; ECN hesap komisyonu ayrıca eklenir

_yerel = __import__("threading").local()


def _oturum() -> requests.Session:
    if not hasattr(_yerel, "s"):
        _yerel.s = requests.Session()
    return _yerel.s


def _indir(url: str, hedef: Path, deneme: int = 3) -> bool:
    """Dosyayı indirir (varsa atlar). 404 -> boş işaret dosyası (tekrar denenmez)."""
    yok = hedef.with_suffix(hedef.suffix + ".yok")
    if hedef.exists() or yok.exists():
        return hedef.exists()
    hedef.parent.mkdir(parents=True, exist_ok=True)
    for k in range(deneme):
        try:
            r = _oturum().get(url, timeout=60)
            if r.status_code == 404:
                yok.touch()
                return False
            if r.status_code == 200 and r.content:
                hedef.write_bytes(r.content)
                return True
            if r.status_code in (403, 429):
                time.sleep(20 * (k + 1))
        except requests.RequestException:
            time.sleep(3 * (k + 1))
    return False


# ------------------------------------------------------------------ zaman
def sunucu_zamani(utc_sn: np.ndarray) -> np.ndarray:
    """UTC epoch saniye -> MT5 'NY kapanışı' sunucu saati (New York saati + 7 saat), epoch gibi kodlanmış."""
    ny = pd.to_datetime(utc_sn, unit="s", utc=True).tz_convert("America/New_York").tz_localize(None)
    return ((ny - pd.Timestamp("1970-01-01")) // pd.Timedelta(seconds=1)).to_numpy(np.int64) + 7 * 3600


def _kaydet(ad: str, h1: pd.DataFrame) -> dict:
    """h1: time(utc), open, high, low, close, tick_volume, spread(point). Sunucu saatine çevirir, H4 üretir."""
    h1 = h1.sort_values("time").drop_duplicates("time")
    h1 = h1.assign(time=sunucu_zamani(h1["time"].to_numpy(np.int64)))
    h1 = h1.drop_duplicates("time", keep="first").reset_index(drop=True)      # yaz saati geçişindeki çift saat
    h1["spread"] = h1["spread"].round().astype(int).clip(lower=0)
    g = h1.assign(g=h1["time"] // 14400 * 14400).groupby("g")
    h4 = g.agg(open=("open", "first"), high=("high", "max"), low=("low", "min"), close=("close", "last"),
               tick_volume=("tick_volume", "sum"), spread=("spread", "first")).reset_index().rename(columns={"g": "time"})
    kol = ["time", "open", "high", "low", "close", "tick_volume", "spread"]
    CIKTI.mkdir(parents=True, exist_ok=True)
    h1[kol].to_csv(CIKTI / f"{ad}_H1.csv.gz", index=False)
    h4[kol].to_csv(CIKTI / f"{ad}_H4.csv.gz", index=False)
    return {"H1": f"{ad}_H1.csv.gz", "H4": f"{ad}_H4.csv.gz", "mum": int(len(h1)),
            "ilk": str(pd.to_datetime(int(h1.time.iloc[0]), unit="s").date()),
            "son": str(pd.to_datetime(int(h1.time.iloc[-1]), unit="s").date())}


def _bilgi(point, digits, tick_usd, min_lot, finansman, uc_kat, sozlesme=1.0, komisyon_yuzde=0.0) -> dict:
    return {"komisyon_yuzde": komisyon_yuzde, "point": point, "digits": digits, "trade_tick_size": point, "trade_tick_value": tick_usd,
            "trade_tick_value_loss": tick_usd, "trade_contract_size": sozlesme, "volume_min": min_lot,
            "volume_step": min_lot if min_lot < 1 else 1.0, "volume_max": 1000.0, "trade_stops_level": 0,
            "swap_mode": 5, "swap_long": finansman, "swap_short": finansman, "swap_rollover3days": uc_kat}


# ------------------------------------------------------------------ FX (FXCM)
def fx_topla(bas_yil: int, bit_yil: int) -> list[dict]:
    isler = [(p, y, w) for p in FX for y in range(bas_yil, bit_yil + 1) for w in range(1, 54)]

    def tek(x):
        p, y, w = x
        return _indir(f"https://candledata.fxcorporate.com/H1/{p}/{y}/{w}.csv.gz", HAM / "fxcm" / p / f"{y}_{w}.csv.gz")
    with ThreadPoolExecutor(6) as ex:
        tamam = sum(ex.map(tek, isler))
    print(f"FXCM: {tamam}/{len(isler)} haftalık dosya", flush=True)
    meta = []
    for p in FX:
        parcalar = []
        for f in sorted((HAM / "fxcm" / p).glob("*.csv.gz")):
            try:
                ham = gzip.decompress(f.read_bytes())
                metin = ham.decode("utf-16") if ham[:2] in (b"\xff\xfe", b"\xfe\xff") else ham.decode("utf-8", "replace")
                parcalar.append(pd.read_csv(io.StringIO(metin)))
            except Exception as ex:
                print(f"  {f.name} okunamadı: {ex}", flush=True)
        if not parcalar:
            continue
        d = pd.concat(parcalar, ignore_index=True).dropna()
        t = pd.to_datetime(d["DateTime"], format="%m/%d/%Y %H:%M:%S.%f", utc=True)
        point, digits = (0.001, 3) if p.endswith("JPY") else (0.00001, 5)
        h1 = pd.DataFrame({"time": (t - pd.Timestamp("1970-01-01", tz="UTC")) // pd.Timedelta(seconds=1),
                           "open": d["BidOpen"], "high": d["BidHigh"], "low": d["BidLow"], "close": d["BidClose"],
                           "tick_volume": 60, "spread": (d["AskOpen"] - d["BidOpen"]) / point})
        h1 = h1[(h1.high >= h1.low) & (h1.open > 0)]
        dosya = _kaydet(p, h1)
        meta.append({"ad": p, "sinif": "forex", "kaynak": f"FXCM (spread gerçek ve ham/ECN; ek komisyon gidiş-dönüş %{FX_KOMISYON} = 100k'da ~7 USD)",
                     "bilgi": _bilgi(point, digits, FX_TICK_USD[p[3:6]], 0.01, FX_FINANSMAN, 3, 100000.0, FX_KOMISYON),
                     "dosyalar": {"H1": dosya["H1"], "H4": dosya["H4"]}, **{k: dosya[k] for k in ("mum", "ilk", "son")}})
        print(f"  {p}: {dosya['ilk']} → {dosya['son']}  {dosya['mum']} mum", flush=True)
    return meta


# ------------------------------------------------------------------ emtia / endeks (HistData)
def _histdata_donem(sembol: str, yil: int, ay: int | None) -> pd.DataFrame | None:
    """Bir yılın (ya da cari yılın bir ayının) M1 verisini indirip UTC H1'e toplar. Sonuç diske önbelleklenir."""
    ad = f"{sembol}_{yil}" + (f"{ay:02d}" if ay else "")
    onbellek = HAM / "histdata" / f"{ad}.csv.gz"
    yok = HAM / "histdata" / f"{ad}.yok"
    if onbellek.exists():
        return pd.read_csv(onbellek)
    if yok.exists():
        return None
    onbellek.parent.mkdir(parents=True, exist_ok=True)
    sayfa = (f"https://www.histdata.com/download-free-forex-historical-data/?/metatrader/1-minute-bar-quotes/"
             f"{sembol.lower()}/{yil}" + (f"/{ay}" if ay else ""))
    s = _oturum()
    for deneme in range(3):
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
                isim = next(n for n in zf.namelist() if n.lower().endswith(".csv"))      # .txt = boşluk raporu
                m1 = pd.read_csv(zf.open(isim), header=None, names=["d", "s", "o", "h", "l", "c", "v"])
            t = pd.to_datetime(m1["d"] + " " + m1["s"], format="%Y.%m.%d %H:%M") + pd.Timedelta(hours=5)   # EST sabit -> UTC
            m1["g"] = ((t - pd.Timestamp("1970-01-01")) // pd.Timedelta(seconds=1)) // 3600 * 3600
            h1 = m1.groupby("g").agg(open=("o", "first"), high=("h", "max"), low=("l", "min"), close=("c", "last"),
                                     tick_volume=("o", "size")).reset_index().rename(columns={"g": "time"})
            h1.to_csv(onbellek, index=False)
            return h1
        except Exception as ex:
            print(f"  {ad}: {type(ex).__name__} {ex}", flush=True)
            time.sleep(10)
    yok.touch()
    return None


def emtia_topla(bas_yil: int, bit_yil: int, bit_ay: int) -> list[dict]:
    meta = []
    for sembol, tanim in EMTIA_ENDEKS.items():
        parcalar = []
        for yil in range(bas_yil, bit_yil + 1):
            if yil < bit_yil:
                parcalar.append(_histdata_donem(sembol, yil, None))
            else:
                for ay in range(1, bit_ay + 1):
                    parcalar.append(_histdata_donem(sembol, yil, ay))
        parcalar = [p for p in parcalar if p is not None and len(p)]
        if not parcalar:
            print(f"  {sembol}: VERİ YOK", flush=True)
            continue
        tum = pd.concat(parcalar, ignore_index=True)
        tum = tum[(tum.high >= tum.low) & (tum.open > 0)]
        for bolum_ad, bas, bit, prm_kaynak in BOLUMLER.get(sembol, [(tanim[0], None, None, sembol)]):
            _, sinif, point, digits, sp_yuzde, sp_taban, tick_usd, min_lot, fin, uc_kat = EMTIA_ENDEKS[prm_kaynak]
            h1 = tum
            if bas:
                h1 = h1[h1["time"] >= pd.Timestamp(bas).timestamp()]
            if bit:
                h1 = h1[h1["time"] < pd.Timestamp(bit).timestamp()]
            h1 = h1.copy()
            saat = (h1["time"] // 3600) % 24
            baz = np.maximum(sp_taban, h1["open"] * sp_yuzde / 100)
            h1["spread"] = np.where((saat >= AKTIF_UTC[0]) & (saat < AKTIF_UTC[1]), baz, baz * PASIF_CARPAN) / point
            dosya = _kaydet(bolum_ad, h1)
            not_ = " [GRXEUR dosyasındaki yanlış etiketli bölüm]" if prm_kaynak != sembol else ""
            meta.append({"ad": bolum_ad, "sinif": sinif,
                         "kaynak": (f"HistData {sembol}{not_} (spread MODEL: max({sp_taban}, fiyatın %{sp_yuzde}'i) "
                                    f"aktif saatlerde, dışında x{PASIF_CARPAN})"),
                         "bilgi": _bilgi(point, digits, tick_usd, min_lot, fin, uc_kat),
                         "dosyalar": {"H1": dosya["H1"], "H4": dosya["H4"]}, **{k: dosya[k] for k in ("mum", "ilk", "son")}})
            print(f"  {bolum_ad} ({sembol}): {dosya['ilk']} → {dosya['son']}  {dosya['mum']} mum", flush=True)
    return meta


# ------------------------------------------------------------------ kripto (Binance)
def kripto_topla(bas_yil: int, bit_yil: int, bit_ay: int) -> list[dict]:
    isler = [(s, y, m) for s in KRIPTO for y in range(max(bas_yil, 2017), bit_yil + 1) for m in range(1, 13)
             if (y, m) <= (bit_yil, bit_ay)]

    def tek(x):
        s, y, m = x
        return _indir(f"https://data.binance.vision/data/spot/monthly/klines/{s}/1h/{s}-1h-{y}-{m:02d}.zip",
                      HAM / "binance" / s / f"{y}-{m:02d}.zip")
    with ThreadPoolExecutor(4) as ex:
        tamam = sum(ex.map(tek, isler))
    print(f"Binance: {tamam}/{len(isler)} aylık dosya", flush=True)
    meta = []
    for s, (ad, point, digits, sp_yuzde, min_lot) in KRIPTO.items():
        parcalar = []
        for f in sorted((HAM / "binance" / s).glob("*.zip")):
            with zipfile.ZipFile(f) as zf:
                d = pd.read_csv(zf.open(zf.namelist()[0]), header=None, usecols=[0, 1, 2, 3, 4, 8],
                                names=["t", "open", "high", "low", "close", "n"])
            d = d[pd.to_numeric(d["t"], errors="coerce").notna()].astype({"t": "int64"})
            d["time"] = np.where(d["t"] > 10 ** 14, d["t"] // 10 ** 6, d["t"] // 1000)     # 2025'ten itibaren mikro saniye
            parcalar.append(d)
        if not parcalar:
            continue
        d = pd.concat(parcalar, ignore_index=True).astype({"open": float, "high": float, "low": float, "close": float})
        h1 = pd.DataFrame({"time": d["time"], "open": d["open"], "high": d["high"], "low": d["low"], "close": d["close"],
                           "tick_volume": d["n"], "spread": d["open"] * sp_yuzde / 100 / point})
        dosya = _kaydet(ad, h1)
        meta.append({"ad": ad, "sinif": "kripto", "kaynak": f"Binance {s} spot (spread MODEL: fiyatın %{sp_yuzde}'i)",
                     "bilgi": _bilgi(point, digits, point, min_lot, KRIPTO_FINANSMAN, 3),
                     "dosyalar": {"H1": dosya["H1"], "H4": dosya["H4"]}, **{k: dosya[k] for k in ("mum", "ilk", "son")}})
        print(f"  {ad}: {dosya['ilk']} → {dosya['son']}  {dosya['mum']} mum", flush=True)
    return meta


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--bas", type=int, default=2015)
    ap.add_argument("--sadece", choices=["fx", "emtia", "kripto"], action="append")
    a = ap.parse_args()
    simdi = datetime.now(timezone.utc)
    bit_yil, bit_ay = simdi.year, simdi.month - 1 or 12
    if simdi.month == 1:
        bit_yil -= 1
    secim = a.sadece or ["fx", "emtia", "kripto"]
    dosya = CIKTI / "semboller.json"
    eski = json.loads(dosya.read_text(encoding="utf-8"))["semboller"] if dosya.exists() else []
    meta = {m["ad"]: m for m in eski}
    if "fx" in secim:
        meta.update({m["ad"]: m for m in fx_topla(a.bas, simdi.year)})
    if "kripto" in secim:
        meta.update({m["ad"]: m for m in kripto_topla(a.bas, bit_yil, bit_ay)})
    if "emtia" in secim:
        yeni = {m["ad"]: m for m in emtia_topla(a.bas, bit_yil, bit_ay)}
        for ad_ in [k for k, m in meta.items() if m["sinif"] in ("emtia", "endeks") and k not in yeni]:
            del meta[ad_]                       # artık kullanılmayan semboller (ör. USOIL)
            for f in CIKTI.glob(f"{ad_}_H?.csv.gz"):
                f.unlink()
        meta.update(yeni)
    CIKTI.mkdir(parents=True, exist_ok=True)
    dosya.write_text(json.dumps({"indirme_utc": simdi.isoformat(timespec="seconds"), "zaman_dilimi": "H1",
                                 "ust_zaman_dilimi": "H4", "sunucu_saati": "NY kapanışı (UTC+2 / ABD yaz saatinde UTC+3)",
                                 "not": "Halka açık kaynaklardan araştırma verisi; broker verisi değildir.",
                                 "semboller": list(meta.values())}, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"Tamam: {len(meta)} sembol -> {CIKTI}", flush=True)


if __name__ == "__main__":
    sys.exit(main())
