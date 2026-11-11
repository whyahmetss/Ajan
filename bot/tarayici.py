"""1. AŞAMA - TARAYICI
Piyasayı LLM kullanmadan, ücretsiz tarar ve aktif stratejinin (config: strateji.ad) kuralına uyan
adayları döndürür. Böylece yüzlerce sembol için API parası ödemezsin.

Sadece AZ ÖNCE KAPANAN mum analiz edilir ve sadece yeni mumda işlem gören (açık) piyasalar
taranır. Sinyal hesabı bot/strateji.py'dedir; backtest AYNI fonksiyonları ve aynı "ardışık mum"
kuralını kullanır.
"""
import logging

import numpy as np
import pandas as pd
import MetaTrader5 as mt5

from . import indikator, mt5_baglanti as mb, strateji
from .strateji import ust_trend as _ust_trend, yon_skoru as _yon_skoru  # canlı ve backtest AYNI kural

log = logging.getLogger("tarayici")


def tara(cfg, sunucu_simdi: int | None = None) -> list[dict]:
    t = cfg["tarayici"]
    zd = cfg["zaman_dilimi"]
    strateji_ad, sp = strateji.aktif(cfg)
    if mb.ofset_sn() is None and sunucu_simdi is None:
        log.warning("Sunucu saati bilinmiyor (2 dakikadan taze tick yok, piyasalar kapalı olabilir): "
                    "tarama atlandı")
        return []
    simdi = sunucu_simdi if sunucu_simdi is not None else mb.sunucu_simdi()
    bar_bas = mb.mum_baslangici(simdi, zd)                 # şu an oluşmakta olan mum
    beklenen = bar_bas - mb.DAKIKA[zd] * 60               # az önce kapanan mum
    izinli = set(sp.get("siniflar") or [])
    liste = [s for s in mb.semboller(cfg) if not izinli or s["sinif"] in izinli]
    tickler = mb.tickler([s["ad"] for s in liste])
    gunluk = strateji_ad == "gunluk_kirilim"
    mum_sayisi = int(sp.get("gunluk_bar", 6000)) if gunluk else cfg["tarama_bar"]
    adaylar, elenen, kapali, eski = [], 0, 0, 0

    for s in liste:
        ad = s["ad"]
        tick = tickler.get(ad)
        # Piyasa açık mı: yeni mumda en az bir tick olmalı (kapalı piyasaya LLM parası harcanmasın)
        if tick is None or int(tick.time) < bar_bas:
            kapali += 1
            continue
        df = mb.mumlar(ad, zd, mum_sayisi, int(tick.time))
        if df is None or len(df) < t["min_bar"]:
            continue
        # Analiz edilen mum az önce kapanan mum olmalı: seans açılışında / boşluktan sonra
        # eski muma göre giriş yapılmaz (backtest de ardışık olmayan mumda giriş yapmaz)
        if int(df["zaman_sn"].iloc[-1]) != beklenen:
            eski += 1
            continue
        bilgi = mt5.symbol_info(ad)
        if bilgi is None:
            continue
        sinyal = (_gunluk_kirilim(df, sp) if gunluk else _trend_skor(ad, df, cfg, int(tick.time)))
        if sinyal is None:
            continue
        d = indikator.ekle(df.iloc[-cfg["tarama_bar"]:].reset_index(drop=True))     # spread filtresi ve LLM bağlamı
        son = d.iloc[-1]
        spread = tick.ask - tick.bid
        if not son.atr > 0:
            continue
        if spread / son.atr > t["max_spread_atr_orani"]:
            elenen += 1
            continue
        if "ust_trend" not in sinyal:
            sinyal["ust_trend"] = _ust_trend(mb.mumlar(ad, cfg["ust_zaman_dilimi"], cfg["tarama_bar"], int(tick.time)))
        adaylar.append({
            "sembol": ad, "sinif": s["sinif"], "strateji": strateji_ad, **sinyal,
            "mum_zamani": str(son.time), "mum_zaman_sn": int(son.zaman_sn),
            "fiyat": float(son.close), "atr": float(son.atr), "rsi": float(son.rsi),
            "adx": float(son.adx), "ema20": float(son.ema20), "ema50": float(son.ema50),
            "ema200": float(son.ema200), "spread": float(spread), "digits": bilgi.digits,
            "son_mumlar": _ozet_mumlar(d, bilgi.digits, mb.ofset_sn()),
        })

    adaylar.sort(key=lambda a: a["skor"], reverse=True)
    log.info("Strateji %s | taranan: %d sembol | kapalı/tick yok: %d | eski mum: %d | spread ile elenen: %d | aday: %d",
             strateji_ad, len(liste), kapali, eski, elenen, len(adaylar))
    return adaylar[: t["max_aday"]]


def _trend_skor(ad: str, df, cfg, tick_zamani: int) -> dict | None:
    """ESKİ strateji (gerçek veride zararda; sadece karşılaştırma için)."""
    d = indikator.ekle(df)
    ust = _ust_trend(mb.mumlar(ad, cfg["ust_zaman_dilimi"], cfg["tarama_bar"], tick_zamani))
    sk_al, n_al = _yon_skoru(d, +1, ust)
    sk_sat, n_sat = _yon_skoru(d, -1, ust)
    yon, skor, neden = (1, sk_al, n_al) if sk_al >= sk_sat else (-1, sk_sat, n_sat)
    if skor < cfg["tarayici"]["min_skor"]:
        return None
    return {"yon": "AL" if yon > 0 else "SAT", "skor": skor, "nedenler": neden, "ust_trend": ust}


def _gunluk_kirilim(df, sp: dict) -> dict | None:
    """Günlük kırılım momentumu: sinyal sadece sunucu gününün ilk H1 mumu kapandığında oluşur."""
    yon, skor, d_atr = strateji.gunluk_kirilim_dizisi(
        df["zaman_sn"].to_numpy(np.int64), df["open"].to_numpy(float), df["high"].to_numpy(float),
        df["low"].to_numpy(float), df["close"].to_numpy(float), sp)
    if yon[-1] != 1:
        return None
    c = strateji.gunluk_kirilim_cikis(sp)
    hedef = "hedef yok" if c["tp_r"] is None else f"hedef {c['tp_r'] * c['sl_atr']:g} günlük ATR"
    return {"yon": "AL", "skor": round(float(skor[-1]), 1), "gunluk_atr": float(d_atr[-1]),
            "cikis": {"sl_mesafe": c["sl_atr"] * float(d_atr[-1]), "tp_r": c["tp_r"], "max_mum": c["max_mum"]},
            "nedenler": [f"Dünkü günlük kapanış son {sp.get('kirilim_gun', 20)} günün zirvesinde",
                         f"{sp.get('trend_gun', 200)} günlük ortalamanın %{skor[-1] - 100:.0f} üstünde"],
            "strateji_aciklama": (f"Günlük kırılım momentumu (sadece alış): stop {c['sl_atr']:g} günlük ATR, {hedef}, "
                                  f"{c['max_mum']} mum sonra zaman stopu. Kazanma oranı düşük (~%45-50), kâr az "
                                  f"sayıda büyük hareketten gelir; 'geç giriş / uzamış hareket' bu stratejide "
                                  f"kusur değil, kuralın kendisidir.")}


def _ozet_mumlar(d, digits: int, ofset_sn: int | None, n: int = 24) -> list:
    """LLM'e gönderilecek kısa mum özeti (token tasarrufu için). Saatler UTC'ye çevrilir."""
    kaydir = pd.Timedelta(seconds=ofset_sn or 0)
    ek = "" if ofset_sn is not None else " sunucu"
    out = []
    for _, r in d.iloc[-n:].iterrows():
        out.append([(r.time - kaydir).strftime("%m-%d %H:%M") + ek, round(r.open, digits),
                    round(r.high, digits), round(r.low, digits), round(r.close, digits)])
    return out
