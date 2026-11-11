"""Emir: fiyat kayması iptali (analiz fiyatına göre), demo kilidi, emir sonuçları, sapma, doldurma."""
from types import SimpleNamespace as NS

import pytest

from bot import emir, risk


@pytest.fixture
def ortam(mt5):
    mt5.sembol_ekle("EURUSD")
    mt5.tick_ayarla("EURUSD", 1.10000, 1.10010, 1000)
    return mt5


ADAY = {"sembol": "EURUSD", "yon": "AL", "atr": 0.0010, "sinif": "forex"}


def test_kayma_LLM_planina_gore_olculur(cfg, ortam):
    """Y3 regresyonu: plan emirden hemen önce tazelense bile kayma analiz fiyatına göre ölçülür."""
    plan_llm = risk.plan_olustur(ADAY, cfg)                     # LLM'lerin gördüğü: 1.10010
    ortam.tick_ayarla("EURUSD", 1.10120, 1.10130, 1060)         # LLM düşünürken +0.8R
    plan_yeni = risk.plan_olustur(ADAY, cfg, referans=plan_llm)
    ok, mesaj = emir.piyasa_emri(plan_yeni, 0.1, cfg)
    assert not ok and "uzaklaştı" in mesaj and ortam.D.gonderilen == []


def test_kucuk_kaymada_sl_tp_kayar_mesafe_ayni(cfg, ortam):
    plan = risk.plan_olustur(ADAY, cfg)
    ortam.tick_ayarla("EURUSD", 1.10015, 1.10025, 1010)         # +0.1R
    ok, mesaj = emir.piyasa_emri(plan, 0.1, cfg)
    assert ok
    istek = ortam.D.gonderilen[-1]
    assert istek["price"] == 1.10025
    assert istek["price"] - istek["sl"] == pytest.approx(plan["giris"] - plan["sl"], abs=1e-9)
    assert istek["tp"] - istek["price"] == pytest.approx(plan["tp"] - plan["giris"], abs=1e-9)
    assert istek["magic"] == cfg["magic"] and len(istek["comment"]) <= 31


def test_gercek_hesapta_emir_ve_stop_reddedilir(cfg, ortam):
    plan = risk.plan_olustur(ADAY, cfg)
    ortam.D.hesap.trade_mode = ortam.ACCOUNT_TRADE_MODE_REAL
    ok, mesaj = emir.piyasa_emri(plan, 0.1, cfg)
    assert not ok and "GÜVENLİK" in mesaj
    p = ortam.pozisyon_ekle(symbol="EURUSD", sl=1.09, tp=1.12)
    assert emir.stop_guncelle(p, 1.095) is False
    assert ortam.D.gonderilen == [] and p.sl == 1.09


def test_hesap_bilgisi_yoksa_emir_yok(cfg, ortam):
    plan = risk.plan_olustur(ADAY, cfg)
    ortam.D.hesap = None
    assert emir.piyasa_emri(plan, 0.1, cfg)[0] is False
    assert ortam.D.gonderilen == []


def test_contest_hesabi_da_demo_degil(cfg, ortam):
    plan = risk.plan_olustur(ADAY, cfg)
    ortam.D.hesap.trade_mode = ortam.ACCOUNT_TRADE_MODE_CONTEST
    assert emir.piyasa_emri(plan, 0.1, cfg)[0] is False


@pytest.mark.parametrize("sonuc, basari, metin", [
    (NS(retcode=10010, order=7, price=1.1001, volume=0.05, comment="partial"), True, "KISMİ"),
    (NS(retcode=10004, order=0, price=0, volume=0, comment="Requote"), False, "10004"),
    (None, None, None),
])
def test_emir_sonuclari(cfg, ortam, sonuc, basari, metin):
    plan = risk.plan_olustur(ADAY, cfg)
    if sonuc is None:                                          # order_send None döndürürse
        ortam.D.order_send_sonuc = False
        import tests.sahte_mt5 as m
        eski = m.order_send
        m.order_send = lambda istek: None
        try:
            assert emir.piyasa_emri(plan, 0.1, cfg)[0] is False
        finally:
            m.order_send = eski
        return
    ortam.D.order_send_sonuc = sonuc
    ok, mesaj = emir.piyasa_emri(plan, 0.1, cfg)
    assert ok is basari and metin in mesaj


def test_order_check_reddi(cfg, ortam):
    ortam.D.order_check_sonuc = NS(retcode=10019, comment="No money")
    ok, mesaj = emir.piyasa_emri(risk.plan_olustur(ADAY, cfg), 0.1, cfg)
    assert not ok and "No money" in mesaj and ortam.D.gonderilen == []


def test_sapma_stop_mesafesine_gore(cfg, ortam):
    b = ortam.D.semboller["EURUSD"]
    assert emir.sapma_puani({"mesafe": 0.0015}, b, cfg) == 20                   # taban 20 point
    btc = ortam.sembol_ekle("BTCUSD", point=0.01, digits=2)
    assert emir.sapma_puani({"mesafe": 1500.0}, btc, cfg) == 7500               # 0.05R


@pytest.mark.parametrize("maske, beklenen", [(1, 0), (3, 0), (2, 1), (0, 2)])
def test_doldurma_modu(maske, beklenen):
    assert emir._doldurma_modu(NS(filling_mode=maske)) == beklenen
