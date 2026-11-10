"""1. AŞAMA - TARAYICI
Tüm piyasayı (forex, endeks, kripto, emtia) LLM kullanmadan, ücretsiz tarar.
Her sembole 0-100 arası skor verir, en iyi birkaç adayı ajanlara gönderir.
Böylece yüzlerce sembol için API parası ödemezsin.
"""
import logging
import MetaTrader5 as mt5

from . import indikator, mt5_baglanti as mb

log = logging.getLogger("tarayici")


def _yon_skoru(d, yon: int, ust_trend: int) -> tuple[int, list[str]]:
    """yon: +1 alış, -1 satış. Skor ve nedenleri döndürür."""
    son, onceki = d.iloc[-1], d.iloc[-2]
    c = son.close
    skor, neden = 0, []

    trend = (son.ema50 > son.ema200 and c > son.ema50) if yon > 0 else (son.ema50 < son.ema200 and c < son.ema50)
    if trend:
        skor += 30; neden.append("EMA50/200 trend yönünde")
    if ust_trend == yon:
        skor += 15; neden.append("Üst zaman dilimi aynı yönde")
    elif ust_trend == -yon:
        skor -= 20; neden.append("Üst zaman dilimi TERS yönde")

    if son.adx >= 30:
        skor += 20; neden.append(f"Güçlü trend ADX {son.adx:.0f}")
    elif son.adx >= 20:
        skor += 10; neden.append(f"Trend var ADX {son.adx:.0f}")

    # Kurulum A: 20 mumluk kırılım
    kirilim = c > son.dc_ust if yon > 0 else c < son.dc_alt
    # Kurulum B: trend içinde EMA20'ye geri çekilme ve dönüş
    son3 = d.iloc[-3:]
    dokundu = (son3.low <= son3.ema20).any() if yon > 0 else (son3.high >= son3.ema20).any()
    donus = (c > son.ema20 and son.rsi > onceki.rsi) if yon > 0 else (c < son.ema20 and son.rsi < onceki.rsi)
    geri_cekilme = trend and dokundu and donus and 38 <= son.rsi <= 62

    if kirilim:
        skor += 25; neden.append("20 mumluk kırılım")
    if geri_cekilme:
        skor += 25; neden.append("EMA20 geri çekilmesinden dönüş")
    if not (kirilim or geri_cekilme):
        skor -= 25   # kurulum yoksa aday olmasın

    r = son.rsi
    if (yon > 0 and r > 78) or (yon < 0 and r < 22):
        skor -= 15; neden.append(f"RSI aşırı ({r:.0f})")
    elif (yon > 0 and 50 <= r <= 70) or (yon < 0 and 30 <= r <= 50):
        skor += 10; neden.append(f"RSI momentum uygun ({r:.0f})")

    return max(0, min(100, skor)), neden


def _ust_trend(df_ust) -> int:
    if df_ust is None or len(df_ust) < 210:
        return 0
    d = indikator.ekle(df_ust).iloc[-1]
    if d.ema50 > d.ema200 and d.close > d.ema50:
        return 1
    if d.ema50 < d.ema200 and d.close < d.ema50:
        return -1
    return 0


def tara(cfg) -> list[dict]:
    t = cfg["tarayici"]
    zd, ust_zd = cfg["zaman_dilimi"], cfg["ust_zaman_dilimi"]
    liste = mb.semboller(cfg)
    tickler = mb.son_tick_zamanlari([s["ad"] for s in liste])
    sunucu_simdi = max(tickler.values()) if tickler else 0
    adaylar, elenen = [], 0

    for s in liste:
        ad = s["ad"]
        if not mb.piyasa_acik_mi(tickler.get(ad, 0), sunucu_simdi, zd):
            continue
        df = mb.mumlar(ad, zd, cfg["tarama_bar"])
        if df is None or len(df) < t["min_bar"]:
            continue
        d = indikator.ekle(df)
        son = d.iloc[-1]
        bilgi = mt5.symbol_info(ad)
        tick = mt5.symbol_info_tick(ad)
        if bilgi is None or tick is None or son.atr <= 0:
            continue
        spread = tick.ask - tick.bid
        if spread / son.atr > t["max_spread_atr_orani"]:
            elenen += 1
            continue

        ust = _ust_trend(mb.mumlar(ad, ust_zd, cfg["tarama_bar"]))
        sk_al, n_al = _yon_skoru(d, +1, ust)
        sk_sat, n_sat = _yon_skoru(d, -1, ust)
        yon, skor, neden = (1, sk_al, n_al) if sk_al >= sk_sat else (-1, sk_sat, n_sat)
        if skor < t["min_skor"]:
            continue

        adaylar.append({
            "sembol": ad, "sinif": s["sinif"], "yon": "AL" if yon > 0 else "SAT",
            "skor": skor, "nedenler": neden, "ust_trend": ust,
            "fiyat": float(son.close), "atr": float(son.atr), "rsi": float(son.rsi),
            "adx": float(son.adx), "ema20": float(son.ema20), "ema50": float(son.ema50),
            "ema200": float(son.ema200), "spread": float(spread), "digits": bilgi.digits,
            "son_mumlar": _ozet_mumlar(d, bilgi.digits),
        })

    adaylar.sort(key=lambda a: a["skor"], reverse=True)
    log.info("Taranan: %d sembol | spread ile elenen: %d | aday: %d",
             len(liste), elenen, len(adaylar))
    return adaylar[: t["max_aday"]]


def _ozet_mumlar(d, digits: int, n: int = 24) -> list:
    """LLM'e gönderilecek kısa mum özeti (token tasarrufu için)."""
    out = []
    for _, r in d.iloc[-n:].iterrows():
        out.append([str(r.time)[5:16], round(r.open, digits), round(r.high, digits),
                    round(r.low, digits), round(r.close, digits)])
    return out
