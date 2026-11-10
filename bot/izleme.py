"""5. AŞAMA - İZLEME: açık pozisyonları yönetir (başa baş stop + takip eden stop).
Stop SADECE kâr yönünde hareket eder, asla geri gitmez."""
import logging

import MetaTrader5 as mt5

from . import emir, indikator, mt5_baglanti as mb, risk

log = logging.getLogger("izleme")


def pozisyonlari_yonet(cfg):
    r = cfg["risk"]
    for p in risk.bot_pozisyonlari(cfg["magic"]):
        bilgi = mt5.symbol_info(p.symbol)
        tick = mt5.symbol_info_tick(p.symbol)
        if bilgi is None or tick is None or p.tp == 0:
            continue
        al = p.type == mt5.POSITION_TYPE_BUY
        # İlk risk mesafesi TP'den geri hesaplanır (TP hiç değişmez): TP = giriş ± tp_r * R
        R = abs(p.tp - p.price_open) / r["tp_r"]
        if R <= 0:
            continue
        fiyat = tick.bid if al else tick.ask
        kar = (fiyat - p.price_open) if al else (p.price_open - fiyat)
        min_mesafe = (bilgi.trade_stops_level + 2) * bilgi.point
        yeni = None

        if kar >= r["trailing_baslangic_r"] * R:
            df = mb.mumlar(p.symbol, cfg["zaman_dilimi"], 100)
            if df is not None:
                a = float(indikator.atr(df).iloc[-1])
                yeni = fiyat - r["trailing_atr_carpan"] * a if al else fiyat + r["trailing_atr_carpan"] * a
        if yeni is None and kar >= r["breakeven_r"] * R:
            tampon = (tick.ask - tick.bid) + 2 * bilgi.point     # spread + komisyonu karşılasın
            yeni = p.price_open + tampon if al else p.price_open - tampon
        if yeni is None:
            continue

        yeni = round(yeni, bilgi.digits)
        # Sadece iyileştir + broker minimum mesafesine uy
        if al:
            if (p.sl and yeni <= p.sl) or fiyat - yeni < min_mesafe:
                continue
        else:
            if (p.sl and yeni >= p.sl) or yeni - fiyat < min_mesafe:
                continue
        if emir.stop_guncelle(p, yeni):
            log.info("%s stop %s -> %s (kâr %.2fR)", p.symbol, p.sl, yeni, kar / R)
