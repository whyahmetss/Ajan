"""BACKTEST - stratejinin dürüstlük testi.

Ne test edilir: Tarayıcının kural tabanlı stratejisi (CANLIDA KULLANILAN AYNI FONKSİYON),
stop/hedef/başa baş/takip eden stop kuralları ve gerçek spread.
Ne test EDİLMEZ: LLM ajanları. Çünkü LLM'ler geçmişi eğitimden "biliyor"; geçmişte
test etmek kendini kandırmak olur. LLM katmanının katkısını demo kayıtlarıyla ölçeceğiz.

Kurallar (kendimizi kandırmamak için):
- Sinyal mum KAPANIŞINDA oluşur, giriş BİR SONRAKİ mumun açılışından yapılır.
- Alışta spread girişe, satışta çıkışa eklenir.
- Aynı mumda hem stop hem hedef görülürse STOP sayılır (kötümser varsayım).
- Fiyat stopun ötesinde açılırsa (gap) çıkış açılış fiyatından yapılır.
- Veri ikiye bölünür: ilk %70 "eğitim", son %30 "görülmemiş test". Ayar eğitimde seçilir,
  karar test bölümüne göre verilir.
"""
import logging
import math

import numpy as np
import pandas as pd
import MetaTrader5 as mt5

from . import indikator, mt5_baglanti as mb
from .tarayici import _yon_skoru

log = logging.getLogger("backtest")


def _ust_trend_dizisi(d: pd.DataFrame, ust: pd.DataFrame, zd: str, ust_zd: str) -> np.ndarray:
    """Her alt-zaman-dilimi mumu için, o an KAPANMIŞ son üst-zaman-dilimi mumunun trendi."""
    u = indikator.ekle(ust)
    u["trend"] = np.where((u.ema50 > u.ema200) & (u.close > u.ema50), 1,
                          np.where((u.ema50 < u.ema200) & (u.close < u.ema50), -1, 0))
    u.loc[u.index < 200, "trend"] = 0
    u["hazir"] = u["time"] + pd.Timedelta(minutes=mb.DAKIKA[ust_zd])   # kapanış anı
    alt = pd.DataFrame({"kapanis": d["time"] + pd.Timedelta(minutes=mb.DAKIKA[zd])})
    m = pd.merge_asof(alt.sort_values("kapanis"), u[["hazir", "trend"]].sort_values("hazir"),
                      left_on="kapanis", right_on="hazir", direction="backward")
    return m["trend"].fillna(0).astype(int).to_numpy()


def _islem(d, i, yon, cfg, point, min_mesafe):
    r = cfg["risk"]
    o, h, l, c = d.open.to_numpy(), d.high.to_numpy(), d.low.to_numpy(), d.close.to_numpy()
    sp, atr = d.spread.to_numpy() * point, d.atr.to_numpy()
    j = i + 1
    m = max(r["sl_atr_carpan"] * atr[i], min_mesafe + sp[j])
    if yon > 0:
        giris = o[j] + sp[j]; sl = giris - m; tp = giris + r["tp_r"] * m
    else:
        giris = o[j]; sl = giris + m; tp = giris - r["tp_r"] * m

    for k in range(j, len(d)):
        if yon > 0:
            if k > j and o[k] <= sl:  return (o[k] - giris) / m, k
            if l[k] <= sl:            return (sl - giris) / m, k
            if k > j and o[k] >= tp:  return (o[k] - giris) / m, k
            if h[k] >= tp:            return (tp - giris) / m, k
            kar = c[k] - giris
            if kar >= r["trailing_baslangic_r"] * m:
                sl = max(sl, c[k] - r["trailing_atr_carpan"] * atr[k])
            elif kar >= r["breakeven_r"] * m:
                sl = max(sl, giris + sp[k])
        else:
            ask_o, ask_h, ask_l = o[k] + sp[k], h[k] + sp[k], l[k] + sp[k]
            if k > j and ask_o >= sl: return (giris - ask_o) / m, k
            if ask_h >= sl:           return (giris - sl) / m, k
            if k > j and ask_o <= tp: return (giris - ask_o) / m, k
            if ask_l <= tp:           return (giris - tp) / m, k
            kar = giris - (c[k] + sp[k])
            if kar >= r["trailing_baslangic_r"] * m:
                sl = min(sl, c[k] + sp[k] + r["trailing_atr_carpan"] * atr[k])
            elif kar >= r["breakeven_r"] * m:
                sl = min(sl, giris - sp[k])
    son = len(d) - 1
    return ((c[son] - giris) if yon > 0 else (giris - c[son] - sp[son])) / m, son


def sembol_test(sembol: str, cfg, bar: int, min_skor: int) -> list[dict]:
    zd, ust_zd = cfg["zaman_dilimi"], cfg["ust_zaman_dilimi"]
    df = mb.mumlar(sembol, zd, bar)
    oran = mb.DAKIKA[ust_zd] // mb.DAKIKA[zd]
    ust = mb.mumlar(sembol, ust_zd, bar // max(oran, 1) + 300)
    bilgi = mt5.symbol_info(sembol)
    if df is None or ust is None or bilgi is None or len(df) < 500:
        return []
    d = indikator.ekle(df)
    ut = _ust_trend_dizisi(d, ust, zd, ust_zd)
    min_mesafe = (bilgi.trade_stops_level + 5) * bilgi.point
    max_oran = cfg["tarayici"]["max_spread_atr_orani"]
    islemler, i, n = [], 220, len(d)
    while i < n - 2:
        satir = d.iloc[i]
        if satir.atr <= 0 or (satir.spread * bilgi.point) / satir.atr > max_oran:
            i += 1; continue
        pencere = d.iloc[i - 2:i + 1]
        s_al, _ = _yon_skoru(pencere, +1, ut[i])
        s_sat, _ = _yon_skoru(pencere, -1, ut[i])
        yon, skor = (1, s_al) if s_al >= s_sat else (-1, s_sat)
        if skor < min_skor:
            i += 1; continue
        R, k = _islem(d, i, yon, cfg, bilgi.point, min_mesafe)
        islemler.append({"sembol": sembol, "giris_zaman": d.time.iloc[i + 1], "cikis_zaman": d.time.iloc[k],
                         "yon": "AL" if yon > 0 else "SAT", "skor": skor, "R": round(R, 3),
                         "konum": (i + 1) / n})
        i = k + 1          # aynı sembolde aynı anda tek pozisyon
    return islemler


def metrikler(R: list[float], risk_yuzde: float) -> dict:
    if not R:
        return {"islem": 0}
    a = np.array(R)
    kum = np.cumsum(a)
    dd = float((np.maximum.accumulate(np.concatenate([[0], kum]))[1:] - kum).max())
    kazanc, kayip = a[a > 0].sum(), -a[a < 0].sum()
    return {"islem": len(a), "kazanma_%": round(float(100 * (a > 0).mean()), 1),
            "ort_R": round(float(a.mean()), 3), "toplam_R": round(float(a.sum()), 1),
            "kar_faktoru": round(float(kazanc / kayip), 2) if kayip > 0 else math.inf,
            "max_dusus_R": round(dd, 1),
            "getiri_%": round(float(a.sum() * risk_yuzde), 1),
            "max_dusus_%": round(dd * risk_yuzde, 1)}


def calistir(cfg, semboller: list[dict], bar: int, skorlar: list[int], egitim_orani: float = 0.7):
    risk_yuzde = cfg["risk"]["islem_basi_risk_yuzde"]
    tum = []
    min_s = min(skorlar)
    for s in semboller:
        try:
            isl = sembol_test(s["ad"], cfg, bar, min_s)
            for x in isl:
                x["sinif"] = s["sinif"]
            tum.extend(isl)
            log.info("%-12s %-7s %4d işlem", s["ad"], s["sinif"], len(isl))
        except Exception as ex:
            log.warning("%s test edilemedi: %s", s["ad"], ex)
    if not tum:
        print("Hiç işlem yok. Daha fazla bar ya da daha düşük skor dene.")
        return None
    df = pd.DataFrame(tum)
    df["bolum"] = np.where(df["konum"] <= egitim_orani, "egitim", "TEST")

    print("\n" + "=" * 78)
    print(f" BACKTEST  {cfg['zaman_dilimi']}  | işlem başı risk %{risk_yuzde} | "
          f"eğitim %{int(egitim_orani*100)} / test %{int((1-egitim_orani)*100)}")
    print("=" * 78)
    print("Not: Eşikler aynı işlem listesinden süzülür; daha yüksek eşik = daha az ama seçici işlem.")
    satirlar = []
    for sk in skorlar:
        alt = df[df["skor"] >= sk]
        for b in ("egitim", "TEST"):
            m = metrikler(alt[alt.bolum == b]["R"].tolist(), risk_yuzde)
            satirlar.append({"min_skor": sk, "bolum": b, **m})
    print(pd.DataFrame(satirlar).to_string(index=False))

    print("\nVarlık sınıfına göre (min_skor = %d, sadece TEST bölümü):" % cfg["tarayici"]["min_skor"])
    secili = df[(df.skor >= cfg["tarayici"]["min_skor"]) & (df.bolum == "TEST")]
    for sinif, g in secili.groupby("sinif"):
        print(f"  {sinif:8s}", metrikler(g["R"].tolist(), risk_yuzde))

    print("\nNASIL OKUNUR: Karar TEST satırına göre verilir. TEST'te kar_faktoru < 1.2 veya")
    print("ort_R < 0.05 ise bu strateji canlıya hazır DEĞİL. Eğitim iyi, test kötüyse aşırı uyum var.")
    print("Bu sonuçlar portföy limitlerini, haber filtresini ve LLM katmanını içermez.")
    return df
