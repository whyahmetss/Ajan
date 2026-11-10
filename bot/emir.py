"""4. AŞAMA - EMİR. Botta emir gönderebilen TEK yer burası."""
import logging

import MetaTrader5 as mt5

log = logging.getLogger("emir")


def _doldurma_modu(bilgi):
    fm = bilgi.filling_mode     # bit maskesi: 1=FOK, 2=IOC
    if fm & 1:
        return mt5.ORDER_FILLING_FOK
    if fm & 2:
        return mt5.ORDER_FILLING_IOC
    return mt5.ORDER_FILLING_RETURN


def piyasa_emri(plan: dict, lot: float, cfg, yorum: str = "ajanbot") -> tuple[bool, str]:
    sym = plan["sembol"]
    bilgi = mt5.symbol_info(sym)
    tick = mt5.symbol_info_tick(sym)
    if bilgi is None or tick is None:
        return False, "Sembol bilgisi yok"
    al = plan["yon"] == "AL"
    fiyat = tick.ask if al else tick.bid

    # Plan oluşturulduktan sonra fiyat çok kaydıysa iptal (LLM'ler düşünürken piyasa gitmiş olabilir)
    kayma = abs(fiyat - plan["giris"])
    if kayma > 0.3 * plan["mesafe"]:
        return False, f"Fiyat plandan çok uzaklaştı ({kayma:.{bilgi.digits}f}), emir iptal"

    # SL/TP'yi yeni fiyata göre kaydır ki risk mesafesi aynı kalsın
    fark = fiyat - plan["giris"]
    sl = round(plan["sl"] + fark, bilgi.digits)
    tp = round(plan["tp"] + fark, bilgi.digits)

    istek = {
        "action": mt5.TRADE_ACTION_DEAL, "symbol": sym, "volume": float(lot),
        "type": mt5.ORDER_TYPE_BUY if al else mt5.ORDER_TYPE_SELL,
        "price": fiyat, "sl": sl, "tp": tp,
        "deviation": cfg["risk"]["max_sapma_puan"], "magic": cfg["magic"],
        "comment": yorum[:31], "type_time": mt5.ORDER_TIME_GTC,
        "type_filling": _doldurma_modu(bilgi),
    }
    kontrol = mt5.order_check(istek)
    if kontrol is None or kontrol.retcode not in (0, mt5.TRADE_RETCODE_DONE):
        return False, f"order_check reddetti: {getattr(kontrol, 'comment', mt5.last_error())}"
    sonuc = mt5.order_send(istek)
    if sonuc is None or sonuc.retcode != mt5.TRADE_RETCODE_DONE:
        return False, f"Emir reddedildi: {getattr(sonuc, 'retcode', '?')} {getattr(sonuc, 'comment', mt5.last_error())}"
    return True, f"#{sonuc.order} {plan['yon']} {lot} {sym} @ {sonuc.price} SL {sl} TP {tp}"


def stop_guncelle(pozisyon, yeni_sl: float) -> bool:
    istek = {"action": mt5.TRADE_ACTION_SLTP, "position": pozisyon.ticket,
             "symbol": pozisyon.symbol, "sl": yeni_sl, "tp": pozisyon.tp}
    s = mt5.order_send(istek)
    ok = s is not None and s.retcode == mt5.TRADE_RETCODE_DONE
    if not ok:
        log.warning("Stop güncellenemedi %s: %s", pozisyon.symbol, getattr(s, "comment", mt5.last_error()))
    return ok
