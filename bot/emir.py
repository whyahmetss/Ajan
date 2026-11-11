"""4. AŞAMA - EMİR. Botta emir gönderebilen TEK yer burası.
Her order_send'den hemen önce hesabın DEMO olduğu kontrol edilir (config'den bağımsız, kapatılamaz)."""
import logging
import math

import MetaTrader5 as mt5

from . import mt5_baglanti as mb

log = logging.getLogger("emir")


def _demo_degilse_reddet() -> str | None:
    if not mb.hesap_demo_mu():
        log.critical(mb.GERCEK_HESAP_MESAJI)
        return "GÜVENLİK: hesap demo değil (ya da hesap bilgisi alınamadı), emir gönderilmedi"
    return None


def _doldurma_modu(bilgi):
    fm = bilgi.filling_mode     # bit maskesi: 1=FOK, 2=IOC (SYMBOL_FILLING_*)
    if fm & 1:
        return mt5.ORDER_FILLING_FOK
    if fm & 2:
        return mt5.ORDER_FILLING_IOC
    return mt5.ORDER_FILLING_RETURN


def sapma_puani(plan: dict, bilgi, cfg) -> int:
    """Emirde izin verilen fiyat sapması (point): stop mesafesinin max_sapma_r katı,
    en az max_sapma_puan. Sabit 20 point JPY'de 2 pip, BTC'de 0.2$ ediyordu."""
    r = cfg["risk"]
    return max(int(r["max_sapma_puan"]), math.ceil(r.get("max_sapma_r", 0.05) * plan["mesafe"] / bilgi.point))


def piyasa_emri(plan: dict, lot: float, cfg, yorum: str = "ajanbot") -> tuple[bool, str]:
    sym = plan["sembol"]
    bilgi = mt5.symbol_info(sym)
    tick = mt5.symbol_info_tick(sym)
    if bilgi is None or tick is None:
        return False, "Sembol bilgisi yok"
    al = plan["yon"] == "AL"
    fiyat = tick.ask if al else tick.bid

    # Kayma, LLM'lerin ANALİZ ETTİĞİ fiyata göre ölçülür. Plan sonradan tazelense bile
    # (onay beklerken, LLM düşünürken) referans değişmez.
    ref = plan.get("analiz_giris", plan["giris"])
    ref_m = plan.get("analiz_mesafe", plan["mesafe"])
    izin = cfg["risk"].get("max_kayma_r", 0.3)
    kayma = abs(fiyat - ref)
    if kayma > izin * ref_m:
        return False, (f"Fiyat analizden {kayma / ref_m:.2f}R uzaklaştı (izin {izin}R): "
                       f"{ref:.{bilgi.digits}f} -> {fiyat:.{bilgi.digits}f}, emir iptal")

    # SL/TP'yi yeni fiyata göre kaydır ki risk mesafesi (ve lot hesabı) aynı kalsın
    fark = fiyat - plan["giris"]
    sl = mb.fiyat_normalize(plan["sl"] + fark, bilgi)
    tp = mb.fiyat_normalize(plan["tp"] + fark, bilgi)

    istek = {
        "action": mt5.TRADE_ACTION_DEAL, "symbol": sym, "volume": float(lot),
        "type": mt5.ORDER_TYPE_BUY if al else mt5.ORDER_TYPE_SELL,
        "price": fiyat, "sl": sl, "tp": tp,
        "deviation": sapma_puani(plan, bilgi, cfg), "magic": cfg["magic"],
        "comment": yorum[:31], "type_time": mt5.ORDER_TIME_GTC,
        "type_filling": _doldurma_modu(bilgi),
    }
    kontrol = mt5.order_check(istek)
    if kontrol is None or kontrol.retcode not in (0, mt5.TRADE_RETCODE_DONE):
        return False, (f"order_check reddetti: {getattr(kontrol, 'retcode', '?')} "
                       f"{getattr(kontrol, 'comment', mt5.last_error())}")
    red = _demo_degilse_reddet()
    if red:
        return False, red
    sonuc = mt5.order_send(istek)
    if sonuc is None:
        return False, f"Emir gönderilemedi: {mt5.last_error()}"
    if sonuc.retcode == mt5.TRADE_RETCODE_DONE:
        return True, f"#{sonuc.order} {plan['yon']} {lot} {sym} @ {sonuc.price} SL {sl} TP {tp}"
    if sonuc.retcode == mt5.TRADE_RETCODE_DONE_PARTIAL:
        # Pozisyon AÇILDI ama istenenden küçük (IOC). Risk daha az, yine de kayda geçsin.
        return True, (f"KISMİ DOLUM #{sonuc.order} {plan['yon']} {sonuc.volume}/{lot} {sym} "
                      f"@ {sonuc.price} SL {sl} TP {tp}")
    return False, f"Emir reddedildi: {sonuc.retcode} {sonuc.comment}"


def stop_guncelle(pozisyon, yeni_sl: float) -> bool:
    red = _demo_degilse_reddet()
    if red:
        log.warning("Stop güncellenmedi %s: %s", pozisyon.symbol, red)
        return False
    istek = {"action": mt5.TRADE_ACTION_SLTP, "position": pozisyon.ticket,
             "symbol": pozisyon.symbol, "sl": yeni_sl, "tp": pozisyon.tp, "magic": pozisyon.magic}
    s = mt5.order_send(istek)
    ok = s is not None and s.retcode == mt5.TRADE_RETCODE_DONE
    if not ok:
        log.warning("Stop güncellenemedi %s: %s %s", pozisyon.symbol, getattr(s, "retcode", "?"),
                    getattr(s, "comment", mt5.last_error()))
    return ok
