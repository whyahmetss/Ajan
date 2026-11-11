"""1. AŞAMA - TARAYICI
Tüm piyasayı (forex, endeks, kripto, emtia) LLM kullanmadan, ücretsiz tarar.
Her sembole 0-100 arası skor verir, en iyi birkaç adayı ajanlara gönderir.
Böylece yüzlerce sembol için API parası ödemezsin.

Sadece AZ ÖNCE KAPANAN mum analiz edilir ve sadece yeni mumda işlem gören (açık) piyasalar
taranır. Backtest aynı _yon_skoru fonksiyonunu ve aynı "ardışık mum" kuralını kullanır.
"""
import logging

import pandas as pd
import MetaTrader5 as mt5

from . import indikator, mt5_baglanti as mb
from .strateji import ust_trend as _ust_trend, yon_skoru as _yon_skoru  # canlı ve backtest AYNI kural

log = logging.getLogger("tarayici")


def tara(cfg, sunucu_simdi: int | None = None) -> list[dict]:
    t = cfg["tarayici"]
    zd, ust_zd = cfg["zaman_dilimi"], cfg["ust_zaman_dilimi"]
    if mb.ofset_sn() is None and sunucu_simdi is None:
        log.warning("Sunucu saati bilinmiyor (2 dakikadan taze tick yok, piyasalar kapalı olabilir): "
                    "tarama atlandı")
        return []
    simdi = sunucu_simdi if sunucu_simdi is not None else mb.sunucu_simdi()
    bar_bas = mb.mum_baslangici(simdi, zd)                 # şu an oluşmakta olan mum
    beklenen = bar_bas - mb.DAKIKA[zd] * 60               # az önce kapanan mum
    liste = mb.semboller(cfg)
    tickler = mb.tickler([s["ad"] for s in liste])
    adaylar, elenen, kapali, eski = [], 0, 0, 0

    for s in liste:
        ad = s["ad"]
        tick = tickler.get(ad)
        # Piyasa açık mı: yeni mumda en az bir tick olmalı (kapalı piyasaya LLM parası harcanmasın)
        if tick is None or int(tick.time) < bar_bas:
            kapali += 1
            continue
        df = mb.mumlar(ad, zd, cfg["tarama_bar"], int(tick.time))
        if df is None or len(df) < t["min_bar"]:
            continue
        # Analiz edilen mum az önce kapanan mum olmalı: seans açılışında / boşluktan sonra
        # eski muma göre giriş yapılmaz (backtest de ardışık olmayan mumda giriş yapmaz)
        if int(df["zaman_sn"].iloc[-1]) != beklenen:
            eski += 1
            continue
        d = indikator.ekle(df)
        son = d.iloc[-1]
        bilgi = mt5.symbol_info(ad)
        if bilgi is None or not son.atr > 0:
            continue
        spread = tick.ask - tick.bid
        if spread / son.atr > t["max_spread_atr_orani"]:
            elenen += 1
            continue

        ust = _ust_trend(mb.mumlar(ad, ust_zd, cfg["tarama_bar"], int(tick.time)))
        sk_al, n_al = _yon_skoru(d, +1, ust)
        sk_sat, n_sat = _yon_skoru(d, -1, ust)
        yon, skor, neden = (1, sk_al, n_al) if sk_al >= sk_sat else (-1, sk_sat, n_sat)
        if skor < t["min_skor"]:
            continue

        adaylar.append({
            "sembol": ad, "sinif": s["sinif"], "yon": "AL" if yon > 0 else "SAT",
            "skor": skor, "nedenler": neden, "ust_trend": ust,
            "mum_zamani": str(son.time), "mum_zaman_sn": int(son.zaman_sn),
            "fiyat": float(son.close), "atr": float(son.atr), "rsi": float(son.rsi),
            "adx": float(son.adx), "ema20": float(son.ema20), "ema50": float(son.ema50),
            "ema200": float(son.ema200), "spread": float(spread), "digits": bilgi.digits,
            "son_mumlar": _ozet_mumlar(d, bilgi.digits, mb.ofset_sn()),
        })

    adaylar.sort(key=lambda a: a["skor"], reverse=True)
    log.info("Taranan: %d sembol | kapalı/tick yok: %d | eski mum: %d | spread ile elenen: %d | aday: %d",
             len(liste), kapali, eski, elenen, len(adaylar))
    return adaylar[: t["max_aday"]]


def _ozet_mumlar(d, digits: int, ofset_sn: int | None, n: int = 24) -> list:
    """LLM'e gönderilecek kısa mum özeti (token tasarrufu için). Saatler UTC'ye çevrilir."""
    kaydir = pd.Timedelta(seconds=ofset_sn or 0)
    ek = "" if ofset_sn is not None else " sunucu"
    out = []
    for _, r in d.iloc[-n:].iterrows():
        out.append([(r.time - kaydir).strftime("%m-%d %H:%M") + ek, round(r.open, digits),
                    round(r.high, digits), round(r.low, digits), round(r.close, digits)])
    return out
