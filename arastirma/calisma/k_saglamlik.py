"""Kripto momentum (K1/K2) için sağlamlık yoklaması. SADECE geliştirme dönemi (eğitim + TEST).
Ayar seçmek için değil, zayıf nokta aramak için."""
from dataclasses import replace
import numpy as np
import pandas as pd
from arastirma import deney, motor
from arastirma.motor import Maliyet
from bot import backtest

cfg, veriler, dn = deney.hazirla()
kripto = [v for v in veriler if v.sinif == "kripto"]
mal = Maliyet()
gel = backtest.Kesim("gel", dn["egitim"].bas, dn["TEST"].bit, dn["TEST"].veri_bit)
bolumler = {"egitim": dn["egitim"], "TEST": dn["TEST"]}


def calis(ev, sinyal, cikis, kesim, **kw):
    return deney.islemler(ev, sinyal, cikis, mal, kesim, cfg, **kw)[0]


def satir(etiket, ev, sinyal, cikis, **kw):
    out = {"deney": etiket}
    for b, k in bolumler.items():
        o = motor.ozet(calis(ev, sinyal, cikis, k, **kw))
        out.update({f"{b}_n": o.get("islem", 0), f"{b}_kazanma": o.get("kazanma_%"), f"{b}_ortR": o.get("ort_R"),
                    f"{b}_KF": o.get("kar_faktoru")})
    return out


for ad, cikis in (("K1 (TP 1 ATR)", deney.GUNLUK), ("K2 (hedef yok, 240 mum)", deney.TUTMA)):
    print(f"\n================ {ad} ================")
    s = [satir("baz (20 gün, >SMA200)", kripto, deney.s_gunluk_kirilim, cikis)]
    # (1) coin coin
    for v in kripto:
        s.append(satir(f"  sadece {v.ad}", [v], deney.s_gunluk_kirilim, cikis))
    # (2) kırılım penceresi 16 / 24 gün
    for g in (16, 24):
        ev2 = []
        for v in kripto:
            w = replace(v, f=dict(v.f))
            gun = v.t // 86400
            kap = pd.DataFrame({"gun": gun, "c": v.c}).groupby("gun")["c"].last()
            pos = np.searchsorted(kap.index.to_numpy(), gun) - 1
            x = kap.rolling(g).max().to_numpy(float)
            w.f[f"d_maks{g}"] = np.where(pos >= 0, x[np.clip(pos, 0, None)], np.nan)
            ev2.append(w)
        s.append(satir(f"kırılım penceresi {g} gün", ev2, deney.s_gunluk_kirilim, cikis, gun=g))
    # (3) SMA200 filtresi olmadan
    def s_filtresiz(sem):
        with np.errstate(invalid="ignore"):
            m = sem.gun_ilk & (sem.d_c >= sem.d_maks20) & (sem.d_atr > 0)
        return m, np.ones(sem.n, np.int8), np.ones(sem.n)
    s.append(satir("SMA200 filtresi YOK", kripto, s_filtresiz, cikis))
    # (4) gün sınırı kaydırma: sunucu günü yerine 3 ve 7 saat erken biten gün (ör. UTC günü)
    for kay in (3, 7, 12):
        ev3 = []
        for v in kripto:
            w = replace(v, f=dict(v.f))
            w.f.update(motor._gunluk(v.t + kay * 3600, v.o, v.h, v.l, v.c))
            ev3.append(w)
        s.append(satir(f"gün sınırı {kay} saat kaydırıldı", ev3, deney.s_gunluk_kirilim, cikis))
    # (5) en iyi 5 işlem çıkarılınca (aykırı değere bağımlılık)
    for b, k in bolumler.items():
        d = calis(kripto, deney.s_gunluk_kirilim, cikis, k)
        d5 = d.sort_values("R", ascending=False).iloc[5:]
        print(f"   {b}: en iyi 5 işlem çıkarılınca ort R {d['R'].mean():+.3f} → {d5['R'].mean():+.3f} | "
              f"en büyük kazanç {d['R'].max():.1f}R, en büyük kayıp {d['R'].min():.1f}R | "
              f"çıkışlar: {d['kod'].map(motor.KOD_AD).value_counts(normalize=True).round(2).to_dict()}")
    # (6) ters sinyal: 20 günün DİBİNDE kapanınca alış (anlamsız olmalı)
    def s_ters(sem):
        with np.errstate(invalid="ignore"):
            m = sem.gun_ilk & (sem.d_c <= sem.d_min20) & (sem.d_atr > 0)
        return m, np.ones(sem.n, np.int8), np.ones(sem.n)
    s.append(satir("TERS: 20 günün dibinde alış", kripto, s_ters, cikis))
    print(pd.DataFrame(s).to_string(index=False))
