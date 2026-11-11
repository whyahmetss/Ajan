"""Testler için SAHTE MetaTrader5 modülü: gerçek terminal ve Windows olmadan bot kodunu çalıştırır.
Sabitlerin değerleri gerçek paketle (MetaTrader5 5.0.x) aynıdır. Durum D nesnesinde tutulur;
testler D'yi doğrudan değiştirir (hesap türü, tick, mum, pozisyon, deal, hata enjeksiyonu)."""
from datetime import datetime
from types import SimpleNamespace as NS

import numpy as np

# ------------------------------------------------------------------ sabitler
TIMEFRAME_M1, TIMEFRAME_M5, TIMEFRAME_M15, TIMEFRAME_M30 = 1, 5, 15, 30
TIMEFRAME_H1, TIMEFRAME_H4, TIMEFRAME_D1 = 1 | 0x4000, 4 | 0x4000, 24 | 0x4000
TF_SANIYE = {TIMEFRAME_M1: 60, TIMEFRAME_M5: 300, TIMEFRAME_M15: 900, TIMEFRAME_M30: 1800,
             TIMEFRAME_H1: 3600, TIMEFRAME_H4: 14400, TIMEFRAME_D1: 86400}
ORDER_TYPE_BUY, ORDER_TYPE_SELL = 0, 1
POSITION_TYPE_BUY, POSITION_TYPE_SELL = 0, 1
ORDER_FILLING_FOK, ORDER_FILLING_IOC, ORDER_FILLING_RETURN = 0, 1, 2
ORDER_TIME_GTC = 0
TRADE_ACTION_DEAL, TRADE_ACTION_SLTP = 1, 6
TRADE_RETCODE_REQUOTE, TRADE_RETCODE_DONE, TRADE_RETCODE_DONE_PARTIAL = 10004, 10009, 10010
TRADE_RETCODE_INVALID_STOPS, TRADE_RETCODE_MARKET_CLOSED, TRADE_RETCODE_NO_MONEY = 10016, 10018, 10019
ACCOUNT_TRADE_MODE_DEMO, ACCOUNT_TRADE_MODE_CONTEST, ACCOUNT_TRADE_MODE_REAL = 0, 1, 2
(SYMBOL_TRADE_MODE_DISABLED, SYMBOL_TRADE_MODE_LONGONLY, SYMBOL_TRADE_MODE_SHORTONLY,
 SYMBOL_TRADE_MODE_CLOSEONLY, SYMBOL_TRADE_MODE_FULL) = 0, 1, 2, 3, 4
DEAL_TYPE_BUY, DEAL_TYPE_SELL, DEAL_TYPE_BALANCE, DEAL_TYPE_CREDIT = 0, 1, 2, 3
DEAL_TYPE_CHARGE, DEAL_TYPE_CORRECTION, DEAL_TYPE_BONUS, DEAL_TYPE_COMMISSION = 4, 5, 6, 7
COPY_TICKS_INFO = 1

MUM_DTYPE = np.dtype([("time", "<i8"), ("open", "<f8"), ("high", "<f8"), ("low", "<f8"),
                      ("close", "<f8"), ("tick_volume", "<u8"), ("spread", "<i4"), ("real_volume", "<u8")])


class _Durum:
    def __init__(self):
        self.sifirla()

    def sifirla(self):
        self.baglanabilir = True
        self.terminal_var = True
        self.terminal_bagli = True
        self.hesap = NS(login=123456, server="Sahte-Demo", balance=10000.0, equity=10000.0,
                        margin_free=10000.0, currency="USD", trade_mode=ACCOUNT_TRADE_MODE_DEMO)
        self.semboller, self.tickler, self.mumlar = {}, {}, {}
        self.pozisyonlar, self.dealler = [], []
        self.gonderilen, self.kontrol_edilen, self.secilen = [], [], []
        self.order_check_sonuc = None
        self.order_send_sonuc = None
        self.pozisyon_hatasi = self.deal_hatasi = self.kar_hatasi = self.semboller_hatasi = False
        self.marjin = 0.0
        self._bilet = 1000


D = _Durum()


def sifirla():
    D.sifirla()


# ------------------------------------------------------------------ test yardımcıları
def sembol_ekle(ad, **kw):
    b = dict(name=ad, path=f"Forex\\Majors\\{ad}", point=1e-5, digits=5, trade_tick_size=1e-5,
             trade_tick_value=1.0, trade_tick_value_loss=1.0, trade_contract_size=100000.0,
             volume_min=0.01, volume_step=0.01, volume_max=100.0, trade_stops_level=0,
             trade_freeze_level=0, filling_mode=1, trade_mode=SYMBOL_TRADE_MODE_FULL, swap_mode=1,
             swap_long=-5.0, swap_short=1.0, swap_rollover3days=3, currency_base=ad[:3],
             currency_profit=ad[3:6], spread=10)
    b.update(kw)
    D.semboller[ad] = NS(**b)
    return D.semboller[ad]


def tick_ayarla(ad, bid, ask, zaman):
    D.tickler[ad] = NS(time=int(zaman), bid=float(bid), ask=float(ask), last=0.0, volume=0,
                       time_msc=int(zaman) * 1000)


def mum_ayarla(ad, tf, dizi):
    D.mumlar[(ad, tf)] = dizi


def pozisyon_ekle(**kw):
    D._bilet += 1
    p = dict(ticket=D._bilet, time=0, type=POSITION_TYPE_BUY, magic=260611, volume=0.1,
             price_open=1.1, sl=0.0, tp=0.0, price_current=1.1, profit=0.0, swap=0.0,
             symbol="EURUSD", comment="")
    p.update(kw)
    D.pozisyonlar.append(NS(**p))
    return D.pozisyonlar[-1]


def deal_ekle(**kw):
    d = dict(ticket=len(D.dealler) + 1, time=0, type=DEAL_TYPE_SELL, entry=1, profit=0.0,
             commission=0.0, swap=0.0, fee=0.0, symbol="EURUSD", magic=260611)
    d.update(kw)
    D.dealler.append(NS(**d))


# ------------------------------------------------------------------ API
def initialize(*a, **kw):
    return D.baglanabilir


def shutdown():
    return True


def last_error():
    return (1, "Success")


def account_info():
    return D.hesap


def terminal_info():
    return NS(connected=D.terminal_bagli, trade_allowed=True) if D.terminal_var else None


def symbols_get(*a, **kw):
    return None if D.semboller_hatasi else tuple(D.semboller.values())


def symbol_info(ad):
    return D.semboller.get(ad)


def symbol_info_tick(ad):
    return D.tickler.get(ad)


def symbol_select(ad, etkin=True):
    D.secilen.append(ad)
    return ad in D.semboller


def copy_rates_from_pos(ad, tf, bas, adet):
    r = D.mumlar.get((ad, tf))
    if r is None:
        return None
    son = len(r) - bas
    return r[max(0, son - adet):son].copy()


def _ts(x):
    return x.timestamp() if isinstance(x, datetime) else float(x)


def copy_rates_range(ad, tf, bas, bit):
    r = D.mumlar.get((ad, tf))
    if r is None:
        return None
    return r[(r["time"] >= _ts(bas)) & (r["time"] <= _ts(bit))].copy()


def copy_ticks_range(*a, **kw):
    return None


def positions_get(*a, **kw):
    return None if D.pozisyon_hatasi else tuple(D.pozisyonlar)


def history_deals_get(bas, bit, *a, **kw):
    if D.deal_hatasi:
        return None
    return tuple(d for d in D.dealler if _ts(bas) <= d.time <= _ts(bit))


def order_calc_profit(tip, ad, hacim, acilis, kapanis):
    b = D.semboller.get(ad)
    if D.kar_hatasi or b is None:
        return None
    fark = (kapanis - acilis) if tip == ORDER_TYPE_BUY else (acilis - kapanis)
    return fark / b.trade_tick_size * b.trade_tick_value * hacim


def order_calc_margin(tip, ad, hacim, fiyat):
    return D.marjin


def order_check(istek):
    D.kontrol_edilen.append(istek)
    return D.order_check_sonuc or NS(retcode=0, comment="Done")


def order_send(istek):
    D.gonderilen.append(istek)
    if D.order_send_sonuc is not None:
        return D.order_send_sonuc
    if istek["action"] == TRADE_ACTION_DEAL and istek.get("position"):        # pozisyon kapatma
        for p in list(D.pozisyonlar):
            if p.ticket == istek["position"]:
                D.pozisyonlar.remove(p)
                kar = (istek["price"] - p.price_open) * (1 if p.type == POSITION_TYPE_BUY else -1)
                b = D.semboller.get(p.symbol)
                para = kar / b.trade_tick_size * b.trade_tick_value * p.volume if b else 0.0
                tick = D.tickler.get(p.symbol)
                deal_ekle(time=tick.time if tick else 0, profit=para, symbol=p.symbol, magic=p.magic)
                return NS(retcode=TRADE_RETCODE_DONE, order=p.ticket, price=istek["price"],
                          volume=istek["volume"], comment="Request executed")
        return NS(retcode=10013, order=0, price=0.0, volume=0.0, comment="Position not found")
    if istek["action"] == TRADE_ACTION_DEAL:
        tick = D.tickler.get(istek["symbol"])
        p = pozisyon_ekle(time=tick.time if tick else 0, symbol=istek["symbol"], volume=istek["volume"],
                          type=POSITION_TYPE_BUY if istek["type"] == ORDER_TYPE_BUY else POSITION_TYPE_SELL,
                          price_open=istek["price"], sl=istek["sl"], tp=istek["tp"], magic=istek["magic"],
                          comment=istek.get("comment", ""))
        return NS(retcode=TRADE_RETCODE_DONE, order=p.ticket, price=istek["price"],
                  volume=istek["volume"], comment="Request executed")
    if istek["action"] == TRADE_ACTION_SLTP:
        for p in D.pozisyonlar:
            if p.ticket == istek["position"]:
                p.sl, p.tp = istek["sl"], istek["tp"]
                return NS(retcode=TRADE_RETCODE_DONE, order=0, price=0.0, volume=0.0, comment="Done")
    return NS(retcode=10013, order=0, price=0.0, volume=0.0, comment="Invalid request")
