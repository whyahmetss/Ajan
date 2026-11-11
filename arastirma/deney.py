"""Hipotez testleri. Her hipotez docs/ARASTIRMA_GUNLUGU.md'de ÖNCEDEN tanımlandı.

Kullanım:  python -m arastirma.deney H0            (geliştirme dönemi: eğitim + TEST)
           python -m arastirma.deney H1 --kisa     (sadece özet tablo)
           python -m arastirma.deney H1 --kilitli  (KİLİTLİ dönem: strateji donduktan sonra, BİR kez)
"""
import argparse
import json
import sys
from datetime import datetime, timezone

import numpy as np
import pandas as pd
import yaml

from arastirma import motor
from arastirma.motor import Cikis, Maliyet
from bot import backtest
from bot.yollar import yol

pd.set_option("display.width", 250)
SABIT = Cikis.sabit(tp_atr=1.0, sl_atr=2.0, max_mum=24)       # H1-H5 için ön kayıtlı çıkış


# ------------------------------------------------------------------ sinyaller: sem -> (maske, yon, skor)
def s_mevcut(sem, esik=60):
    return sem.skor0 >= esik, sem.yon0, sem.skor0.astype(float)


def _trend_yukari(sem):
    with np.errstate(invalid="ignore"):
        return sem.d_c > sem.d_sma200


def _trend_asagi(sem):
    with np.errstate(invalid="ignore"):
        return sem.d_c < sem.d_sma200


def s_dip(sem, z=2.0):
    """D1 yükseliş trendi + z <= -z (sadece alış)."""
    with np.errstate(invalid="ignore"):
        m = _trend_yukari(sem) & (sem.z <= -z)
    return m, np.ones(sem.n, np.int8), -sem.z


def s_geri_cekilme(sem, z=2.0):
    """D1 trendi yönünde geri çekilme (iki yön)."""
    with np.errstate(invalid="ignore"):
        al = _trend_yukari(sem) & (sem.z <= -z)
        sat = _trend_asagi(sem) & (sem.z >= z)
    return al | sat, np.where(sat, -1, 1).astype(np.int8), np.abs(sem.z)


def s_asya(sem, z=2.0):
    """21:00-07:00 UTC arası ortalamaya dönüş (trend filtresi yok, iki yön)."""
    saat = sem.utc_saat
    asya = (saat >= 21) | (saat < 7)
    with np.errstate(invalid="ignore"):
        al, sat = asya & (sem.z <= -z), asya & (sem.z >= z)
    return al | sat, np.where(sat, -1, 1).astype(np.int8), np.abs(sem.z)


def s_gunluk_dip(sem, rsi=10.0):
    """Günün ilk H1 mumu kapanınca: dünkü günlük kapanış > SMA200 ve günlük RSI(2) < eşik (sadece alış)."""
    with np.errstate(invalid="ignore"):
        m = sem.gun_ilk & _trend_yukari(sem) & (sem.d_rsi2 < rsi) & (sem.d_atr > 0)
    return m, np.ones(sem.n, np.int8), 100.0 - sem.d_rsi2


def h_gun_ilk(sem):
    """H6/H7 için rastgele giriş havuzu: yükseliş trendindeki HER günün ilk mumu (aynı saat, aynı rejim)."""
    with np.errstate(invalid="ignore"):
        return sem.gun_ilk & _trend_yukari(sem) & (sem.d_atr > 0)


def s_gunluk_kirilim(sem, gun=20):
    """Günün ilk mumu kapanınca: dünkü günlük kapanış son `gun` günün en yüksek kapanışı ve > SMA200 (alış)."""
    zirve = sem.d_maks20 if gun == 20 else sem.f[f"d_maks{gun}"]
    with np.errstate(invalid="ignore"):
        m = sem.gun_ilk & _trend_yukari(sem) & (sem.d_c >= zirve) & (sem.d_atr > 0)
        guc = sem.d_c / sem.d_sma200
    return m, np.ones(sem.n, np.int8), guc


def s_h1_guc(sem, z=2.0):
    """Yükseliş trendinde H1 gücü: z >= +z (alış)."""
    with np.errstate(invalid="ignore"):
        m = _trend_yukari(sem) & (sem.z >= z) & (sem.d_atr > 0)
    return m, np.ones(sem.n, np.int8), sem.z


def h_trend(sem):
    with np.errstate(invalid="ignore"):
        return _trend_yukari(sem) & (sem.d_atr > 0)


GUNLUK = Cikis.sabit(tp_atr=1.0, sl_atr=2.0, max_mum=120, atr="d_atr")
TUTMA = Cikis(sl_atr=2.0, tp_r=motor.SONSUZ, be_r=motor.SONSUZ, tr_bas=motor.SONSUZ, tr_atr=0.0, max_mum=240, atr="d_atr")
HAVUZ = {"H6": h_gun_ilk, "H7": h_gun_ilk, "K1": h_gun_ilk, "K2": h_gun_ilk, "K3": h_trend}

HIPOTEZLER = {
    "H0": ("Mevcut strateji (skor>=60, SL 1.5 ATR, TP 2R, BE, takip)", s_mevcut, None, None),
    "H1": ("Endekslerde trendde dip alımı", s_dip, SABIT, ("endeks",)),
    "H2": ("Altın ve kriptoda trendde dip alımı", s_dip, SABIT, ("emtia_altin", "kripto")),
    "H3": ("Forex'te trend yönünde geri çekilme", s_geri_cekilme, SABIT, ("forex",)),
    "H4": ("Forex Asya seansı ortalamaya dönüş", s_asya, SABIT, ("forex",)),
    "H5": ("Mevcut sinyal + kısa hedef (TP 1 ATR, SL 2 ATR, 24 mum)", s_mevcut, SABIT, None),
    "H6": ("Endekslerde GÜNLÜK dip alımı (RSI2<10, >SMA200; TP 1 / SL 2 günlük ATR, 120 mum)", s_gunluk_dip, GUNLUK, ("endeks",)),
    "H7": ("Altın ve kriptoda GÜNLÜK dip alımı", s_gunluk_dip, GUNLUK, ("emtia_altin", "kripto")),
    "K1": ("Kripto günlük kırılım (20 gün zirve, >SMA200) + TP 1 / SL 2 günlük ATR, 120 mum", s_gunluk_kirilim, GUNLUK, ("kripto",)),
    "K2": ("Kripto günlük kırılım + sadece SL 2 günlük ATR ve 240 mum (hedef yok)", s_gunluk_kirilim, TUTMA, ("kripto",)),
    "K3": ("Kripto yükseliş trendinde H1 z>=2 + TP 1 / SL 2 günlük ATR, 120 mum", s_h1_guc, GUNLUK, ("kripto",)),
}


def evren(veriler, siniflar):
    if siniflar is None:
        return veriler
    out = []
    for v in veriler:
        if v.sinif in siniflar or ("emtia_altin" in siniflar and v.ad in ("XAUUSD", "XAGUSD")):
            out.append(v)
    return out


# ------------------------------------------------------------------ çalıştırma
def cfg_yukle():
    with open(yol("config.yaml"), encoding="utf-8") as f:
        return yaml.safe_load(f)


def varsayilan_cikis(cfg) -> Cikis:
    r = cfg["risk"]
    return Cikis(r["sl_atr_carpan"], r["tp_r"], r["breakeven_r"], r["trailing_baslangic_r"], r["trailing_atr_carpan"], 0)


def islemler(veriler, sinyal, cikis, mal, kesim, cfg, havuz=False, havuz_maskesi=None, **sinyal_kw):
    """Sembol başına tek pozisyonlu işlemler (+ istenirse rastgele giriş havuzu: uygun BÜTÜN mumlar,
    ya da havuz_maskesi verilmişse onun seçtiği mumlar)."""
    parcalar, hv = [], []
    max_sp = cfg["tarayici"]["max_spread_atr_orani"]
    for sem in veriler:
        uygun, son = motor.uygun_mumlar(sem, kesim.bas, kesim.bit, kesim.veri_bit, max_sp, mal)
        m, yon, skor = sinyal(sem, **sinyal_kw)
        df = motor.tek(sem, uygun & m, yon, cikis, mal, son)
        if len(df):
            df["skor"] = skor[df["i"].to_numpy()]
            parcalar.append(df)
        if havuz:
            if havuz_maskesi is not None:
                idx = np.flatnonzero(uygun & havuz_maskesi(sem))
            else:
                idx = np.flatnonzero(uygun)[::3]                 # her 3 mumdan biri: yeterince büyük havuz
            for y in np.unique(yon[uygun & m]) if (uygun & m).any() else []:
                hv.append(motor.simule(sem, idx, np.full(len(idx), y, np.int8), cikis, mal, son))
    bos = pd.DataFrame(columns=["sembol", "sinif", "giris_t", "cikis_t", "R", "risk_yuzde", "yon", "kod"])
    return (pd.concat(parcalar, ignore_index=True) if parcalar else bos,
            pd.concat(hv, ignore_index=True) if hv else bos)


def portfoy_islemleri(veriler, sinyal, cikis, mal, kesim, cfg, **sinyal_kw):
    """Canlı portföy kurallarıyla (max pozisyon, döviz limiti, tur başına max aday, günlük limit)."""
    parcalar, skorlar = [], []
    max_sp = cfg["tarayici"]["max_spread_atr_orani"]
    for sem in veriler:
        uygun, son = motor.uygun_mumlar(sem, kesim.bas, kesim.bit, kesim.veri_bit, max_sp, mal)
        m, yon, skor = sinyal(sem, **sinyal_kw)
        idx = np.flatnonzero(uygun & m)
        df = motor.simule(sem, idx, yon[idx], cikis, mal, son)
        if len(df):
            parcalar.append(df)
            skorlar.append(skor[df["i"].to_numpy()])
    if not parcalar:
        return pd.DataFrame()
    tum = pd.concat(parcalar, ignore_index=True)
    return motor.portfoy(tum, np.concatenate(skorlar), cfg)


def tablo(satirlar):
    return pd.DataFrame(satirlar).to_string(index=False)


def rapor(ad, veriler, dn, cfg, kisa=False, cikis=None, **sinyal_kw):
    tanim, sinyal, cikis0, siniflar = HIPOTEZLER[ad]
    cikis = cikis or cikis0 or varsayilan_cikis(cfg)
    ev = evren(veriler, siniflar)
    mal = Maliyet()
    print(f"\n{'=' * 100}\n {ad}: {tanim}\n evren: {len(ev)} sembol ({', '.join(sorted({v.sinif for v in ev}))}) | çıkış: {cikis}"
          f"\n{'=' * 100}")
    sonuc = {}
    satir = []
    for b in ("egitim", "TEST"):
        df, hv = islemler(ev, sinyal, cikis, mal, dn[b], cfg, havuz=True, havuz_maskesi=HAVUZ.get(ad), **sinyal_kw)
        sonuc[b] = (df, hv)
        satir.append({"bolum": b, "kapsam": "hepsi", **motor.ozet(df)})
        for sn, g in df.groupby("sinif"):
            satir.append({"bolum": b, "kapsam": sn, **motor.ozet(g)})
    print("1) Sembol başına (portföy kuralları yok), maliyetler dahil:")
    print(tablo(satir))

    print("\n2) Rastgele girişe karşı (aynı çıkış, aynı sembol/yıl/yön; p<0.05 = sinyal anlamlı):")
    satir = []
    for b in ("egitim", "TEST"):
        df, hv = sonuc[b]
        if len(df):
            alt, ust = motor.blok_guven_araligi(df)
            satir.append({"bolum": b, "kapsam": "hepsi", **motor.permutasyon(df, hv), "ort_R_%95": f"[{alt:+.3f}, {ust:+.3f}]"})
            for sn, g in df.groupby("sinif"):
                satir.append({"bolum": b, "kapsam": sn, **motor.permutasyon(g, hv[hv.sinif == sn]), "ort_R_%95": ""})
    print(tablo(satir))
    if kisa:
        return sonuc

    gel = backtest.Kesim("gelistirme", dn["egitim"].bas, dn["TEST"].bit, dn["TEST"].veri_bit)
    df_gel, _ = islemler(ev, sinyal, cikis, mal, gel, cfg, **sinyal_kw)
    print("\n3) Yıl yıl (geliştirme dönemi, sembol başına):")
    print(motor.yil_yil(df_gel).to_string(index=False) if len(df_gel) else "işlem yok")

    print("\n4) Maliyet stresi (TEST bölümü):")
    satir = []
    for etiket, m in (("baz", mal), ("spread x1.5", Maliyet(spread_carpani=1.5)), ("spread x2", Maliyet(spread_carpani=2.0)),
                      ("spread x1.5 + ek komisyon %0.007", Maliyet(spread_carpani=1.5, komisyon_yuzde=0.007)),
                      ("maliyetsiz (spread 0.01x, swap yok)", Maliyet(spread_carpani=0.01, komisyon_carpani=0.0, swap=False))):
        d, _ = islemler(ev, sinyal, cikis, m, dn["TEST"], cfg, **sinyal_kw)
        satir.append({"maliyet": etiket, **motor.ozet(d)})
    print(tablo(satir))

    print("\n5) Parametre hassasiyeti ±%20 (TEST bölümü):")
    satir = [{"degisiklik": "baz", **motor.ozet(sonuc["TEST"][0])}]
    for etiket, c in motor.komsular(cikis):
        d, _ = islemler(ev, sinyal, c, mal, dn["TEST"], cfg, **sinyal_kw)
        satir.append({"degisiklik": etiket, **motor.ozet(d)})
    print(tablo(satir))

    print("\n6) Canlı portföy kurallarıyla (max 4 pozisyon, aynı döviz 2, tur başına 3 aday, günlük %2):")
    satir = []
    for b in ("egitim", "TEST"):
        d = portfoy_islemleri(ev, sinyal, cikis, mal, dn[b], cfg, **sinyal_kw)
        yil = (dn[b].bit - dn[b].bas) / (365.25 * 86400)
        o = motor.ozet(d)
        satir.append({"bolum": b, **o, "yillik_getiri_%": round(o.get("getiri_%", 0) / yil, 1),
                      **{f"MC_dusus_{k}": v for k, v in (motor.monte_carlo_dusus(d).items() if len(d) else [])}})
    print(tablo(satir))

    print("\n7) Walk-forward (3 yıl eğitim → sonraki 1 yıl test, kayarak):")
    walk_forward(ad, veriler, dn, cfg, cikis=cikis)
    return sonuc


WF_IZGARA = {"H0": ("esik", [50, 60, 70, 80]), "H5": ("esik", [50, 60, 70, 80]),
             "H1": ("z", [1.6, 2.0, 2.4]), "H2": ("z", [1.6, 2.0, 2.4]), "H3": ("z", [1.6, 2.0, 2.4]),
             "H4": ("z", [1.6, 2.0, 2.4]), "H6": ("rsi", [5.0, 10.0, 15.0]), "H7": ("rsi", [5.0, 10.0, 15.0]),
             "K3": ("z", [1.6, 2.0, 2.4])}


def walk_forward(ad, veriler, dn, cfg, egitim_yil=3, cikis=None):
    """Kayan pencere: her yıl, önceki `egitim_yil` yılda en iyi ortalama R'yi veren ayar seçilir ve
    SONRAKİ yılda (hiç görülmemiş) kullanılır. Sabit varsayılan ayarla da karşılaştırılır."""
    tanim, sinyal, cikis0, siniflar = HIPOTEZLER[ad]
    cikis = cikis or cikis0 or varsayilan_cikis(cfg)
    prm, degerler = WF_IZGARA.get(ad, (None, [None]))
    ev, mal = evren(veriler, siniflar), Maliyet()
    YIL = int(365.25 * 86400)
    T0, TK = dn["egitim"].bas, dn["TEST"].bit
    satir, secili, sabit = [], [], []
    bas = T0 + egitim_yil * YIL
    while bas < TK:
        bit = min(bas + YIL, TK)
        egitim = backtest.Kesim("e", bas - egitim_yil * YIL, bas, bas)
        test = backtest.Kesim("t", bas, bit, TK)
        en_iyi, en_iyi_r = None, -1e9
        for d in degerler:
            e, _ = islemler(ev, sinyal, cikis, mal, egitim, cfg, **({prm: d} if prm else {}))
            if len(e) >= 30 and e["R"].mean() > en_iyi_r:
                en_iyi, en_iyi_r = d, float(e["R"].mean())
        if en_iyi is None:
            en_iyi, en_iyi_r = degerler[len(degerler) // 2], float("nan")
        t, _ = islemler(ev, sinyal, cikis, mal, test, cfg, **({prm: en_iyi} if prm else {}))
        sb, _ = islemler(ev, sinyal, cikis, mal, test, cfg)
        secili.append(t)
        sabit.append(sb)
        o = motor.ozet(t)
        satir.append({"test_yili": str(pd.to_datetime(bas, unit="s").date()), f"secilen_{prm}": en_iyi,
                      "egitim_ort_R": round(en_iyi_r, 3), "islem": o.get("islem", 0), "kazanma_%": o.get("kazanma_%"),
                      "ort_R": o.get("ort_R"), "kar_faktoru": o.get("kar_faktoru"),
                      "sabit_ayar_ort_R": motor.ozet(sb).get("ort_R")})
        bas = bit
    print(tablo(satir))
    for etiket, parca in (("walk-forward (ayar her yıl yeniden seçildi)", secili), ("sabit varsayılan ayar", sabit)):
        dolu = [x for x in parca if len(x)]
        tum = pd.concat(dolu, ignore_index=True) if dolu else pd.DataFrame()
        print(f"  TOPLAM {etiket}: {motor.ozet(tum)}")


def kilitli_test(adlar, veriler, dn, cfg, zorla=False):
    """KİLİTLİ dönem: TEK açılış (birden fazla önceden tanımlı hipotez aynı açılışta raporlanabilir).
    Her açılış kayitlar/kilit.json'a yazılır."""
    kilit = backtest._kilit_oku(cfg)
    if kilit.get("acilislar") and not zorla:
        raise SystemExit(f"KİLİTLİ dönem daha önce açıldı ({kilit['acilislar'][-1]['zaman_utc']}). Tekrar bakmak "
                         "testi geçersiz kılar (--zorla ile kayda geçerek bakılabilir).")
    mal, kayit, cikti = Maliyet(), {}, {}
    yil = (dn["KILITLI"].bit - dn["KILITLI"].bas) / (365.25 * 86400)
    for ad in adlar:
        tanim, sinyal, cikis0, siniflar = HIPOTEZLER[ad]
        cikis = cikis0 or varsayilan_cikis(cfg)
        ev = evren(veriler, siniflar)
        df, hv = islemler(ev, sinyal, cikis, mal, dn["KILITLI"], cfg, havuz=True, havuz_maskesi=HAVUZ.get(ad))
        pf = portfoy_islemleri(ev, sinyal, cikis, mal, dn["KILITLI"], cfg)
        po = motor.ozet(pf)
        kayit[ad] = {"cikis": str(cikis), "sembol_basina": motor.ozet(df),
                     "portfoy": {**po, "yillik_getiri_%": round(po.get("getiri_%", 0) / yil, 1)},
                     "rastgele": motor.permutasyon(df, hv) if len(df) else {},
                     "ort_R_%95": [round(x, 3) for x in motor.blok_guven_araligi(df)] if len(df) else None}
        cikti[ad] = (df, pf, tanim)
    kilit.setdefault("acilislar", []).append(
        {"zaman_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"), "hipotezler": adlar,
         "sonuc": kayit, "zorla": zorla})
    backtest._kilit_yaz(cfg, kilit)
    for ad, (df, pf, tanim) in cikti.items():
        print(f"\n{'#' * 100}\n KİLİTLİ DÖNEM — {ad}: {tanim}\n{'#' * 100}")
        print(json.dumps(kayit[ad], ensure_ascii=False, indent=1))
        if len(df):
            print("Sembol başına:")
            print(tablo([{"sembol": sn, **motor.ozet(g)} for sn, g in df.groupby("sembol")]))
            print("Yıl yıl (sembol başına):")
            print(motor.yil_yil(df).to_string(index=False))
            print("Çıkış türleri:", df["kod"].map(motor.KOD_AD).value_counts(normalize=True).round(2).to_dict())
        if len(pf):
            print("Portföy Monte Carlo düşüş:", motor.monte_carlo_dusus(pf))
    return cikti


def hazirla(cfg=None, sadece=None):
    cfg = cfg or cfg_yukle()
    veriler = motor.yukle(cfg, "veri", sadece)
    dn = backtest.donemler(veriler, cfg)
    return cfg, veriler, dn


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("hipotez", nargs="+", choices=list(HIPOTEZLER))
    ap.add_argument("--kisa", action="store_true")
    ap.add_argument("--kilitli", action="store_true")
    ap.add_argument("--zorla", action="store_true")
    a = ap.parse_args()
    cfg, veriler, dn = hazirla()
    t = lambda x: str(pd.to_datetime(x, unit="s").date())           # noqa: E731
    print(f"{len(veriler)} sembol | eğitim {t(dn['egitim'].bas)} → {t(dn['egitim'].bit)} | TEST → {t(dn['TEST'].bit)} | "
          f"KİLİTLİ → {t(dn['KILITLI'].bit)}")
    if a.kilitli:
        kilitli_test(a.hipotez, veriler, dn, cfg, zorla=a.zorla)
        return
    for ad in a.hipotez:
        rapor(ad, veriler, dn, cfg, kisa=a.kisa)


if __name__ == "__main__":
    sys.exit(main())
