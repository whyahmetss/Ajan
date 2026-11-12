"""Karar kayıtlarından rapor: hangi ajan ne sıklıkla haklı çıktı, LLM katmanı sonucu iyileştiriyor mu?

kayitlar/kararlar.jsonl'deki her adayın planı (giriş / stop / hedef / zaman stopu) kayıtlıdır. Aday işleme
dönüşmemiş olsa bile (sinyal modu, LLM reddi) plan, sonraki gerçek fiyatlarla sanal olarak oynatılır ve
sonucu R cinsinden hesaplanır. Böylece LLM'in onayladığı ve reddettiği adaylar karşılaştırılabilir.

Sanal sonuç kuralları backtest ile aynıdır: aynı mumda stop ve hedef görülürse stop sayılır; fiyat stopun
ötesinde açılırsa açılıştan çıkılır; zaman stopunda mum kapanışından çıkılır. Başa baş / takip eden stop
sadece trend_skor planlarında uygulanır.
"""
import json
import os
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd

from .strateji import SONSUZ, gunluk_tablo, yeni_stop

OYLAR = ("DESTEK", "KARSI", "NOTR", "HATA")


def kayitlari_oku(klasor) -> list[dict]:
    yol = os.path.join(klasor, "kararlar.jsonl")
    out = []
    if not os.path.exists(yol):
        return out
    with open(yol, encoding="utf-8") as f:
        for satir in f:
            try:
                out.append(json.loads(satir))
            except ValueError:
                continue
    return out


def plan_sonucu(kayit: dict, mumlar: pd.DataFrame, point: float, risk_cfg: dict) -> dict | None:
    """Planı, giriş mumundan itibaren verilen mumlarla oynatır. mumlar: time, open, high, low, close, spread
    (ilk satır giriş mumu). Dönüş: {"R", "cikis", "mum"} ya da None (plan/mum yok)."""
    plan = kayit.get("plan") or {}
    giris, sl = plan.get("giris"), plan.get("sl")
    if not giris or not sl or mumlar is None or len(mumlar) == 0:
        return None
    al = kayit.get("yon") == "AL"
    m = abs(giris - sl)
    if not m > 0:
        return None
    tp = plan.get("tp") or 0.0
    max_mum = int(plan.get("max_mum") or 0)
    eski = kayit.get("strateji", "trend_skor") == "trend_skor"
    kur = ({k: risk_cfg[k] for k in ("breakeven_r", "trailing_baslangic_r", "trailing_atr_carpan")} if eski
           else {"breakeven_r": SONSUZ, "trailing_baslangic_r": SONSUZ, "trailing_atr_carpan": 0.0})
    o, h, l, c = (mumlar[k].to_numpy(float) for k in ("open", "high", "low", "close"))
    sp = mumlar["spread"].to_numpy(float) * point
    tr = np.maximum(h - l, np.maximum(np.abs(h - np.r_[c[0], c[:-1]]), np.abs(l - np.r_[c[0], c[:-1]])))
    atr = pd.Series(tr).ewm(alpha=1 / 14, adjust=False).mean().to_numpy()
    for k in range(len(o)):
        if al:
            if k > 0 and o[k] <= sl:
                return {"R": (o[k] - giris) / m, "cikis": "SL", "mum": k + 1}
            if l[k] <= sl:
                return {"R": (sl - giris) / m, "cikis": "SL", "mum": k + 1}
            if tp and h[k] >= tp:
                return {"R": (max(tp, o[k] if k > 0 else tp) - giris) / m, "cikis": "TP", "mum": k + 1}
        else:
            ao = o[k] + sp[k]
            if k > 0 and ao >= sl:
                return {"R": (giris - ao) / m, "cikis": "SL", "mum": k + 1}
            if h[k] + sp[k] >= sl:
                return {"R": (giris - sl) / m, "cikis": "SL", "mum": k + 1}
            if tp and l[k] + sp[k] <= tp:
                return {"R": (giris - min(tp, ao if k > 0 else tp)) / m, "cikis": "TP", "mum": k + 1}
        if max_mum and k + 1 >= max_mum:
            return {"R": ((c[k] - giris) if al else (giris - c[k] - sp[k])) / m, "cikis": "ZAMAN", "mum": k + 1}
        yeni = yeni_stop(al, giris, m, c[k], sp[k], atr[k], point, kur)
        if yeni is not None and ((al and yeni > sl) or (not al and yeni < sl)):
            sl = yeni
    son = len(o) - 1
    return {"R": ((c[son] - giris) if al else (giris - c[son] - sp[son])) / m, "cikis": "ACIK", "mum": son + 1}


GOLGE_IZ_ATR = 3.0      # gölge çıkış: girişten beri en yüksek günlük kapanış - 3 günlük ATR (araştırma adayı "T2")


def golge_takip_sonucu(kayit: dict, mumlar: pd.DataFrame, gecmis: pd.DataFrame, point: float) -> dict | None:
    """SADECE İZLEME: aynı girişin, zaman stopu yerine günlük kapanışa göre takip eden stopla sonucu ne olurdu?
    Bot bu çıkışı KULLANMAZ; docs/ARASTIRMA_RAPORU_2.md'de kanıtı yetersiz bulunan T2 adayının ileriye dönük
    kaydını tutmak içindir. Kurallar arastirma/stratejiler/takip_cikis.py ile aynıdır (test edilir).
    mumlar: giriş mumundan itibaren H1; gecmis: girişten önceki H1 mumları (günlük ATR için, en az ~60 gün)."""
    plan = kayit.get("plan") or {}
    giris, sl = plan.get("giris"), plan.get("sl")
    if kayit.get("yon") != "AL" or not giris or not sl or mumlar is None or gecmis is None or not len(mumlar) or not len(gecmis):
        return None
    m = giris - sl
    if not m > 0:
        return None
    tum = pd.concat([gecmis[gecmis["time"] < mumlar["time"].iloc[0]], mumlar], ignore_index=True)
    bas = len(tum) - len(mumlar)
    g = gunluk_tablo(*(tum[k].to_numpy() for k in ("time", "open", "high", "low", "close")))
    d_c, d_atr = g["d_c"][bas:], g["d_atr"][bas:]
    o, l, c = (mumlar[k].to_numpy(float) for k in ("open", "low", "close"))
    if not (np.isfinite(d_c[0]) and np.isfinite(d_atr[0])):
        return None
    tepe = d_c[0]
    for k in range(len(o)):
        if k > 0 and np.isfinite(d_c[k]) and np.isfinite(d_atr[k]):
            tepe = max(tepe, d_c[k])
            sl = max(sl, tepe - GOLGE_IZ_ATR * d_atr[k])
        if k > 0 and o[k] <= sl:
            return {"R": (o[k] - giris) / m, "cikis": "SL", "mum": k + 1}
        if l[k] <= sl:
            return {"R": (sl - giris) / m, "cikis": "SL", "mum": k + 1}
    return {"R": (c[-1] - giris) / m, "cikis": "ACIK", "mum": len(o)}


def sonuclari_hesapla(kayitlar: list[dict], mum_getir, cfg, gecmis_getir=None) -> pd.DataFrame:
    """Planı olan her aday için sanal sonuç. mum_getir(sembol, giris_mumu_zamani) -> (mumlar DataFrame, point).
    gecmis_getir(sembol, giris_mumu_zamani) -> girişten önceki H1 mumları; verilirse gunluk_kirilim adayları
    için gölge takip çıkışı (R_golge, cikis_golge) da hesaplanır."""
    periyot = {"M15": 900, "M30": 1800, "H1": 3600, "H4": 14400}[cfg["zaman_dilimi"]]
    satirlar = []
    for k in kayitlar:
        if not (k.get("plan") and k.get("mum_zamani")):
            continue
        giris_t = int(pd.Timestamp(k["mum_zamani"]).timestamp()) + periyot
        try:
            mumlar, point = mum_getir(k["sembol"], giris_t)
        except Exception:
            mumlar, point = None, 0.0
        s = plan_sonucu(k, mumlar, point, cfg["risk"])
        if s is None:
            continue
        satir = {"zaman_utc": k.get("zaman_utc"), "sembol": k["sembol"], "strateji": k.get("strateji", "trend_skor"),
                 "yon": k.get("yon"), "asama": k.get("asama"), "sonuc": k.get("sonuc"), "llm_mod": k.get("llm_mod"),
                 "llm_onay": k.get("llm_onay"), **s}
        if gecmis_getir is not None and satir["strateji"] == "gunluk_kirilim" and cfg["zaman_dilimi"] == "H1":
            try:
                gs = golge_takip_sonucu(k, mumlar, gecmis_getir(k["sembol"], giris_t), point)
            except Exception:
                gs = None
            if gs:
                satir["R_golge"], satir["cikis_golge"] = gs["R"], gs["cikis"]
        for o in k.get("oylar") or []:
            satir[f"oy_{o['ajan']}"] = o["oy"]
        for y in k.get("yargiclar") or []:
            satir[f"yargic_{y['model']}"] = y["karar"]
        satirlar.append(satir)
    return pd.DataFrame(satirlar)


def _ozet(d: pd.DataFrame) -> dict:
    kapali = d[d["cikis"] != "ACIK"]
    if len(kapali) == 0:
        return {"aday": len(d), "sonuclanan": 0}
    R = kapali["R"]
    return {"aday": len(d), "sonuclanan": len(kapali), "kazanma_%": round(100 * float((R > 0).mean()), 1),
            "ort_R": round(float(R.mean()), 3), "toplam_R": round(float(R.sum()), 2)}


def rapor_metni(kayitlar: list[dict], df: pd.DataFrame, gun: int | None = None) -> str:
    """Okunabilir rapor. gun verilirse sadece son `gun` günün kayıtları."""
    baslik = "TÜM KAYITLAR"
    if gun:
        sinir = (datetime.now(timezone.utc) - timedelta(days=gun)).isoformat()
        kayitlar = [k for k in kayitlar if (k.get("zaman_utc") or "") >= sinir]
        df = df[df["zaman_utc"] >= sinir] if len(df) else df
        baslik = f"SON {gun} GÜN"
    out = [f"===== KARAR RAPORU — {baslik} =====", f"Toplam kayıt: {len(kayitlar)}"]
    if kayitlar:
        say = pd.Series([f"{k.get('asama')}/{k.get('sonuc')}" for k in kayitlar]).value_counts()
        out.append("Aşama/sonuç dağılımı: " + ", ".join(f"{a}: {n}" for a, n in say.items()))
        usd = sum(k.get("llm_maliyet_usd") or 0 for k in kayitlar)
        llm_li = sum(1 for k in kayitlar if k.get("oylar"))
        out.append(f"LLM maliyeti: {usd:.4f} USD ({llm_li} aday LLM'e gitti"
                   + (f", aday başına {usd / llm_li:.4f} USD)" if llm_li else ")"))
    if df is None or len(df) == 0:
        out.append("Sanal sonucu hesaplanabilen aday yok (plan kaydı ya da sonraki mum verisi yok).")
        return "\n".join(out)

    out.append("\n-- Kural tabanlı strateji (LLM'den bağımsız): planı olan bütün adayların sanal sonucu --")
    out.append(f"  hepsi: {_ozet(df)}")
    for s, g in df.groupby("strateji"):
        out.append(f"  {s}: {_ozet(g)}")

    if "R_golge" in df:
        gk = df[df["R_golge"].notna()]
        iki = gk[(gk["cikis"] != "ACIK") & (gk["cikis_golge"] != "ACIK")]
        out.append("\n-- Gölge çıkış izlemesi (bot KULLANMIYOR): aynı girişler, zaman stopu yerine takip eden stopla --")
        out.append(f"  ikisi de sonuçlanan aday: {len(iki)} | hâlâ açık gölge işlem: {int((gk['cikis_golge'] == 'ACIK').sum())}")
        if len(iki):
            out.append(f"  mevcut çıkış : kazanma %{100 * (iki['R'] > 0).mean():.0f}, ort {iki['R'].mean():+.3f}R, toplam {iki['R'].sum():+.1f}R")
            out.append(f"  gölge çıkış  : kazanma %{100 * (iki['R_golge'] > 0).mean():.0f}, ort {iki['R_golge'].mean():+.3f}R, "
                       f"toplam {iki['R_golge'].sum():+.1f}R")
        out.append("  Geçmiş veride bu fark kanıtlanamadı (35 işlem). Karar için en az 40-50 sonuçlanmış aday bekle.")

    llm = df[df["llm_onay"].notna()] if "llm_onay" in df else df.iloc[0:0]
    if len(llm):
        out.append("\n-- LLM katmanı sonucu iyileştiriyor mu? (LLM'in onayladığı ve reddettiği adayların sanal sonucu) --")
        out.append(f"  LLM ONAY : {_ozet(llm[llm['llm_onay'] == True])}")            # noqa: E712
        out.append(f"  LLM RED  : {_ozet(llm[llm['llm_onay'] == False])}")           # noqa: E712
        out.append("  Onaylananların ortalaması reddedilenlerden belirgin yüksekse LLM katmanı değer katıyor demektir.")
        out.append("  Az sayıda adayla (30'dan az) bu fark tesadüf olabilir; karar vermek için acele etme.")

    kapali = df[df["cikis"] != "ACIK"]
    ajan_kol = [c for c in df.columns if c.startswith("oy_")]
    if ajan_kol and len(kapali):
        out.append("\n-- Ajanlar: oy verdikleri adayların sanal sonucu --")
        out.append("  'isabet' = (DESTEK deyip kazanan + KARSI deyip kaybeden) / (DESTEK + KARSI oyları)")
        for c in ajan_kol:
            parca = []
            for oy in OYLAR:
                g = kapali[kapali[c] == oy]
                if len(g):
                    parca.append(f"{oy} {len(g)} aday, ort {g['R'].mean():+.2f}R")
            net = kapali[kapali[c].isin(["DESTEK", "KARSI"])]
            dogru = ((net[c] == "DESTEK") & (net["R"] > 0)) | ((net[c] == "KARSI") & (net["R"] <= 0))
            isabet = f"isabet %{100 * dogru.mean():.0f} ({len(net)} oy)" if len(net) else "isabet: yeterli oy yok"
            out.append(f"  {c[3:]:6s} {isabet} | " + "; ".join(parca))
    yargic_kol = [c for c in df.columns if c.startswith("yargic_")]
    if yargic_kol and len(kapali):
        out.append("\n-- Yargıçlar --")
        for c in yargic_kol:
            parca = [f"{kr} {len(g)} aday, ort {g['R'].mean():+.2f}R" for kr, g in kapali.groupby(c)]
            out.append(f"  {c[7:]:7s} " + "; ".join(parca))
    acik = int((df["cikis"] == "ACIK").sum())
    if acik:
        out.append(f"\nNot: {acik} adayın sanal işlemi henüz sonuçlanmadı (ortalamalara dahil değil).")
    return "\n".join(out)
