"""Haber takvimi: döviz eşleşmesi, blackout, takvim alınamazsa / bayatsa güvenli taraf."""
import time
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace as NS

from bot import haber


def test_sembol_dovizleri():
    assert haber.sembol_dovizleri("EURUSD.m", "forex") == {"EUR", "USD"}
    assert haber.sembol_dovizleri("XAUUSD", "emtia") == {"XAU", "USD"}
    assert haber.sembol_dovizleri("GER40", "endeks") == {"EUR"}
    assert haber.sembol_dovizleri("BTCUSD", "kripto") == {"USD"}
    assert haber.sembol_dovizleri("USOIL", "emtia") == {"USD"}


def test_blackout_sinirlar():
    simdi = datetime.now(timezone.utc)
    olaylar = [{"baslik": "CPI", "doviz": "USD", "zaman": simdi + timedelta(minutes=29), "etki": "High"}]
    assert haber.blackout_var_mi(olaylar, {"USD"}, 30)
    assert haber.blackout_var_mi(olaylar, {"EUR"}, 30) is None
    assert haber.blackout_var_mi(olaylar, {"USD"}, 20) is None


def _cevap(veri):
    return NS(raise_for_status=lambda: None, json=lambda: veri)


def test_takvim_basarili_ve_saat_dilimi(monkeypatch):
    veri = [{"title": "NFP", "country": "USD", "date": "2026-11-06T08:30:00-05:00", "impact": "High"},
            {"title": "Saatsiz", "country": "USD", "date": "2026-11-06T08:30:00", "impact": "High"}]
    monkeypatch.setattr(haber.requests, "get", lambda *a, **k: _cevap(veri))
    olaylar, ok = haber.takvim({"haber": {"aktif": True, "kaynak": "x"}})
    assert ok and len(olaylar) == 1                       # saat dilimsiz kayıt atlanır
    assert olaylar[0]["zaman"] == datetime(2026, 11, 6, 13, 30, tzinfo=timezone.utc)


def test_takvim_alinamazsa_ok_false(monkeypatch):
    olaylar, ok = haber.takvim({"haber": {"aktif": True, "kaynak": "x"}})   # conftest: ağ hata verir
    assert olaylar == [] and ok is False


def test_takvim_bayatsa_ok_false(monkeypatch):
    simdi = time.time()
    haber._onbellek.update(zaman=simdi - 7 * 3600, veri=[{"x": 1}], son_deneme=simdi)
    _, ok = haber.takvim({"haber": {"aktif": True, "kaynak": "x", "max_bayatlik_saat": 6}})
    assert ok is False
    haber._onbellek.update(zaman=simdi - 3 * 3600, son_deneme=simdi)
    assert haber.takvim({"haber": {"aktif": True, "kaynak": "x"}})[1] is True


def test_takvim_onceki_haftadan_kaldiysa_ok_false(monkeypatch):
    pazar = (time.time() // 86400 + 4) // 7 * 7 * 86400 - 4 * 86400      # bu haftanın Pazar 00:00 UTC
    monkeypatch.setattr(haber.time, "time", lambda: pazar + 3600)
    haber._onbellek.update(zaman=pazar - 3600, veri=[{"x": 1}], son_deneme=pazar + 3600)
    assert haber.takvim({"haber": {"aktif": True, "kaynak": "x"}})[1] is False


def test_haber_kapaliysa_takvim_bos_ve_ok():
    assert haber.takvim({"haber": {"aktif": False}}) == ([], True)
