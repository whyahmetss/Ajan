"""STRATEJİNİN SAF KURALLARI (MetaTrader5'e bağımlı değil).
Canlı bot (tarayici.py, izleme.py, risk.py) ve backtest (backtest.py) bu fonksiyonların
AYNISINI kullanır; böylece backtest canlıda çalışan kuralı test eder.
- yon_skoru  : 0-100 sinyal skoru
- ust_trend  : üst zaman dilimi trendi
- yeni_stop  : başa baş / takip eden stop
- adima_yuvarla : lotu adımın katına aşağı yuvarlama
"""
from decimal import ROUND_FLOOR, Decimal

import numpy as np

from . import indikator

DAKIKA = {"M1": 1, "M5": 5, "M15": 15, "M30": 30, "H1": 60, "H4": 240, "D1": 1440}
UST_TREND_MIN_BAR = 210


def yon_skoru(d, yon: int, ust_trend: int) -> tuple[int, list[str]]:
    """yon: +1 alış, -1 satış. d: indikatörlü mumlar (en az son 3 mum). Skor ve nedenler."""
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


def skor_dizisi(d, ust_trend) -> tuple:
    """yon_skoru'nun BÜTÜN mumlar için vektörel eşdeğeri (backtest için hızlı sürüm).
    d: indikatörlü mumlar, ust_trend: her mum için +1/0/-1. Dönüş: (yon dizisi, skor dizisi).
    tests/test_strateji.py, her mumda yon_skoru ile birebir aynı sonucu verdiğini doğrular."""
    c, e20, e50, e200 = (d[k].to_numpy(float) for k in ("close", "ema20", "ema50", "ema200"))
    rsi, adx, lo, hi = (d[k].to_numpy(float) for k in ("rsi", "adx", "low", "high"))
    dcu, dcl = d["dc_ust"].to_numpy(float), d["dc_alt"].to_numpy(float)
    ut = np.asarray(ust_trend)
    rsi_once = np.r_[np.nan, rsi[:-1]]

    def son3(x):                                   # son 3 mumdan herhangi biri
        return x | np.r_[False, x[:-1]] | np.r_[False, False, x[:-2]]
    with np.errstate(invalid="ignore"):
        adx_puan = np.where(adx >= 30, 20, np.where(adx >= 20, 10, 0))
        skorlar = []
        for yon in (1, -1):
            if yon > 0:
                trend = (e50 > e200) & (c > e50)
                kirilim = c > dcu
                dokundu, donus = son3(lo <= e20), (c > e20) & (rsi > rsi_once)
                rsi_puan = np.where(rsi > 78, -15, np.where((rsi >= 50) & (rsi <= 70), 10, 0))
            else:
                trend = (e50 < e200) & (c < e50)
                kirilim = c < dcl
                dokundu, donus = son3(hi >= e20), (c < e20) & (rsi < rsi_once)
                rsi_puan = np.where(rsi < 22, -15, np.where((rsi >= 30) & (rsi <= 50), 10, 0))
            geri = trend & dokundu & donus & (rsi >= 38) & (rsi <= 62)
            sk = (30 * trend + np.where(ut == yon, 15, np.where(ut == -yon, -20, 0)) + adx_puan
                  + 25 * kirilim + 25 * geri - 25 * ~(kirilim | geri) + rsi_puan)
            skorlar.append(np.clip(sk, 0, 100))
    al, sat = skorlar
    return np.where(al >= sat, 1, -1).astype(np.int8), np.maximum(al, sat).astype(np.int16)


def trend_degeri(ema50, ema200, close):
    """+1 / -1 / 0 (skaler ya da numpy dizisi)."""
    return np.where((ema50 > ema200) & (close > ema50), 1, np.where((ema50 < ema200) & (close < ema50), -1, 0))


def ust_trend(df_ust) -> int:
    """Kapanmış üst zaman dilimi mumlarından trend. 210 mumdan azsa 0 (yatay)."""
    if df_ust is None or len(df_ust) < UST_TREND_MIN_BAR:
        return 0
    d = indikator.ekle(df_ust).iloc[-1]
    return int(trend_degeri(d.ema50, d.ema200, d.close))


def yeni_stop(al: bool, giris: float, R: float, kapanis: float, spread: float, atr: float,
              point: float, r: dict) -> float | None:
    """Kapanan mumun verisiyle önerilen stop (alış: kapanış; satış: kapanış + mumun spread'i).
    None = değişiklik önerisi yok. Çağıran stopun sadece iyileşmesini sağlamalı."""
    kar = (kapanis - giris) if al else (giris - (kapanis + spread))
    if kar >= r["trailing_baslangic_r"] * R:
        return (kapanis - r["trailing_atr_carpan"] * atr if al
                else kapanis + spread + r["trailing_atr_carpan"] * atr)
    if kar >= r["breakeven_r"] * R:
        tampon = spread + 2 * point          # spread + komisyon payı
        return giris + tampon if al else giris - tampon
    return None


def adima_yuvarla(ham: float, adim: float, ust: float) -> float:
    """Lotu adımın tam katına AŞAĞI yuvarlar (risk asla aşılmaz). Decimal ile hesaplanır:
    0.25 gibi adımlarda da doğru, 0.3/0.012 = 24.999... gibi kayan nokta hatalarına dayanıklı."""
    a = Decimal(repr(float(adim or 0.01)))
    n = (Decimal(repr(float(ham))) / a + Decimal("1e-9")).to_integral_value(rounding=ROUND_FLOOR)
    n_ust = (Decimal(repr(float(ust))) / a).to_integral_value(rounding=ROUND_FLOOR)
    return float(max(min(n, n_ust), 0) * a)
