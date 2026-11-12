"""Prop firma kural simülasyonu: bilinen senaryolarda doğru sonuç."""
import numpy as np
import pandas as pd
import pytest

from arastirma import prop

GUN = 86400


def _egri(saatlik_dip, saatlik_son, bakiye, bas=0):
    n = len(saatlik_dip)
    return bas + np.arange(n) * 3600, np.asarray(saatlik_dip, float), np.asarray(saatlik_son, float), np.asarray(bakiye, float)


def test_hedefe_ulasma_asgari_islem_gunu_ister():
    iz, dip, son, bak = _egri([0, 5, 11, 11], [0, 5, 11, 11], [0, 5, 11, 11])
    k = prop.Kural("t", hedef=10.0, min_gun=1)
    assert prop.degerlendir(iz, dip, son, bak, np.array([0]), k)[0] == "gecti"
    k = prop.Kural("t", hedef=10.0, min_gun=4)
    assert prop.degerlendir(iz, dip, son, bak, np.array([0]), k)[0] == "bitmedi"


def test_gunluk_limit_yuzen_zarari_sayar():
    # bakiye değişmiyor ama gün içinde özvarlık -5.5'e iniyor
    iz, dip, son, bak = _egri([0, -3, -5.5, -1], [0, -3, -2, -1], [0, 0, 0, 0])
    assert prop.degerlendir(iz, dip, son, bak, np.array([0]), prop.Kural("t"))[0] == "gunluk"
    iz, dip, son, bak = _egri([0, -3, -4.9, -1], [0, -3, -2, -1], [0, 0, 0, 0])
    assert prop.degerlendir(iz, dip, son, bak, np.array([0]), prop.Kural("t"))[0] == "bitmedi"


def test_toplam_limit_statik_ve_takip_eden():
    # her gün -3: günlük limit aşılmaz; 4. gün toplam -12 -> statik ihlal
    bak = np.repeat([-3.0, -6.0, -9.0, -12.0], 24)
    iz, dip, son, b = _egri(bak, bak, bak)
    assert prop.degerlendir(iz, dip, son, b, np.array([0]), prop.Kural("t"))[0] == "toplam"
    # +8'e çıkıp +1'e iniyor: statik limitte sorun yok, takip eden %6 limitte ihlal
    bak = np.repeat([4.0, 8.0, 5.0, 1.0], 24)
    iz, dip, son, b = _egri(bak, bak, bak)
    assert prop.degerlendir(iz, dip, son, b, np.array([0]), prop.Kural("t"))[0] == "bitmedi"
    assert prop.degerlendir(iz, dip, son, b, np.array([0]), prop.Kural("t", toplam=6.0, takip=True))[0] == "toplam"


def test_sure_siniri():
    bak = np.repeat(np.linspace(0, 12, 60), 24)                 # 60 günde +12
    iz, dip, son, b = _egri(bak, bak, bak)
    gunler = np.arange(60)
    assert prop.degerlendir(iz, dip, son, b, gunler, prop.Kural("t", sure_gun=30))[0] == "sure"
    assert prop.degerlendir(iz, dip, son, b, gunler, prop.Kural("t", sure_gun=0))[0] == "gecti"


def test_islem_yolu_ve_egri():
    """Tek alış işlemi: giriş 100, stop mesafesi 2, risk %0.5. Mum dipleri yüzen zarara doğru çevrilir."""
    class S:
        t = np.arange(5) * 3600
        h = np.array([100, 101, 102, 103, 104.0])
        l = np.array([100, 99, 100, 101, 102.0])
        c = np.array([100, 100, 101, 102, 104.0])
        sp = np.zeros(5)
    islem = pd.DataFrame([{"sembol": "X", "i": 0, "k": 3, "yon": 1, "G": 100.0, "M": 2.0, "R": 1.0, "risk_yuzde": 0.5,
                           "kod": 5, "giris_t": 3600, "cikis_t": 3 * 3600}])
    yl = prop.yollar(islem, {"X": S})
    t, kotu, kap, cikis_t, sonuc, _ = yl[0]
    assert list(t) == [3600, 7200, 10800]
    assert kotu == pytest.approx([-0.25, 0.0, 0.25])            # (99-100)/2*0.5, (100-100)/2*0.5, min((101-100)/2*0.5, 0.5)
    assert sonuc == pytest.approx(0.5)
    dip, son, bak = prop.egri(yl, np.array([3600, 7200, 10800]))
    assert bak == pytest.approx([0, 0, 0.5]) and son[-1] == pytest.approx(0.5) and dip[0] == pytest.approx(-0.25)
