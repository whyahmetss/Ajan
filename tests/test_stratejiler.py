"""Aday strateji modülleri (arastirma/stratejiler): dolum kuralları, eski motorla eşlik ve GELECEK VERİSİ testleri.
Sentetik veri kullanılır; kârlılık hakkında hiçbir şey söylemez."""
import numpy as np
import pandas as pd
import pytest

pytest.importorskip("numba")

from arastirma import motor  # noqa: E402
from arastirma.stratejiler import ADAYLAR, hacim_kirilim, orb, ortak  # noqa: E402
from tests.yardimci import STANDART_BILGI, elle_mumlar, rastgele_mumlar, ust_mumlar  # noqa: E402

BILGI = {**STANDART_BILGI, "swap_mode": 5, "swap_long": -3.0, "swap_short": -2.0, "swap_rollover3days": 3}
MAL = motor.Maliyet(swap=False)
ZD_AD = {"M15": ("EURUSD", "forex", 900), "H1": ("BTCUSD", "kripto", 3600), "H4": ("EURGBP", "forex", 14400)}


def _df(zd, n, tohum=3):
    ad, sinif, periyot = ZD_AD[zd]
    # UTC pazartesi 00:00'dan başlar; M15 için yaz saati geçişini (2024-03-31 Londra) içerecek kadar uzun
    m = rastgele_mumlar(n, periyot=periyot, tohum=tohum, hafta_sonu=(zd == "H1"), oynaklik=0.004 if zd != "M15" else 0.0008)
    df = pd.DataFrame(m)[["time", "open", "high", "low", "close", "spread"]]
    if zd == "H1":
        df["hacim"] = np.random.default_rng(tohum).lognormal(10, 1, n)
    return df


def _seri(zd, df):
    ad, sinif, periyot = ZD_AD[zd]
    return ortak.seri_kur(ad, sinif, BILGI, df, periyot, 50, gunluk=(zd != "M15"))


def _elle(satirlar, spread_pt=0):
    df = pd.DataFrame(elle_mumlar(satirlar, spread_pt=spread_pt))[["time", "open", "high", "low", "close", "spread"]]
    s = ortak.seri_kur("X", "forex", {**STANDART_BILGI, "point": 0.01}, df, 3600, 0, gunluk=False)
    s.f["atr"] = np.full(s.n, 1.0)
    return s


def _tek(s, **kw):
    e = ortak.emirler([0], [kw.pop("yon", 1)], [kw.pop("kson", s.n - 1)], **kw)
    return ortak.simule(s, e, MAL, s.n - 1, kw.get("kay", 0.0)).iloc[0]


# ------------------------------------------------------------------ dolum kuralları
def test_alis_stop_ve_bosluk():
    s = _elle([(100, 100, 100, 100), (100, 101, 99.5, 100), (100, 100.5, 97, 98)])
    r = _tek(s, sl_mesafe=[2.0])                               # giriş 100, stop 98 -> 3. mumda stop
    assert r.R == pytest.approx(-1.0) and r.kod == ortak.SL
    s = _elle([(100, 100, 100, 100), (100, 101, 99.5, 100), (97, 97.5, 96, 97)])
    r = _tek(s, sl_mesafe=[2.0])                               # boşlukla stopun altında açılış: açılıştan dolar
    assert r.R == pytest.approx(-1.5)


def test_ayni_mumda_stop_ve_hedef_stop_sayilir():
    s = _elle([(100, 100, 100, 100), (100, 103, 97, 100)])
    r = _tek(s, sl_mesafe=[2.0], tp_r=[1.0])
    assert r.kod == ortak.SL and r.R == pytest.approx(-1.0)


def test_hedef_ve_zaman_cikisi():
    s = _elle([(100, 100, 100, 100), (100, 101, 99.5, 100.5), (100.5, 102.5, 100, 102)])
    assert _tek(s, sl_mesafe=[2.0], tp_r=[1.0]).R == pytest.approx(1.0)
    r = _tek(s, sl_mesafe=[2.0], kson=1)                       # 2. mumun kapanışında zaman çıkışı
    assert r.kod == ortak.ZAMAN and r.R == pytest.approx(0.25)


def test_satis_spread_ve_mutlak_stop():
    # spread 0.5 (50 point x 0.01). Satış girişi bid 100; stop 102 ask ile tetiklenir: bid tepe 101.6 + 0.5 >= 102
    s = _elle([(100, 100, 100, 100), (100, 100.2, 99, 99.5), (99.5, 101.6, 99.4, 101)], spread_pt=50)
    r = _tek(s, yon=-1, sl_fiyat=[102.0])
    assert r.kod == ortak.SL and r.M == pytest.approx(2.0) and r.R == pytest.approx(-1.0)


def test_kayma_aleyhe_isler():
    s = _elle([(100, 100, 100, 100), (100, 101, 99.5, 100), (100, 100.5, 97, 98)], spread_pt=40)
    e = ortak.emirler([0], [1], [s.n - 1], sl_mesafe=[2.0])
    r0 = ortak.simule(s, e, MAL, s.n - 1, 0.0).iloc[0]
    r1 = ortak.simule(s, e, MAL, s.n - 1, 0.5).iloc[0]
    # giriş 0.5 x 0.4 = 0.2 daha pahalı, stop 0.2 daha kötü -> toplam 0.4 / 2.0 = 0.2R daha kötü
    assert r0.R == pytest.approx(-1.0) and r1.R == pytest.approx(-1.2)


def test_sinyalle_cikis_ve_hareketli_hedef():
    s = _elle([(100, 100, 100, 100), (100, 101, 99.5, 100.5), (100.5, 101, 100, 100.8), (100.8, 102, 100.5, 101.5)])
    cik = np.array([False, False, True, False])
    e = ortak.emirler([0], [1], [3], sl_mesafe=[2.0], cik_al=cik, cik_sat=np.zeros(4, bool))
    r = ortak.simule(s, e, MAL, 3).iloc[0]
    assert r.kod == ortak.SINYAL and r.R == pytest.approx(0.4)
    hedef = np.array([np.nan, 105.0, 105.0, 101.2])
    e = ortak.emirler([0], [1], [3], sl_mesafe=[2.0], hedef=hedef)
    r = ortak.simule(s, e, MAL, 3).iloc[0]
    assert r.kod == ortak.TP and r.R == pytest.approx(0.6) and r.k == 3


def test_takip_eden_stop_sadece_yukari_gider():
    s = _elle([(100, 100, 100, 100), (100, 101, 99.5, 101), (101, 106, 101, 106), (106, 106.5, 102.5, 103), (103, 104, 99, 100)])
    iz_ref = np.array([100.0, 100.0, 101.0, 106.0, 103.0])     # k. mumun açılışında bilinen "dünkü kapanış"
    e = ortak.emirler([0], [1], [10], sl_mesafe=[2.0], iz_ref=iz_ref, iz_mes=np.full(5, 3.0))
    r = ortak.simule(s, e, MAL, 4).iloc[0]
    # 4. mumda (k=3) stop 106-3=103'e çekilir ve aynı mumda 102.5 görülür -> 103'ten çıkış (+1.5R); geri inmez
    assert r.kod == ortak.TRAIL and r.k == 3 and r.R == pytest.approx(1.5)


# ------------------------------------------------------------------ eski motorla eşlik
def test_eski_motorla_ayni_sonuc():
    h1 = rastgele_mumlar(5000, tohum=9, hafta_sonu=False, spread_pt=14)
    sem = motor.sembol_kur("EURUSD", "forex", BILGI, pd.DataFrame(h1), pd.DataFrame(ust_mumlar(h1)), 500)
    s = ortak.seri_kur("EURUSD", "forex", BILGI, pd.DataFrame(h1), 3600, 500)
    rng = np.random.default_rng(1)
    idx = np.sort(rng.choice(np.arange(500, 4900), 400, replace=False))
    yon = rng.choice([-1, 1], len(idx)).astype(np.int8)
    for tp in (motor.SONSUZ, 1.5):
        cikis = motor.Cikis(sl_atr=2.0, tp_r=tp, be_r=motor.SONSUZ, tr_bas=motor.SONSUZ, tr_atr=0.0, max_mum=40)
        mal = motor.Maliyet(spread_carpani=1.5)
        eski = motor.simule(sem, idx, yon, cikis, mal, sem.n - 1)
        e = ortak.emirler(idx, yon, idx + 40, sl_mesafe=2.0 * s.atr[idx], tp_r=tp)
        yeni = ortak.simule(s, e, mal, s.n - 1)
        assert len(eski) == len(yeni) > 300
        np.testing.assert_allclose(eski["R"].to_numpy(), yeni["R"].to_numpy(), rtol=1e-12, atol=1e-12)
        assert (eski["k"].to_numpy() == yeni["k"].to_numpy()).all()


# ------------------------------------------------------------------ gelecek verisi (kesme) testi
@pytest.mark.parametrize("ad", ["K2", "O1", "O2", "V1", "T1", "T2", "M1"])
def test_gelecek_verisi_kullanilmiyor(ad):
    """Sinyaller, verinin t anında kesilmiş haliyle de AYNI çıkmalı (kesimden önceki bütün mumlar için)."""
    mod = ADAYLAR[ad][1]
    n = {"M15": 12000, "H1": 9000, "H4": 3000}[mod.ZD]
    df = _df(mod.ZD, n)
    tam_seri = _seri(mod.ZD, df)
    assert mod.evren(tam_seri)
    prm = {**mod.PRM, "hacim_kat": 0.8} if ad == "V1" else mod.PRM     # sentetik veride yeterli sinyal için
    tam = mod.sinyal(tam_seri, prm)
    assert len(tam) >= 10, "sentetik veride yeterli sinyal yok; test anlamsız olur"
    pay = 100 if mod.ZD == "M15" else 2                       # ORB: seans sonu mumu için bir günlük pay
    rng = np.random.default_rng(0)
    for kes in rng.integers(n // 3, n - 5, 25):
        kisa_seri = _seri(mod.ZD, df.iloc[:kes])
        kisa = mod.sinyal(kisa_seri, prm)
        m_tam, m_kisa = tam.i < kes - pay, kisa.i < kes - pay
        assert (tam.i[m_tam] == kisa.i[m_kisa]).all() if m_tam.sum() == m_kisa.sum() else False
        assert (tam.yon[m_tam] == kisa.yon[m_kisa]).all()
        for alan in ("sl_mesafe", "sl_fiyat", "tp_r", "skor"):
            np.testing.assert_allclose(getattr(tam, alan)[m_tam], getattr(kisa, alan)[m_kisa], rtol=1e-9, equal_nan=True)
        if mod.ZD == "M15":
            assert (tam.kson[m_tam] == kisa.kson[m_kisa]).all()
        for k, dizi in tam.dizi.items():                      # mum başına diziler: kesimden önceki değerler aynı
            np.testing.assert_allclose(np.asarray(dizi[:kes], float), np.asarray(kisa.dizi[k], float), rtol=1e-9, equal_nan=True)


def test_gostergeler_nedensel():
    df = _df("H1", 4000)
    tam = _seri("H1", df)
    kisa = _seri("H1", df.iloc[:3000])
    for k in ("atr", "adx", "d_c", "d_atr", "d_maks20", "d_sma200"):
        np.testing.assert_allclose(tam.f[k][:3000], kisa.f[k], rtol=1e-9, equal_nan=True)


def test_hacim_orani_bugunun_hacmini_kullanmaz():
    df = _df("H1", 2000)
    s1 = _seri("H1", df)
    df2 = df.copy()
    son_gun = df2["time"] // 86400 == df2["time"].iloc[-1] // 86400
    df2.loc[son_gun, "hacim"] *= 100                           # bugünün hacmini değiştir
    s2 = _seri("H1", df2)
    np.testing.assert_allclose(hacim_kirilim.gunluk_hacim_orani(s1, 20), hacim_kirilim.gunluk_hacim_orani(s2, 20), equal_nan=True)


# ------------------------------------------------------------------ ORB kuralları
def _orb_gunu(tarih, bas_fiyat=1.1000, kir=None):
    """Londra saatine göre 07:00-17:00 arası M15 mumları (UTC zaman). kir: (dakika, yön) -> o mum aralık dışında kapanır."""
    yerel = pd.date_range(f"{tarih} 07:00", f"{tarih} 16:45", freq="15min", tz="Europe/London")
    t = (yerel.tz_convert("UTC").tz_localize(None) - pd.Timestamp("1970-01-01")) // pd.Timedelta(seconds=1)
    o = np.full(len(t), bas_fiyat)
    h, l, c = o + 0.0005, o - 0.0005, o.copy()
    dk = yerel.hour * 60 + yerel.minute
    if kir:
        i = int(np.flatnonzero(dk == kir[0])[0])
        c[i] = bas_fiyat + kir[1] * 0.0010
        h[i], l[i] = max(h[i], c[i]), min(l[i], c[i])
        o[i + 1:] = c[i]; c[i + 1:] = c[i]; h[i + 1:] = c[i] + 0.0002; l[i + 1:] = c[i] - 0.0002
    return pd.DataFrame({"time": t, "open": o, "high": h, "low": l, "close": c, "spread": 10})


@pytest.mark.parametrize("tarih", ["2024-01-15", "2024-07-15"])      # kış ve yaz saati
def test_orb_araligi_ve_tetik(tarih):
    df = _orb_gunu(tarih, kir=(9 * 60, 1))                      # 09:00 Londra mumu aralığın üstünde kapanır
    s = ortak.seri_kur("EURUSD", "forex", BILGI, df, 900, 0, gunluk=False)
    e = orb.O1.sinyal(s, orb.O1.PRM)
    assert len(e) == 1 and e.yon[0] == 1
    yerel = pd.to_datetime(s.t, unit="s", utc=True).tz_convert("Europe/London")
    assert (yerel[e.i[0]].hour, yerel[e.i[0]].minute) == (9, 0)
    assert e.sl_fiyat[0] == pytest.approx(1.1000 - 0.0005)      # stop: aralığın dibi
    assert (yerel[e.kson[0]].hour, yerel[e.kson[0]].minute) == (16, 15)   # 16:30'da kapanan son mum


def test_orb_pencere_disi_ve_fitil_tetiklemez():
    df = _orb_gunu("2024-01-15", kir=(11 * 60 + 15, 1))         # açılıştan 3 saat sonra kapanan mum: pencere dışı
    s = ortak.seri_kur("EURUSD", "forex", BILGI, df, 900, 0, gunluk=False)
    assert len(orb.O1.sinyal(s, orb.O1.PRM)) == 0
    df = _orb_gunu("2024-01-15")
    df.loc[6, "high"] = 1.1050                                  # fitil aralığı aşar ama kapanış içeride
    s = ortak.seri_kur("EURUSD", "forex", BILGI, df, 900, 0, gunluk=False)
    assert len(orb.O1.sinyal(s, orb.O1.PRM)) == 0


def test_orb_eksik_aralik_mumu_gunu_atlar():
    df = _orb_gunu("2024-01-15", kir=(9 * 60, -1))
    df = df.drop(index=5).reset_index(drop=True)                # 08:15 mumu yok
    s = ortak.seri_kur("EURUSD", "forex", BILGI, df, 900, 0, gunluk=False)
    assert len(orb.O1.sinyal(s, orb.O1.PRM)) == 0
