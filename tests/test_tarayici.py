"""Tarayıcı: piyasa açık/kapalı, kapanmış mum, spread, sıralama, UTC mum saatleri ve
CANLI-BACKTEST EŞLİĞİ (aynı mumda aynı yön ve skor)."""
import numpy as np
import pandas as pd
import pytest

from bot import backtest, izleme, mt5_baglanti as mb, risk, strateji, tarayici
from tests.yardimci import STANDART_BILGI, rastgele_mumlar, ust_mumlar


def test_canli_ve_backtest_ayni_fonksiyonlari_kullanir():
    assert tarayici._yon_skoru is strateji.yon_skoru is backtest.yon_skoru
    assert izleme.yeni_stop is strateji.yeni_stop is backtest.yeni_stop
    assert risk.adima_yuvarla is strateji.adima_yuvarla is backtest.adima_yuvarla


def _kur(mt5, ad, h1, h4, j, tick_gecikme=20, spread=0.00012):
    """j. mum oluşmakta: mumlar j'ye kadar (dahil), tick j'nin içinde."""
    mt5.sembol_ekle(ad)
    mt5.mum_ayarla(ad, mt5.TIMEFRAME_H1, h1[: j + 1])
    t = int(h1["time"][j]) + tick_gecikme
    mt5.mum_ayarla(ad, mt5.TIMEFRAME_H4, h4[h4["time"] <= t])
    c = float(h1["close"][j - 1])
    mt5.tick_ayarla(ad, c, c + spread, t)
    return t


@pytest.fixture
def veri():
    h1 = rastgele_mumlar(1300, tohum=11)
    return h1, ust_mumlar(h1)


def test_canli_tarayici_backtest_ile_ayni_sinyali_uretir(cfg, mt5, veri):
    h1, h4 = veri
    cfg["tarayici"].update(min_skor=0, max_spread_atr_orani=99)
    v = backtest.veri_hazirla("EURUSD", "forex", STANDART_BILGI, pd.DataFrame(h1), pd.DataFrame(h4), cfg, onbellek=None)
    farkli, sinyal = [], 0
    for i in range(1100, 1290, 7):
        mt5.sifirla()
        mb._secili.clear()
        t = _kur(mt5, "EURUSD", h1, h4, i + 1)
        adaylar = tarayici.tara(cfg, sunucu_simdi=t)
        assert len(adaylar) == 1 and adaylar[0]["mum_zaman_sn"] == int(h1["time"][i])
        a = adaylar[0]
        canli = (1 if a["yon"] == "AL" else -1, a["skor"])
        sinyal += a["skor"] >= 60
        if canli != (int(v.yon[i]), int(v.skor[i])):
            farkli.append((i, canli, (int(v.yon[i]), int(v.skor[i]))))
    assert farkli == []
    assert sinyal > 0                       # test anlamlı: en az bir gerçek sinyal karşılaştırıldı


def test_kapali_piyasa_ve_eski_mum_taranmaz(cfg, mt5, veri, monkeypatch):
    h1, h4 = veri
    monkeypatch.setattr(tarayici, "_yon_skoru", lambda d, yon, ust: (90, ["test"]))
    t = _kur(mt5, "EURUSD", h1, h4, 1200)
    _kur(mt5, "GBPUSD", h1, h4, 1199)                 # son tick bir önceki mumda: piyasa kapalı
    mt5.mum_ayarla("AUDUSD", mt5.TIMEFRAME_H1, np.delete(h1[:1201], 1199))   # son kapanan mum eksik
    mt5.sembol_ekle("AUDUSD")
    mt5.tick_ayarla("AUDUSD", 1.1, 1.1001, t)
    mt5.mum_ayarla("AUDUSD", mt5.TIMEFRAME_H4, h4[h4["time"] <= t])
    adaylar = tarayici.tara(cfg, sunucu_simdi=t)
    assert [a["sembol"] for a in adaylar] == ["EURUSD"]


def test_spread_filtresi_ve_siralama(cfg, mt5, veri, monkeypatch):
    h1, h4 = veri
    skorlar = {"EURUSD": 70, "GBPUSD": 90, "AUDUSD": 80, "NZDUSD": 75, "USDCAD": 95}
    monkeypatch.setattr(tarayici, "_yon_skoru", lambda d, yon, ust: (skorlar[_kur.son], ["t"]))
    t = None
    for ad in skorlar:
        t = _kur(mt5, ad, h1, h4, 1200, spread=0.05 if ad == "USDCAD" else 0.00012)
    orijinal = mb.mumlar

    def izle(sembol, *a, **k):
        _kur.son = sembol
        return orijinal(sembol, *a, **k)
    monkeypatch.setattr(mb, "mumlar", izle)
    adaylar = tarayici.tara(cfg, sunucu_simdi=t)
    assert [a["sembol"] for a in adaylar] == ["GBPUSD", "AUDUSD", "NZDUSD"]     # USDCAD spread ile elendi


def test_mum_saatleri_UTC(cfg, mt5, veri, monkeypatch):
    h1, h4 = veri
    monkeypatch.setattr(tarayici, "_yon_skoru", lambda d, yon, ust: (90, ["t"]))
    t = _kur(mt5, "EURUSD", h1, h4, 1200)
    mb._saat["ofset"] = 3 * 3600
    a = tarayici.tara(cfg, sunucu_simdi=t)[0]
    son = pd.to_datetime(int(h1["time"][1199]) - 3 * 3600, unit="s").strftime("%m-%d %H:%M")
    assert a["son_mumlar"][-1][0] == son


def test_sunucu_saati_bilinmiyorsa_tarama_yok(cfg, mt5):
    mb._saat["ofset"] = None
    assert tarayici.tara(cfg) == []
