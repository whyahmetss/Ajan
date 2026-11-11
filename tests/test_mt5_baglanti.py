"""Gerçek hesap kilidi, bağlantı kopması, sunucu saati, kapanmış mum, sınıflandırma, fiyat yuvarlama."""
import time
from types import SimpleNamespace as NS

import pytest

from bot import mt5_baglanti as mb
from tests.yardimci import PAZARTESI, rastgele_mumlar


def test_demo_hesaba_baglanir(mt5):
    assert mb.baglan().login == 123456


@pytest.mark.parametrize("mod", [2, 1])          # REAL, CONTEST
def test_gercek_hesapta_baslamaz(mt5, mod):
    mt5.D.hesap.trade_mode = mod
    with pytest.raises(SystemExit, match="DEMO değil"):
        mb.baglan()


def test_calisirken_gercek_hesaba_gecilirse_durur(mt5):
    mb.baglan()
    mb.demo_kilidi()
    mt5.D.hesap.trade_mode = mt5.ACCOUNT_TRADE_MODE_REAL
    with pytest.raises(SystemExit):
        mb.demo_kilidi()


def test_baglanti_kopmasi(mt5):
    mt5.D.baglanabilir = False
    with pytest.raises(mb.BaglantiHatasi):
        mb.baglan()
    mt5.D.hesap = None
    with pytest.raises(mb.BaglantiHatasi):
        mb.demo_kilidi()
    mt5.D.terminal_bagli = False
    assert mb.baglanti_saglikli() is False
    mt5.D.terminal_var = False
    assert mb.baglanti_saglikli() is False


def test_pozisyon_ve_sembol_hatasi(mt5):
    mt5.D.pozisyon_hatasi = True
    with pytest.raises(mb.MT5Hatasi):
        mb.pozisyonlar()
    mt5.D.semboller_hatasi = True
    with pytest.raises(mb.MT5Hatasi):
        mb.semboller({"piyasalar": {}})


def test_sunucu_ofseti(mt5, tmp_path):
    mb.saat_ayarla({"log_klasoru": str(tmp_path)})
    mt5.sembol_ekle("BTCUSD")
    mt5.tick_ayarla("BTCUSD", 1, 2, time.time() + 3 * 3600 - 40)       # UTC+3, 40 sn önceki tick
    assert mb.ofset_guncelle(["BTCUSD"]) and mb.ofset_sn() == 3 * 3600
    assert abs(mb.sunucu_simdi() - (time.time() + 3 * 3600)) < 2
    mt5.tick_ayarla("BTCUSD", 1, 2, time.time() + 2 * 3600 - 900)      # 15 dk eski tick: güncellenmez
    assert not mb.ofset_guncelle(["BTCUSD"]) and mb.ofset_sn() == 3 * 3600
    mb.saat_ayarla({"log_klasoru": str(tmp_path)})                      # dosyadan geri yüklenir
    assert mb.ofset_sn() == 3 * 3600
    mb.saat_ayarla({"log_klasoru": str(tmp_path), "sunucu_utc_ofset_saat": 2})
    assert mb.ofset_sn() == 7200


def test_kapanmis_mumlar(mt5):
    m = rastgele_mumlar(120)
    mt5.sembol_ekle("EURUSD")
    mt5.mum_ayarla("EURUSD", mt5.TIMEFRAME_H1, m)
    son = int(m["time"][-1])
    df = mb.mumlar("EURUSD", "H1", 100, son + 30)          # son mum oluşuyor: atılır
    assert int(df.zaman_sn.iloc[-1]) == son - 3600 and len(df) == 100
    df = mb.mumlar("EURUSD", "H1", 100, son + 3600 + 5)    # yeni mum başladı (verisi gelmedi): son mum kapandı
    assert int(df.zaman_sn.iloc[-1]) == son
    df = mb.mumlar("EURUSD", "H1", 100, son - 3600 + 10)   # saat geride: oluşan mum asla 'kapanmış' sayılmaz
    assert int(df.zaman_sn.iloc[-1]) == son - 7200


@pytest.mark.parametrize("ad, yol, beklenen", [
    ("US30", "Indices\\Major\\US30", "endeks"),
    ("BTCUSD", "Crypto\\Majors\\BTCUSD", "kripto"),
    ("XAUUSD", "Forex\\Metals\\XAUUSD", "emtia"),
    ("EURUSD", "Forex\\Majors\\EURUSD", "forex"),
    ("NVIDIA", "Stocks\\US\\NVIDIA", None),
    ("XOM", "Stocks\\Energy\\XOM", None),
    ("NVIDIA", "", None),
    ("EURUSD.m", "", "forex"),
    ("XAUUSD", "", "emtia"),
    ("GER40", "", "endeks"),
    ("ETHUSD", "", "kripto"),
    ("ABCDEF", "", None),
])
def test_siniflandirma(cfg, ad, yol, beklenen):
    assert mb.sinif_bul(NS(name=ad, path=yol), cfg["piyasalar"]) == beklenen


def test_islem_kapali_semboller_elenir(cfg, mt5):
    mt5.sembol_ekle("EURUSD")
    mt5.sembol_ekle("GBPUSD", trade_mode=mt5.SYMBOL_TRADE_MODE_CLOSEONLY)
    mt5.sembol_ekle("AUDUSD", trade_mode=mt5.SYMBOL_TRADE_MODE_DISABLED)
    assert [s["ad"] for s in mb.semboller(cfg)] == ["EURUSD"]


@pytest.mark.parametrize("fiyat, ts, digits, beklenen", [
    (40000.13, 0.25, 2, 40000.25), (40000.12, 0.25, 2, 40000.0), (1.123456, 0.00001, 5, 1.12346),
    (5000.0, 1.0, 1, 5000.0), (1.1, 0, 5, 1.1),
])
def test_fiyat_normalize(fiyat, ts, digits, beklenen):
    assert mb.fiyat_normalize(fiyat, NS(trade_tick_size=ts, digits=digits)) == pytest.approx(beklenen)


def test_mum_baslangici_h4_d1():
    t = PAZARTESI + 5 * 3600 + 17
    assert mb.mum_baslangici(t, "H4") == PAZARTESI + 4 * 3600
    assert mb.mum_baslangici(t, "D1") == PAZARTESI
