"""Lot hesabı (farklı semboller, min/max lot, lot adımı, kayan nokta) ve SL/TP yerleşimi."""
import random
from decimal import Decimal

import pytest

from bot import karar, risk
from bot.strateji import adima_yuvarla


def _plan(sembol="EURUSD", yon="AL", giris=1.1, sl=1.0985):
    return {"sembol": sembol, "yon": yon, "giris": giris, "sl": sl, "mesafe": abs(giris - sl)}


def test_eurusd_lot(cfg, mt5):
    mt5.sembol_ekle("EURUSD")                         # 1 lot, 1 pip(0.0001) = 10 USD
    lot, n = risk.lot_hesapla(_plan(giris=1.1, sl=1.0985), cfg)   # 15 pip = 150 USD/lot
    assert lot == 0.33                                # 50/150 = 0.333 -> aşağı 0.33
    assert lot * 150 <= 10000 * 0.005 + 1e-9


def test_jpy_ve_endeks_lotu_hesap_parasinda(cfg, mt5):
    mt5.sembol_ekle("USDJPY", point=0.001, digits=3, trade_tick_size=0.001, trade_tick_value=0.67)
    lot, _ = risk.lot_hesapla(_plan("USDJPY", "SAT", 150.0, 150.3), cfg)    # 300 tick x 0.67 = 201/lot
    assert lot == 0.24 and lot * 201 <= 50
    mt5.sembol_ekle("US30", point=0.01, digits=2, trade_tick_size=1.0, trade_tick_value=1.0,
                    volume_step=0.1, volume_min=0.1)
    lot, _ = risk.lot_hesapla(_plan("US30", "AL", 40000.0, 39960.0), cfg)  # 40 tick = 40/lot
    assert lot == 1.2


def test_lot_adimi_025_risk_asilmaz(cfg, mt5):
    """O7 regresyonu: adım 0.25'te 0.7692 -> 0.75 olmalı (eskiden 0.8: risk aşılıyordu)."""
    mt5.sembol_ekle("US30", point=0.01, digits=2, trade_tick_size=1.0, trade_tick_value=1.0,
                    volume_step=0.25, volume_min=0.25)
    plan = _plan("US30", "AL", 40000.0, 39935.0)      # 65 tick = 65/lot
    lot, _ = risk.lot_hesapla(plan, cfg)
    assert lot == 0.75
    for bakiye in range(2000, 60000, 137):
        mt5.D.hesap.balance = float(bakiye)
        lot, _ = risk.lot_hesapla(plan, cfg)
        izin = bakiye * 0.005
        assert lot * 65 <= izin + 1e-9, (bakiye, lot)
        assert Decimal(repr(lot)) % Decimal("0.25") == 0, (bakiye, lot)


def test_adima_yuvarla_kayan_nokta_ve_rastgele():
    assert adima_yuvarla(0.3 / 0.012, 1, 1000) == 25          # 24.999999999999996 -> 25
    assert adima_yuvarla(0.07, 0.01, 100) == 0.07
    assert adima_yuvarla(5.0, 0.01, 2.5) == 2.5                  # volume_max sınırı
    assert adima_yuvarla(5.0, 0.3, 2.5) == pytest.approx(2.4)    # max adımın katı değilse aşağı
    rnd = random.Random(1)
    for _ in range(3000):
        adim = rnd.choice([0.01, 0.1, 0.25, 0.5, 1.0, 0.05, 0.001])
        ham = rnd.uniform(0, 50)
        lot = adima_yuvarla(ham, adim, 1000)
        assert lot <= ham + 1e-9 * adim
        assert ham - lot < adim + 1e-9
        assert Decimal(repr(lot)) % Decimal(repr(adim)) == 0


def test_min_lot_riskten_buyukse_islem_yok(cfg, mt5):
    mt5.sembol_ekle("BTCUSD", point=0.01, digits=2, trade_tick_size=0.01, trade_tick_value=0.01,
                    volume_min=0.5, volume_step=0.01)
    lot, n = risk.lot_hesapla(_plan("BTCUSD", "AL", 60000.0, 58500.0), cfg)   # 1500/lot, min 0.5=750
    assert lot == 0 and "Min lot" in n


def test_order_calc_profit_yoksa_tick_degeriyle(cfg, mt5):
    mt5.sembol_ekle("EURUSD")
    mt5.D.kar_hatasi = True
    lot, _ = risk.lot_hesapla(_plan(), cfg)
    assert lot == 0.33


def test_marjin_yetersiz_ya_da_hesaplanamazsa_islem_yok(cfg, mt5):
    mt5.sembol_ekle("EURUSD")
    mt5.D.marjin = 6000.0                               # serbest marjinin yarısından fazla
    assert risk.lot_hesapla(_plan(), cfg)[0] == 0
    mt5.D.marjin = None
    assert risk.lot_hesapla(_plan(), cfg)[0] == 0


def test_lot_LLM_den_bagimsiz():
    """S1: lot hesabı LLM çarpanı almaz; yargıç çıktısında boyut/çarpan yok."""
    import inspect
    assert list(inspect.signature(risk.lot_hesapla).parameters) == ["plan", "cfg"]
    assert "YARIM" not in karar.YARGIC_SISTEM and "boyut" not in karar.YARGIC_SISTEM


# ------------------------------------------------------------------ SL / TP yerleşimi
def _aday(yon="AL", atr=0.0010, sembol="EURUSD"):
    return {"sembol": sembol, "yon": yon, "atr": atr, "sinif": "forex"}


def test_alis_plani(cfg, mt5):
    mt5.sembol_ekle("EURUSD")
    mt5.tick_ayarla("EURUSD", 1.10000, 1.10010, 0)
    p = risk.plan_olustur(_aday("AL"), cfg)
    assert p["giris"] == 1.10010                        # alış ask'tan
    assert p["sl"] == pytest.approx(1.10010 - 0.0015)   # 1.5 ATR
    assert p["tp"] == pytest.approx(1.10010 + 0.0030)   # 2R
    # stop olursa kayıp tam 1R (SL bid ile tetiklenir, kayıp = giriş(ask) - SL)
    kayip = -mt5.order_calc_profit(mt5.ORDER_TYPE_BUY, "EURUSD", 1.0, p["giris"], p["sl"])
    assert kayip == pytest.approx(p["mesafe"] / 1e-5)


def test_satis_plani(cfg, mt5):
    mt5.sembol_ekle("EURUSD")
    mt5.tick_ayarla("EURUSD", 1.10000, 1.10010, 0)
    p = risk.plan_olustur(_aday("SAT"), cfg)
    assert p["giris"] == 1.10000                        # satış bid'den
    assert p["sl"] == pytest.approx(1.10150) and p["tp"] == pytest.approx(1.09700)


def test_min_stop_mesafesi_ve_spread(cfg, mt5):
    mt5.sembol_ekle("EURUSD", trade_stops_level=300)
    mt5.tick_ayarla("EURUSD", 1.10000, 1.10010, 0)
    p = risk.plan_olustur(_aday("AL", atr=0.0001), cfg)  # 1.5 ATR = 15 point < (300+5)pt + spread
    assert p["mesafe"] == pytest.approx(305e-5 + 10e-5)


def test_tick_adimina_yuvarlama(cfg, mt5):
    """O8: tick size 0.25 olan endekste SL/TP 0.25'in katı olmalı."""
    mt5.sembol_ekle("US500", point=0.01, digits=2, trade_tick_size=0.25, trade_tick_value=12.5)
    mt5.tick_ayarla("US500", 5000.00, 5000.50, 0)
    p = risk.plan_olustur(_aday("AL", atr=7.33, sembol="US500"), cfg)
    for x in (p["sl"], p["tp"]):
        assert Decimal(repr(x)) % Decimal("0.25") == 0


def test_referans_plan_korunur(cfg, mt5):
    mt5.sembol_ekle("EURUSD")
    mt5.tick_ayarla("EURUSD", 1.10000, 1.10010, 0)
    ilk = risk.plan_olustur(_aday(), cfg)
    mt5.tick_ayarla("EURUSD", 1.10100, 1.10110, 0)
    yeni = risk.plan_olustur(_aday(), cfg, referans=ilk)
    assert yeni["giris"] == 1.10110 and yeni["analiz_giris"] == 1.10010
