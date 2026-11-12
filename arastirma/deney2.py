"""Araştırma Raporu 2'nin test koşucusu (docs/ARASTIRMA_RAPORU_2.md). Adaylar, dönemler ve eleme ölçütleri
o belgede ÖNCEDEN sabitlendi; burada sadece uygulanır.

Kullanım:  python -m arastirma.deney2 O1 O2 V1 T1 T2 M1 K2      (eğitim + doğrulama; kilitli döneme BAKMAZ)
           python -m arastirma.deney2 T1 --kilitli              (dokunulmamış test; tek sefer, kayda geçer)
"""
import argparse
import json
import sys
import zipfile
from dataclasses import replace
from datetime import datetime, timezone

import numpy as np
import pandas as pd

from arastirma import motor, prop
from arastirma.deney import cfg_yukle
from arastirma.motor import Maliyet
from arastirma.stratejiler import ADAYLAR, ortak
from arastirma.veri_topla import HAM, KRIPTO, sunucu_zamani
from bot import backtest
from bot.yollar import yol

pd.set_option("display.width", 250)
KAY = 0.25                                      # temel kayma: piyasa emirlerinde spread'in çeyreği
MAJOR = {"EURUSD", "GBPUSD", "USDJPY", "USDCHF", "AUDUSD", "EURJPY", "GBPJPY"}      # Lean FxcmFeeModel 1. grup
STRES = [("temel (kayma 0.25 sp)", Maliyet(), KAY, 0.0),
         ("spread x1.5 + kayma 0.5 sp", Maliyet(spread_carpani=1.5), 0.5, 0.0),
         ("spread x2 + kayma 1 sp + stopta 0.05 ATR", Maliyet(spread_carpani=2.0), 1.0, 0.05),
         ("komisyon x2", Maliyet(komisyon_carpani=2.0), KAY, 0.0),
         ("finansman x3", Maliyet(swap_carpani=3.0), KAY, 0.0),
         ("kaymasız", Maliyet(), 0.0, 0.0)]
ISINMA = {"H4": 250, "M15": 200}
PERIYOT = {"M15": 900, "H1": 3600, "H4": 14400}


# ------------------------------------------------------------------ veri
def _komisyon(ad, sinif, bilgi):
    if sinif == "forex":
        return {**bilgi, "komisyon_yuzde": 0.008 if ad in MAJOR else 0.012}
    return bilgi


def _kripto_hacim(ad: str) -> pd.DataFrame:
    """Binance ham dosyalarından saatlik USDT hacmi (sunucu saatiyle). Önbellek: veri/hacim/."""
    onbellek = yol("veri") / "hacim" / f"{ad}.csv.gz"
    if onbellek.exists():
        return pd.read_csv(onbellek)
    kaynak = next(k for k, v in KRIPTO.items() if v[0] == ad)
    parcalar = []
    for f in sorted((HAM / "binance" / kaynak).glob("*.zip")):
        with zipfile.ZipFile(f) as zf:
            d = pd.read_csv(zf.open(zf.namelist()[0]), header=None, usecols=[0, 7], names=["t", "hacim"])
        d = d[pd.to_numeric(d["t"], errors="coerce").notna()].astype({"t": "int64", "hacim": float})
        d["time"] = np.where(d["t"] > 10 ** 14, d["t"] // 10 ** 6, d["t"] // 1000)
        parcalar.append(d[["time", "hacim"]])
    d = pd.concat(parcalar, ignore_index=True).sort_values("time").drop_duplicates("time")
    d["time"] = sunucu_zamani(d["time"].to_numpy(np.int64))
    d = d.drop_duplicates("time", keep="first")
    onbellek.parent.mkdir(parents=True, exist_ok=True)
    d.to_csv(onbellek, index=False)
    return d


def yukle(zd: str, cfg, kilitli: bool = False) -> list:
    """zd: "H1" | "H4" (veri/, sunucu saati) ya da "M15" (veri_m15/, UTC). kilitli=True sadece --kilitli içindir."""
    out = []
    if zd == "M15":
        kok = yol("veri_m15")
        for s in json.loads((kok / "semboller.json").read_text(encoding="utf-8"))["semboller"]:
            df = pd.read_csv(kok / s["dosya"])
            if kilitli:
                df = pd.concat([df, pd.read_csv(kok / "kilitli" / s["dosya"])], ignore_index=True)
            if len(df) > ISINMA[zd] + 10:
                out.append(ortak.seri_kur(s["ad"], s["sinif"], _komisyon(s["ad"], s["sinif"], s["bilgi"]), df, 900,
                                          ISINMA[zd], gunluk=False))
        return out
    kok = yol("veri")
    isinma = int((cfg.get("backtest") or {}).get("isinma_bar", 1000)) if zd == "H1" else ISINMA[zd]
    for s in json.loads((kok / "semboller.json").read_text(encoding="utf-8"))["semboller"]:
        df = pd.read_csv(kok / s["dosyalar"][zd])
        if zd == "H1" and s["sinif"] == "kripto":
            df = df.merge(_kripto_hacim(s["ad"]), on="time", how="left")
        out.append(ortak.seri_kur(s["ad"], s["sinif"], _komisyon(s["ad"], s["sinif"], s["bilgi"]), df, PERIYOT[zd], isinma))
    return out


def donemler(cfg) -> dict:
    """Önceki araştırmayla AYNI takvim sınırları (sunucu saati). eğitim / doğrulama (eski adıyla TEST) / kilitli."""
    tum = backtest.donemler([motor.canonik(x) for x in motor.yukle(cfg, "veri")], cfg)
    return {"egitim": tum["egitim"], "dogrulama": tum["TEST"], "KILITLI": tum["KILITLI"]}


def _kaydir(kesim, zd):
    """M15 verisi UTC; sunucu saati sınırlarını ~3 saat geri al (sınırlar hafta sonuna yakın, etkisi yok denecek kadar az)."""
    if zd != "M15":
        return kesim
    return backtest.Kesim(kesim.ad, kesim.bas - 10800, kesim.bit - 10800, kesim.veri_bit - 10800)


# ------------------------------------------------------------------ işlemler
def islemler(mod, seriler, prm, kesim, cfg, mal=None, kay=KAY, kay_atr=0.0, havuz=False, portfoy=False):
    mal = mal or Maliyet()
    max_sp = cfg["tarayici"]["max_spread_atr_orani"]
    parcalar, hv = [], []
    for s in seriler:
        if not mod.evren(s):
            continue
        uyg, son = ortak.uygun(s, kesim.bas, kesim.bit, kesim.veri_bit, max_sp, mal.spread_carpani)
        e = mod.sinyal(s, prm)
        e = e.sec(uyg[e.i]) if len(e) else e
        if len(e):
            df = (ortak.simule if portfoy else ortak.tek)(s, e, mal, son, kay, kay_atr)
            if len(df):
                parcalar.append(df)
        if havuz:
            he = mod.havuz(s, prm)
            he = he.sec(uyg[he.i]) if len(he) else he
            if len(he):
                hv.append(ortak.simule(s, he, mal, son, kay, kay_atr))
    bos = pd.DataFrame(columns=["sembol", "sinif", "giris_t", "cikis_t", "R", "risk_yuzde", "yon", "kod", "skor"])
    df = pd.concat(parcalar, ignore_index=True) if parcalar else bos
    if portfoy and len(df):
        df = motor.portfoy(df, df["skor"].to_numpy(), cfg)
    return (df, pd.concat(hv, ignore_index=True) if hv else bos) if havuz else df


def fark_araligi(a: pd.DataFrame, b: pd.DataFrame, n: int = 4000, tohum: int = 0, guven: float = 90.0) -> tuple:
    """ort R(a) - ort R(b) için haftalık blok bootstrap aralığı (aynı haftalar birlikte örneklenir)."""
    ha, hb = a["giris_t"].to_numpy() // (7 * 86400), b["giris_t"].to_numpy() // (7 * 86400)
    haftalar = np.unique(np.r_[ha, hb])
    ia, ib = np.searchsorted(haftalar, ha), np.searchsorted(haftalar, hb)
    ta, sa = np.bincount(ia, a["R"].to_numpy(float), len(haftalar)), np.bincount(ia, minlength=len(haftalar))
    tb, sb = np.bincount(ib, b["R"].to_numpy(float), len(haftalar)), np.bincount(ib, minlength=len(haftalar))
    rng = np.random.default_rng(tohum)
    sec = rng.integers(0, len(haftalar), (n, len(haftalar)))
    with np.errstate(invalid="ignore", divide="ignore"):
        fark = ta[sec].sum(1) / sa[sec].sum(1) - tb[sec].sum(1) / sb[sec].sum(1)
    fark = fark[np.isfinite(fark)]
    alt = (100 - guven) / 2
    return float(np.percentile(fark, alt)), float(np.percentile(fark, 100 - alt))


def tablo(satirlar):
    return pd.DataFrame(satirlar).to_string(index=False)


def _yillik(o, kesim):
    return round(o.get("getiri_%", 0) / ((kesim.bit - kesim.bas) / (365.25 * 86400)), 1)


def rapor(ad, veri, dn, cfg, k2=None) -> dict:
    """Bir adayın eğitim + doğrulama raporu ve eleme ölçütleri (1-7, 9). Dönüş: özet sözlüğü."""
    tanim, mod = ADAYLAR[ad]
    seriler = [s for s in veri[mod.ZD] if mod.evren(s)]
    prm = mod.PRM
    kes = {b: _kaydir(dn[b], mod.ZD) for b in ("egitim", "dogrulama")}
    print(f"\n{'=' * 110}\n {ad}: {tanim}\n zaman dilimi {mod.ZD} | {len(seriler)} sembol: {', '.join(s.ad for s in seriler)}\n"
          f" parametreler: {prm}\n{'=' * 110}")
    ozet = {"aday": ad}
    sonuc = {}
    satir, satir2 = [], []
    for b in ("egitim", "dogrulama"):
        df, hv = islemler(mod, seriler, prm, kes[b], cfg, havuz=True)
        sonuc[b] = df
        o = motor.ozet(df)
        satir.append({"bolum": b, "kapsam": "hepsi", **o})
        for sn, g in df.groupby("sinif"):
            if df["sinif"].nunique() > 1:
                satir.append({"bolum": b, "kapsam": sn, **motor.ozet(g)})
        if len(df):
            alt, ust = motor.blok_guven_araligi(df)
            pm = motor.permutasyon(df, hv, n=5000)
            satir2.append({"bolum": b, **pm, "ort_R_%95": f"[{alt:+.3f}, {ust:+.3f}]"})
            ozet[f"{b}_p"] = pm["p"]
        ozet[b] = o
    print("1) Sembol başına tek pozisyon, maliyet + temel kayma dahil:")
    print(tablo(satir))
    print("\n2) Rastgele girişe karşı (aynı çıkış kuralı, aynı sembol/yıl/yön):")
    print(tablo(satir2) if satir2 else "işlem yok")
    if len(sonuc["dogrulama"]):
        print("\n   Doğrulama, sembol sembol:")
        print(tablo([{"sembol": sn, **motor.ozet(g)} for sn, g in sonuc["dogrulama"].groupby("sembol")]))
        print("   Çıkış türleri (doğrulama):",
              sonuc["dogrulama"]["kod"].map(ortak.KOD_AD).value_counts(normalize=True).round(2).to_dict(),
              "| ortalama maliyet (R):", round(float(sonuc["dogrulama"]["maliyet_R"].mean()), 3))

    gel = backtest.Kesim("gel", kes["egitim"].bas, kes["dogrulama"].bit, kes["dogrulama"].veri_bit)
    df_gel = islemler(mod, seriler, prm, gel, cfg)
    yy = motor.yil_yil(df_gel) if len(df_gel) else pd.DataFrame()
    print("\n3) Yıl yıl (eğitim + doğrulama; sabit kural = ayarsız yürüyen test):")
    print(yy.to_string(index=False) if len(yy) else "işlem yok")
    ozet["pozitif_yil_orani"] = float((yy["ort_R"] > 0).mean()) if len(yy) else 0.0

    print("\n4) Maliyet / kayma stresi (doğrulama):")
    satir = []
    for etiket, m, kay, kay_atr in STRES:
        d = islemler(mod, seriler, prm, kes["dogrulama"], cfg, mal=m, kay=kay, kay_atr=kay_atr)
        satir.append({"senaryo": etiket, **motor.ozet(d)})
    print(tablo(satir))
    ozet["stres_ort_R"] = satir[1].get("ort_R")

    print("\n5) Komşu parametreler ±%20 (doğrulama):")
    satir = [{"degisiklik": "temel", **ozet["dogrulama"]}]
    for k, degerler in mod.KOMSU.items():
        for v in degerler:
            d = islemler(mod, seriler, {**prm, k: v}, kes["dogrulama"], cfg)
            satir.append({"degisiklik": f"{k}={v}", **motor.ozet(d)})
    print(tablo(satir))
    ozet["komsu_min_ort_R"] = min((s.get("ort_R", -9) for s in satir[1:]), default=None)

    print("\n6) Portföy kurallarıyla (en fazla 4 pozisyon, aynı döviz 2, tur başına 3 aday, günlük %2; işlem başı %0.5):")
    satir = []
    for b in ("egitim", "dogrulama"):
        d = islemler(mod, seriler, prm, kes[b], cfg, portfoy=True)
        o = motor.ozet(d)
        satir.append({"bolum": b, **o, "yillik_%": _yillik(o, kes[b]),
                      **{f"MC_dusus_{k}": v for k, v in (motor.monte_carlo_dusus(d).items() if len(d) > 5 else [])}})
        ozet[f"portfoy_{b}"] = {**o, "yillik_%": _yillik(o, kes[b])}
    print(tablo(satir))

    print("\n7) Prop firma kuralları (eğitim + doğrulama portföyü; her hafta yeni değerlendirme başlatılır):")
    pf = islemler(mod, seriler, prm, gel, cfg, portfoy=True)
    if len(pf):
        ps = prop.simule(pf, {s.ad: s for s in seriler}, gel.bas, gel.bit)
        print(ps.to_string(index=False))
        print("   Bütün dönem:", ps.attrs, "\n   Monte Carlo (kapanmış işlemlerle):", prop.monte_carlo(pf))
        ozet["prop_ihlal_%"] = float(ps.iloc[0]["ihlal_gunluk_%"] + ps.iloc[0]["ihlal_toplam_%"])
        ozet["prop_gecti_%"] = float(ps.iloc[0]["gecti_%"])
    if k2 is not None and ad in ("V1", "T2"):
        print("\n8) K2'ye göre fark (ort R, haftalık blok bootstrap %90 aralığı):")
        for b in ("egitim", "dogrulama"):
            if len(sonuc[b]) and len(k2[b]):
                alt, ust = fark_araligi(sonuc[b], k2[b])
                f = sonuc[b]["R"].mean() - k2[b]["R"].mean()
                print(f"   {b}: fark {f:+.3f}R  [%90: {alt:+.3f}, {ust:+.3f}]")
                ozet[f"k2_fark_{b}"] = (round(float(f), 3), round(alt, 3), round(ust, 3))
    ozet["islemler"] = sonuc
    return ozet


def olcutler(o: dict, n_hipotez: int = 6) -> list:
    """Bölüm 4.7 eleme ölçütleri (8 = dokunulmamış test, burada değerlendirilmez)."""
    ad = o["aday"]
    d, e = o.get("dogrulama", {}), o.get("egitim", {})
    asgari = (100, 40) if ad in ("V1", "T2") else (200, 80)
    sonuc = [("1 doğrulama ort R >= +0.05 ve PF >= 1.2", d.get("ort_R", -9) >= 0.05 and d.get("kar_faktoru", 0) >= 1.2),
             (f"2 işlem sayısı eğitim >= {asgari[0]}, doğrulama >= {asgari[1]}",
              e.get("islem", 0) >= asgari[0] and d.get("islem", 0) >= asgari[1]),
             (f"3 rastgele girişe karşı p < {0.05 / n_hipotez:.4f} (doğrulama)", o.get("dogrulama_p", 1.0) < 0.05 / n_hipotez),
             ("4 spread x1.5 + kayma 0.5 sp'de ort R > 0", (o.get("stres_ort_R") or -9) > 0),
             ("5 komşu parametrelerin hepsinde ort R > 0", (o.get("komsu_min_ort_R") or -9) > 0),
             ("6 yılların >= %70'i pozitif", o.get("pozitif_yil_orani", 0) >= 0.7)]
    if ad in ("V1", "T2"):
        f = o.get("k2_fark_dogrulama")
        sonuc.append(("7 K2'den iyi: doğrulamada fark > 0 ve %90 aralığının altı > 0", bool(f and f[0] > 0 and f[1] > 0)))
    sonuc.append(("9 prop ihlal oranı <= %5", o.get("prop_ihlal_%", 100.0) <= 5.0))
    return sonuc


def kilitli(adlar, veri, dn, cfg):
    """Dokunulmamış test: TEK açılış; kayitlar/kilit.json'a yazılır."""
    kilit = backtest._kilit_oku(cfg)
    kayit = {}
    for ad in adlar:
        tanim, mod = ADAYLAR[ad]
        seriler = [s for s in veri[mod.ZD] if mod.evren(s)]
        kes = _kaydir(dn["KILITLI"], mod.ZD)
        df, hv = islemler(mod, seriler, mod.PRM, kes, cfg, havuz=True)
        pf = islemler(mod, seriler, mod.PRM, kes, cfg, portfoy=True)
        po = motor.ozet(pf)
        kayit[ad] = {"tanim": tanim, "prm": {k: v for k, v in mod.PRM.items()}, "sembol_basina": motor.ozet(df),
                     "portfoy": {**po, "yillik_%": _yillik(po, kes)},
                     "rastgele": motor.permutasyon(df, hv, n=5000) if len(df) else {},
                     "ort_R_%95": [round(x, 3) for x in motor.blok_guven_araligi(df)] if len(df) else None}
        print(f"\n{'#' * 100}\n DOKUNULMAMIŞ TEST — {ad}: {tanim}\n{'#' * 100}")
        print(json.dumps(kayit[ad], ensure_ascii=False, indent=1))
        if len(df):
            print(tablo([{"sembol": sn, **motor.ozet(g)} for sn, g in df.groupby("sembol")]))
            print(motor.yil_yil(df).to_string(index=False))
        if len(pf):
            ps = prop.simule(pf, {s.ad: s for s in seriler}, kes.bas, kes.bit)
            print(ps.to_string(index=False), "\n", ps.attrs)
    kilit.setdefault("acilislar", []).append(
        {"zaman_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"), "hipotezler": adlar,
         "belge": "docs/ARASTIRMA_RAPORU_2.md", "sonuc": kayit})
    backtest._kilit_yaz(cfg, kilit)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("aday", nargs="+", choices=list(ADAYLAR))
    ap.add_argument("--kilitli", action="store_true")
    a = ap.parse_args()
    cfg = cfg_yukle()
    dn = donemler(cfg)
    t = lambda x: str(pd.to_datetime(x, unit="s").date())           # noqa: E731
    print(f"eğitim {t(dn['egitim'].bas)} → {t(dn['egitim'].bit)} | doğrulama → {t(dn['dogrulama'].bit)} | "
          f"dokunulmamış → {t(dn['KILITLI'].bit)}")
    zdler = {ADAYLAR[x][1].ZD for x in a.aday} | ({"H1"} if not a.kilitli else set())
    veri = {zd: yukle(zd, cfg, kilitli=a.kilitli) for zd in zdler}
    if a.kilitli:
        kilitli(a.aday, veri, dn, cfg)
        return
    k2 = rapor("K2", veri, dn, cfg)
    ozetler = [k2] + [rapor(ad, veri, dn, cfg, k2=k2["islemler"]) for ad in a.aday if ad != "K2"]
    print(f"\n{'=' * 110}\n KARŞILAŞTIRMA (portföy kurallarıyla) ve ELEME\n{'=' * 110}")
    satir = []
    for o in ozetler:
        for b in ("egitim", "dogrulama"):
            p = o.get(f"portfoy_{b}", {})
            satir.append({"aday": o["aday"], "bolum": b, "islem": p.get("islem"), "WR_%": p.get("kazanma_%"),
                          "ort_R": p.get("ort_R"), "PF": p.get("kar_faktoru"), "max_dusus_%": p.get("max_dusus_%"),
                          "yillik_%": p.get("yillik_%"), "p_rastgele": o.get(f"{b}_p"),
                          "prop_gecti_%": o.get("prop_gecti_%") if b == "dogrulama" else None,
                          "prop_ihlal_%": o.get("prop_ihlal_%") if b == "dogrulama" else None})
    print(tablo(satir))
    for o in ozetler:
        if o["aday"] == "K2":
            continue
        ol = olcutler(o)
        print(f"\n {o['aday']}: " + ("ÖLÇÜT 1-7, 9 GEÇTİ" if all(x[1] for x in ol) else "ELENDİ"))
        for metin, gecti in ol:
            print(f"    [{'+' if gecti else '-'}] {metin}")


if __name__ == "__main__":
    sys.exit(main())
