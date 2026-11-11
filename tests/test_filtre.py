"""Filtre ajanının her kuralı, günlük zarar limiti (deal geçmişi + açık risk), MT5 hatası."""
from datetime import datetime, timedelta, timezone

import pytest

from bot import mt5_baglanti as mb, risk
from tests.yardimci import PAZARTESI

SIMDI = PAZARTESI + 2 * 86400 + 15 * 3600          # Çarşamba 15:00 (sunucu saati)
GUN_BAS = SIMDI // 86400 * 86400


@pytest.fixture
def ortam(mt5, monkeypatch):
    monkeypatch.setattr(mb, "sunucu_simdi", lambda: SIMDI)
    mt5.sembol_ekle("EURUSD")
    mt5.sembol_ekle("GBPUSD")
    mt5.sembol_ekle("AUDUSD")
    mt5.sembol_ekle("GBPJPY", point=0.001, digits=3, trade_tick_size=0.001, trade_tick_value=0.67)
    mt5.tick_ayarla("EURUSD", 1.10000, 1.10010, SIMDI)
    return mt5


def _aday(sembol="EURUSD", yon="AL", sinif="forex"):
    return {"sembol": sembol, "yon": yon, "atr": 0.0010, "sinif": sinif}


def _filtre(cfg, aday=None, olaylar=(), takvim_ok=True):
    aday = aday or _aday()
    plan = risk.plan_olustur(aday, cfg)
    return risk.filtre_ajani(aday, plan, cfg, list(olaylar), {}, takvim_ok)


def test_temiz_aday_gecer(cfg, ortam):
    assert _filtre(cfg) == (True, [])


def test_durdur_dosyasi(cfg, ortam):
    risk.DURDUR_DOSYASI.write_text("")
    ok, s = _filtre(cfg)
    assert not ok and "DURDUR" in s[0]


def test_gunluk_limit_ilk_adaydan_once_kayiplar_sayilir(cfg, ortam):
    """Y1 regresyonu: gün başı, günün ilk adayından değil deal geçmişinden hesaplanır."""
    ortam.deal_ekle(time=GUN_BAS + 3600, profit=-180.0)          # aday yokken -%1.8
    ortam.D.hesap.balance = ortam.D.hesap.equity = 9820.0
    g = risk.gunluk_durum()
    assert g["gun_basi"] == pytest.approx(10000.0) and g["sonuc"] == pytest.approx(-180.0)
    ok, s = _filtre(cfg)                                         # -180 + yeni -49.1 < -200
    assert not ok and "Açık risk dahil" in " ".join(s)
    ortam.deal_ekle(time=GUN_BAS + 7200, profit=-30.0)
    ortam.D.hesap.balance = ortam.D.hesap.equity = 9790.0
    ok, s = _filtre(cfg)
    assert not ok and "Günlük zarar limiti doldu" in s[0]


def test_dunku_deal_ve_para_yatirma_sayilmaz(cfg, ortam):
    ortam.deal_ekle(time=GUN_BAS - 600, profit=-500.0)                       # dün
    ortam.deal_ekle(time=GUN_BAS + 600, type=ortam.DEAL_TYPE_BALANCE, profit=5000.0)
    ortam.deal_ekle(time=GUN_BAS + 900, profit=-20.0, commission=-3.0, swap=-1.0)
    ortam.D.hesap.balance = ortam.D.hesap.equity = 10000.0 - 500 + 5000 - 24
    g = risk.gunluk_durum()
    assert g["gerceklesen"] == pytest.approx(-24.0)
    assert g["gun_basi"] == pytest.approx(9500.0)


def test_acik_stop_riski_dahil(cfg, ortam):
    """S2: gerçekleşen + açık stoplar + yeni işlem riski %2'yi aşamaz."""
    ortam.sembol_ekle("NZDCAD")
    for s in ("GBPUSD", "AUDUSD", "NZDCAD"):
        ortam.pozisyon_ekle(symbol=s, price_open=1.0, sl=0.9950, volume=0.1, tp=1.01)   # stopta -50
    cfg["risk"]["max_ayni_doviz"] = 9
    g = risk.gunluk_durum()
    assert g["en_kotu"] == pytest.approx(-150.0, abs=0.01)
    assert _filtre(cfg)[0]                                       # -150 - 50 = -200: sınırda, izinli
    ortam.deal_ekle(time=GUN_BAS + 60, profit=-10.0)
    ok, s = _filtre(cfg)
    assert not ok and "Açık risk dahil" in s[0]


def test_stopsuz_pozisyon_varsa_islem_yok(cfg, ortam):
    ortam.pozisyon_ekle(symbol="GBPUSD", sl=0.0)
    ok, s = _filtre(cfg)
    assert not ok and "Stopsuz" in s[0]


def test_max_pozisyon_ayni_sembol_ayni_doviz(cfg, ortam):
    cfg["risk"]["max_acik_pozisyon"] = 2
    ortam.pozisyon_ekle(symbol="GBPUSD", sl=1.2, price_open=1.21, tp=1.23)
    ortam.pozisyon_ekle(symbol="AUDUSD", sl=0.6, price_open=0.61, tp=0.63)
    ok, s = _filtre(cfg)
    assert not ok and any("Max açık pozisyon" in x for x in s) and any("USD dövizine" in x for x in s)
    ortam.D.pozisyonlar.clear()
    ortam.pozisyon_ekle(symbol="EURUSD", sl=1.09, price_open=1.1, tp=1.12)
    ok, s = _filtre(cfg)
    assert not ok and "Bu sembolde zaten pozisyon var" in s


def test_baska_doviz_gecer_ve_baska_magic_sayilmaz(cfg, ortam):
    ortam.pozisyon_ekle(symbol="GBPUSD", sl=1.2095, price_open=1.21, tp=1.23)
    ortam.pozisyon_ekle(symbol="AUDUSD", sl=0.6095, price_open=0.61, tp=0.63, magic=999)
    assert _filtre(cfg)[0]                     # USD'li BOT pozisyonu sadece 1 (magic 999 sayılmaz)


def test_haber_blackout(cfg, ortam):
    cfg["haber"]["aktif"] = True
    simdi = datetime.now(timezone.utc)
    olay = {"baslik": "NFP", "doviz": "USD", "zaman": simdi + timedelta(minutes=10), "etki": "High"}
    ok, s = _filtre(cfg, olaylar=[olay])
    assert not ok and "Haber blackout" in s[0]
    assert _filtre(cfg, olaylar=[{**olay, "doviz": "JPY"}])[0]
    assert _filtre(cfg, olaylar=[{**olay, "etki": "Medium"}])[0]
    assert _filtre(cfg, olaylar=[{**olay, "zaman": simdi + timedelta(minutes=45)}])[0]
    assert not _filtre(cfg, olaylar=[{**olay, "zaman": simdi - timedelta(minutes=29)}])[0]


def test_takvim_yoksa_islem_yok(cfg, ortam):
    cfg["haber"]["aktif"] = True
    ok, s = _filtre(cfg, takvim_ok=False)
    assert not ok and "takvim" in s[0]
    cfg["haber"]["takvim_yoksa_islem_yok"] = False
    assert _filtre(cfg, takvim_ok=False)[0]


def test_spread_stopa_gore_genis(cfg, ortam):
    ortam.tick_ayarla("EURUSD", 1.10000, 1.10060, SIMDI)          # spread 6 pip, stop ~15 pip+spread
    ok, s = _filtre(cfg)
    assert not ok and "Spread" in s[0]


def test_sadece_alis_sembolu(cfg, ortam):
    ortam.D.semboller["EURUSD"].trade_mode = ortam.SYMBOL_TRADE_MODE_LONGONLY
    assert _filtre(cfg)[0]
    ok, s = _filtre(cfg, _aday(yon="SAT"))
    assert not ok and "sadece alış" in s[0]


def test_mt5_pozisyon_hatasi_islem_acmaz(cfg, ortam):
    """O5: positions_get None -> 'pozisyon yok' sanılmaz, işlem açılmaz."""
    ortam.D.pozisyon_hatasi = True
    ok, s = _filtre(cfg)
    assert not ok and "MT5 verisi alınamadı" in s[0]
    with pytest.raises(mb.MT5Hatasi):
        risk.bot_pozisyonlari(cfg["magic"])


def test_deal_gecmisi_alinamazsa_islem_yok(cfg, ortam):
    ortam.D.deal_hatasi = True
    ok, s = _filtre(cfg)
    assert not ok and "MT5 verisi alınamadı" in s[0]


def test_plan_yoksa_islem_yok(cfg, ortam):
    ok, s = risk.filtre_ajani(_aday(), None, cfg, [], {})
    assert not ok and "Plan" in s[-1]
