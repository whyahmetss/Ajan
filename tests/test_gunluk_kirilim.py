"""gunluk_kirilim stratejisi: günlük özellikler (gelecek bilgisi yok), sinyal koşulları, canlı tarayıcı
ile backtest eşliği, hedefsiz plan/emir, zaman stopu, LLM gözlem modu."""
import json
from dataclasses import replace

import numpy as np
import pandas as pd
import pytest

import main
from bot import backtest, emir, izleme, mt5_baglanti as mb, risk, strateji, tarayici
from bot.derin_analiz import DerinAnaliz
from tests.sahte_mt5 import MUM_DTYPE
from tests.yardimci import PAZARTESI, STANDART_BILGI, elle_mumlar, onaylayan_llm, ust_mumlar

H = 3600
PRM = {"kirilim_gun": 20, "trend_gun": 200}


@pytest.fixture(scope="module")
def h1():
    """400 gün, 7/24, istikrarlı yükseliş eğilimli sentetik veri (kırılım sinyalleri oluşsun diye)."""
    n = 9600
    rng = np.random.default_rng(7)
    c = 100.0 * np.exp(np.cumsum(rng.normal(0.0002, 0.003, n)))
    o = np.r_[100.0, c[:-1]]
    m = np.zeros(n, MUM_DTYPE)
    m["time"] = PAZARTESI + np.arange(n) * H
    m["open"], m["close"] = o, c
    m["high"] = np.maximum(o, c) * (1 + np.abs(rng.normal(0, 0.001, n)))
    m["low"] = np.minimum(o, c) * (1 - np.abs(rng.normal(0, 0.001, n)))
    m["tick_volume"], m["spread"] = 100, 2
    return m


def _dizi(h1):
    return (h1["time"].astype(np.int64), h1["open"], h1["high"], h1["low"], h1["close"])


# ------------------------------------------------------------------ özellikler ve sinyal
def test_gunluk_tablo_dunku_degerleri_verir(h1):
    t, o, h, l, c = _dizi(h1)
    g = strateji.gunluk_tablo(t, o, h, l, c, 20, 200)
    gun = t // 86400
    i = 7000
    dun = gun == gun[i] - 1
    assert g["d_c"][i] == c[dun][-1]                                              # dünkü kapanış
    kapanislar = pd.Series(c).groupby(gun).last()
    assert g["d_maks"][i] == kapanislar.loc[gun[i] - 20:gun[i] - 1].max()        # dün dahil son 20 gün
    assert g["d_sma"][i] == pytest.approx(kapanislar.loc[gun[i] - 200:gun[i] - 1].mean())
    assert g["gun_ilk"][i] == (gun[i] != gun[i - 1])
    assert np.isnan(g["d_sma"][: 24 * 199]).all()                                # 200 gün dolmadan trend yok


def test_gunluk_ozellikler_gelecekten_etkilenmez(h1):
    t, o, h, l, c = _dizi(h1)
    a = strateji.gunluk_kirilim_dizisi(t, o, h, l, c, PRM)
    kes = 8000
    kes -= int((t[kes] % 86400) // H)                    # günün ilk mumuna hizala
    carp = np.ones(len(t)); carp[kes:] = 1.5             # bugünden itibaren her şeyi boz
    b = strateji.gunluk_kirilim_dizisi(t, o * carp, h * carp, l * carp, c * carp, PRM)
    bugun_sonu = kes + 24
    for x, y in zip(a, b):
        assert np.array_equal(x[:bugun_sonu], y[:bugun_sonu], equal_nan=True)    # bugünün mumları sadece dünü görür


def test_sinyal_kosullari(h1):
    t, o, h, l, c = _dizi(h1)
    yon, skor, d_atr = strateji.gunluk_kirilim_dizisi(t, o, h, l, c, PRM)
    g = strateji.gunluk_tablo(t, o, h, l, c, 20, 200)
    s = yon == 1
    assert 5 < s.sum() < 200 and set(np.unique(yon)) == {0, 1}                    # sadece alış
    assert g["gun_ilk"][s].all() and (g["d_c"][s] > g["d_sma"][s]).all() and (g["d_c"][s] >= g["d_maks"][s]).all()
    assert (skor[s] > 100).all() and (skor[~s] == 0).all() and (d_atr[s] > 0).all()
    # koşulları sağlayan her gün-ilk mumu sinyaldir (eksik sinyal yok)
    with np.errstate(invalid="ignore"):
        beklenen = g["gun_ilk"] & (g["d_c"] > g["d_sma"]) & (g["d_c"] >= g["d_maks"]) & (g["d_atr"] > 0)
    assert np.array_equal(s, beklenen)


def test_cikis_ayarlari():
    assert strateji.gunluk_kirilim_cikis({}) == {"sl_atr": 2.0, "tp_r": None, "max_mum": 240}
    assert strateji.gunluk_kirilim_cikis({"sl_gunluk_atr": 2.0, "tp_gunluk_atr": 1.0, "max_mum": 120}) == \
        {"sl_atr": 2.0, "tp_r": 0.5, "max_mum": 120}
    with pytest.raises(SystemExit):
        strateji.aktif({"strateji": {"ad": "olmayan"}})


def test_prm_aktif_stratejiye_gore(cfg, cfg_gk):
    p = backtest.Prm.cfgden(cfg_gk)
    assert (p.sl_atr_carpan, p.tp_r, p.max_mum, p.min_skor) == (2.0, strateji.SONSUZ, 240, 0)
    assert p.breakeven_r == p.trailing_baslangic_r == strateji.SONSUZ
    assert backtest.Prm.cfgden(cfg).max_mum == 0 and backtest.Prm.cfgden(cfg).tp_r == 2.0


# ------------------------------------------------------------------ backtest: hedefsiz + zaman stopu
def _sv(satirlar, sinyal_mumu=0, atr=1.0):
    n = len(satirlar)
    t = np.array([PAZARTESI + i * H for i in range(n)], np.int64)
    o, h, l, c = (np.array(x, float) for x in zip(*satirlar))
    yon, skor = np.zeros(n, np.int8), np.zeros(n)
    yon[sinyal_mumu], skor[sinyal_mumu] = 1, 120.0
    ardisik = np.ones(n, bool); ardisik[-1] = False
    return backtest.SembolVerisi("BTCUSD", "kripto", {**STANDART_BILGI, "point": 0.01, "trade_tick_size": 0.01,
                                 "trade_tick_value": 0.01}, t, o, h, l, c, np.zeros(n), np.full(n, 0.5), yon, skor,
                                 ardisik, True, H, 0, atr_stop=np.full(n, atr))


def test_backtest_hedef_yok_zaman_stopunda_kapanir(cfg_gk):
    prm = backtest.Prm.cfgden(cfg_gk, max_mum=3, bakiye=1e9)
    yukselis = [(100, 100, 100, 100), (100, 103, 99.5, 102), (102, 110, 101, 109), (109, 120, 108, 118), (118, 130, 117, 125)]
    tr = backtest.islem_simule(_sv(yukselis), 0, prm)
    assert tr["cikis"] == "ZAMAN" and tr["k"] == 3                    # giriş mumu 1; 1,2,3 = 3 mum
    assert tr["R_brut"] == pytest.approx((118 - 100) / 2.0)           # stop 2 x günlük ATR(1.0) = 2.0; hedef yok
    assert tr["be_k"] is None                                         # başa baş / takip yok


def test_backtest_stop_gunluk_atr_ile(cfg_gk):
    prm = backtest.Prm.cfgden(cfg_gk, bakiye=1e9)
    dusus = [(100, 100, 100, 100), (100, 100.5, 97.9, 98.5), (98.5, 99, 95, 96)]
    tr = backtest.islem_simule(_sv(dusus), 0, prm)
    assert tr["cikis"] == "SL" and tr["R_brut"] == pytest.approx(-1.0) and tr["k"] == 1      # 100 - 2x1.0 = 98


def test_backtest_sadece_sinyal_mumlari_aday(cfg_gk):
    v = _sv([(100, 101, 99, 100)] * 30, sinyal_mumu=5)
    k = backtest.Kesim("x", 0, 10 ** 12, 10 ** 12)
    assert list(backtest._adaylar(v, backtest.Prm.cfgden(cfg_gk, bakiye=1e9), k)) == [5]


def test_veri_hazirla_ve_sinif_filtresi(cfg_gk, h1, tmp_path):
    bilgi = {**STANDART_BILGI, "point": 0.01, "trade_tick_size": 0.01, "trade_tick_value": 0.01}
    v = backtest.veri_hazirla("BTCUSD", "kripto", bilgi, pd.DataFrame(h1), pd.DataFrame(ust_mumlar(h1)), cfg_gk)
    yon, skor, d_atr = strateji.gunluk_kirilim_dizisi(*_dizi(h1), PRM)
    assert np.array_equal(v.yon[1000:], yon[1000:]) and np.array_equal(v.atr_stop, d_atr, equal_nan=True)
    assert (v.yon[:1000] == 0).all()
    meta = []
    for ad, sinif in (("BTCUSD", "kripto"), ("EURUSD", "forex")):
        meta.append(backtest.dosyaya_kaydet(tmp_path, ad, sinif, bilgi, pd.DataFrame(h1), pd.DataFrame(ust_mumlar(h1)), cfg_gk))
    (tmp_path / "semboller.json").write_text(json.dumps({"semboller": meta}), encoding="utf-8")
    assert [x.ad for x in backtest.dosyadan_yukle(tmp_path, cfg_gk)] == ["BTCUSD"]           # forex taranmaz


# ------------------------------------------------------------------ canlı tarayıcı = backtest
def _kur(mt5, ad, h1, j, yol="Crypto\\BTCUSD"):
    mt5.sembol_ekle(ad, path=yol, point=0.01, digits=2, trade_tick_size=0.01, trade_tick_value=0.01)
    mt5.mum_ayarla(ad, mt5.TIMEFRAME_H1, h1[: j + 1])
    h4 = ust_mumlar(h1[: j + 1])
    mt5.mum_ayarla(ad, mt5.TIMEFRAME_H4, h4)
    t = int(h1["time"][j]) + 20
    c = float(h1["close"][j - 1])
    mt5.tick_ayarla(ad, c, c + 0.02, t)
    return t


def test_canli_tarayici_backtest_ile_ayni_sinyali_ve_stopu_uretir(cfg_gk, mt5, h1):
    bilgi = {**STANDART_BILGI, "point": 0.01, "trade_tick_size": 0.01, "trade_tick_value": 0.01}
    v = backtest.veri_hazirla("BTCUSD", "kripto", bilgi, pd.DataFrame(h1), pd.DataFrame(ust_mumlar(h1)), cfg_gk)
    sinyaller = [int(i) for i in np.flatnonzero(v.yon == 1) if 6200 < i < 9590]
    assert len(sinyaller) >= 5
    for i in sinyaller[:8]:
        mt5.sifirla(); mb._secili.clear()
        t = _kur(mt5, "BTCUSD", h1, i + 1)
        adaylar = tarayici.tara(cfg_gk, sunucu_simdi=t)
        assert len(adaylar) == 1, i
        a = adaylar[0]
        assert a["yon"] == "AL" and a["mum_zaman_sn"] == int(h1["time"][i]) and a["strateji"] == "gunluk_kirilim"
        assert a["cikis"]["sl_mesafe"] == pytest.approx(2.0 * v.atr_stop[i], rel=1e-6)
        assert a["cikis"]["tp_r"] is None and a["cikis"]["max_mum"] == 240
        assert a["skor"] == pytest.approx(v.skor[i], abs=0.06)
    sinyalsiz = [i for i in range(6300, 9590, 331) if v.yon[i] == 0][:8]
    for i in sinyalsiz:
        mt5.sifirla(); mb._secili.clear()
        t = _kur(mt5, "BTCUSD", h1, i + 1)
        assert tarayici.tara(cfg_gk, sunucu_simdi=t) == [], i


def test_tarayici_sadece_strateji_siniflarini_tarar(cfg_gk, mt5, h1):
    i = int(np.flatnonzero(strateji.gunluk_kirilim_dizisi(*_dizi(h1), PRM)[0] == 1)[-1])
    t = _kur(mt5, "BTCUSD", h1, i + 1)
    _kur(mt5, "EURUSD", h1, i + 1, yol="Forex\\Majors\\EURUSD")
    assert [a["sembol"] for a in tarayici.tara(cfg_gk, sunucu_simdi=t)] == ["BTCUSD"]


# ------------------------------------------------------------------ plan, emir, uçtan uca
def _aday(mt5, simdi):
    mt5.sembol_ekle("BTCUSD", path="Crypto\\BTCUSD", point=0.01, digits=2, trade_tick_size=0.01, trade_tick_value=0.01)
    mt5.tick_ayarla("BTCUSD", 60000.0, 60010.0, simdi)
    return {"sembol": "BTCUSD", "sinif": "kripto", "strateji": "gunluk_kirilim", "yon": "AL", "skor": 123.4,
            "nedenler": ["test"], "ust_trend": 1, "mum_zamani": "2024-01-03 00:00:00", "mum_zaman_sn": 0,
            "fiyat": 60000.0, "atr": 400.0, "rsi": 60.0, "adx": 30.0, "ema20": 59000.0, "ema50": 58000.0,
            "ema200": 55000.0, "spread": 10.0, "digits": 2, "son_mumlar": [], "gunluk_atr": 2000.0,
            "cikis": {"sl_mesafe": 4000.0, "tp_r": None, "max_mum": 240}, "strateji_aciklama": "günlük kırılım"}


def test_plan_hedefsiz(cfg_gk, mt5):
    a = _aday(mt5, 1000)
    p = risk.plan_olustur(a, cfg_gk)
    assert p["sl"] == 60010.0 - 4000.0 and p["tp"] == 0.0 and p["max_mum"] == 240 and p["rr"] is None
    a["cikis"]["tp_r"] = 0.5
    assert risk.plan_olustur(a, cfg_gk)["tp"] == 60010.0 + 2000.0


@pytest.fixture
def ortam(mt5, monkeypatch):
    simdi = PAZARTESI + 2 * 86400 + 3600
    monkeypatch.setattr(mb, "sunucu_simdi", lambda: simdi)
    mt5.D.hesap.balance = mt5.D.hesap.equity = mt5.D.hesap.margin_free = 100000.0
    main._islenmis.clear()
    return mt5, _aday(mt5, simdi)


def _kayit(cfg):
    with open(f"{cfg['log_klasoru']}/kararlar.jsonl", encoding="utf-8") as f:
        return [json.loads(x) for x in f][-1]


def test_otomatik_modda_hedefsiz_emir_ve_etiket(cfg_gk, ortam):
    mt5, aday = ortam
    cfg_gk["mod"] = "otomatik"
    cfg_gk["llm"]["mod"] = "kapali"
    llm = onaylayan_llm()
    main.aday_isle(aday, cfg_gk, llm, DerinAnaliz(cfg_gk), [], True, {})
    istek = mt5.D.gonderilen[-1]
    assert istek["tp"] == 0.0 and istek["sl"] == 56010.0 and istek["comment"] == "ajanbot gk z240"
    assert istek["volume"] == 0.12                                    # 500$ risk / 4000$ = 0.125 -> 0.12
    assert llm.cagrilar == []                                         # LLM kapalı: hiç çağrı yok
    k = _kayit(cfg_gk)
    assert k["sonuc"] == "ACILDI" and k["strateji"] == "gunluk_kirilim" and k["llm_mod"] == "kapali"


def test_gozlem_modunda_LLM_reddi_islemi_engellemez(cfg_gk, ortam):
    mt5, aday = ortam
    llm = onaylayan_llm(ayi={"oy": "KARSI", "guven": 95, "gerekceler": ["aşırı uzamış hareket"]},
                        **{"yargic:claude": {"karar": "RED", "guven": 90, "ozet": "geç giriş"}})
    main.aday_isle(aday, cfg_gk, llm, DerinAnaliz(cfg_gk), [], True, {})
    k = _kayit(cfg_gk)
    assert k["sonuc"] == "SINYAL" and k["llm_mod"] == "gozlem" and k["llm_onay"] is False
    assert len(k["oylar"]) == 4 and len(k["yargiclar"]) == 2          # gözlemde yargıç her zaman sorulur
    assert "VETO" in k["oy_sonucu"]


def test_veto_modunda_LLM_reddi_engeller(cfg_gk, ortam):
    mt5, aday = ortam
    cfg_gk["llm"]["mod"] = "veto"
    llm = onaylayan_llm(ayi={"oy": "KARSI", "guven": 95, "gerekceler": ["x"]})
    main.aday_isle(aday, cfg_gk, llm, DerinAnaliz(cfg_gk), [], True, {})
    assert _kayit(cfg_gk)["asama"] == "oy" and _kayit(cfg_gk)["sonuc"] == "RED"


def test_gozlem_modunda_LLM_hatasi_engellemez(cfg_gk, ortam):
    mt5, aday = ortam
    from bot.llm import LLM
    main.aday_isle(aday, cfg_gk, LLM(cfg_gk), DerinAnaliz(cfg_gk), [], True, {})          # API anahtarı yok
    k = _kayit(cfg_gk)
    assert k["sonuc"] == "SINYAL" and k["llm_onay"] is False and all(o["oy"] == "HATA" for o in k["oylar"])


def test_filtre_ve_lot_kurallari_yeni_stratejide_de_gecerli(cfg_gk, ortam):
    mt5, aday = ortam
    mt5.D.hesap.balance = mt5.D.hesap.equity = 5000.0                 # 25$ risk < min lot (0.01) riski 40$
    cfg_gk["llm"]["mod"] = "kapali"
    main.aday_isle(aday, cfg_gk, onaylayan_llm(), DerinAnaliz(cfg_gk), [], True, {})
    k = _kayit(cfg_gk)
    assert k["asama"] == "lot" and "Min lot" in k["neden"]


# ------------------------------------------------------------------ izleme: zaman stopu
def _izleme_kur(mt5, n_sonra, yorum="ajanbot gk z5", fiyat=1.1):
    """60 düz mum + giriş mumu (60) + n_sonra mum; tick, giriş mumundan n_sonra mum sonra."""
    satir = [(1.1, 1.1002, 1.0998, 1.1)] * 60 + [(1.1, 1.15, 1.0999, 1.14)] * 12
    mumlar = elle_mumlar(satir, spread_pt=10)
    mt5.sembol_ekle("BTCUSD", path="Crypto\\BTCUSD")
    mt5.mum_ayarla("BTCUSD", mt5.TIMEFRAME_H1, mumlar)
    p = mt5.pozisyon_ekle(symbol="BTCUSD", time=int(mumlar["time"][60]) + 15, price_open=1.1001, sl=1.09, tp=0.0,
                          comment=yorum)
    mt5.tick_ayarla("BTCUSD", fiyat, fiyat + 0.0001, int(mumlar["time"][60]) + n_sonra * H + 20)
    return p


def test_zaman_stopu_tam_mumunda_kapatir(cfg_gk, mt5):
    p = _izleme_kur(mt5, 4)                         # giriş mumu dahil 4 mum kapandı: henüz değil
    izleme.pozisyonlari_yonet(cfg_gk)
    assert mt5.D.pozisyonlar == [p] and mt5.D.gonderilen == []
    assert p.sl == 1.09                             # büyük kâra rağmen stop taşınmadı (başa baş / takip yok)
    mt5.tick_ayarla("BTCUSD", 1.14, 1.1401, mt5.D.tickler["BTCUSD"].time + H)       # 5. mum kapandı
    izleme.pozisyonlari_yonet(cfg_gk)
    assert mt5.D.pozisyonlar == []
    istek = mt5.D.gonderilen[-1]
    assert istek["position"] == p.ticket and istek["type"] == mt5.ORDER_TYPE_SELL and istek["price"] == 1.14
    assert istek["comment"] == "ajanbot zaman 5"


def test_zaman_stopu_etiket_yoksa_stratejiden(cfg_gk, cfg, mt5):
    p = _izleme_kur(mt5, 3, yorum="")
    assert izleme.zaman_stopu(p, cfg_gk) == 240     # broker yorumu sildiyse aktif stratejinin ayarı
    assert izleme.zaman_stopu(p, cfg) == 0          # eski stratejide zaman stopu yok
    p.comment = "ajanbot s75"
    assert izleme.zaman_stopu(p, cfg_gk) == 0       # eski stratejinin pozisyonu
    p.comment = "ajanbot gk z120"
    assert izleme.zaman_stopu(p, cfg) == 120        # etiket her zaman önceliklidir


def test_zaman_stopu_gercek_hesapta_kapatmaz(cfg_gk, mt5):
    p = _izleme_kur(mt5, 6)
    mt5.D.hesap.trade_mode = mt5.ACCOUNT_TRADE_MODE_REAL
    izleme.pozisyonlari_yonet(cfg_gk)
    assert mt5.D.pozisyonlar == [p] and mt5.D.gonderilen == []
    ok, mesaj = emir.pozisyon_kapat(p, cfg_gk, "test")
    assert not ok and "GÜVENLİK" in mesaj


def test_canli_zaman_stopu_backtest_ile_ayni_mumda(cfg_gk, mt5):
    """Backtest k = giriş + max_mum - 1 mumunun kapanışında çıkar; canlı da o mum kapanınca kapatır."""
    yukselis = [(100, 101, 99.9, 100)] + [(100 + i, 101.5 + i, 99.8 + i, 101 + i) for i in range(8)]
    tr = backtest.islem_simule(_sv(yukselis, atr=5.0), 0, backtest.Prm.cfgden(cfg_gk, max_mum=5, bakiye=1e9))
    assert tr["cikis"] == "ZAMAN" and tr["k"] - tr["i"] == 5          # giriş mumu i+1, çıkış i+5
    _izleme_kur(mt5, 5)                              # giriş mumundan itibaren 5 mum kapandı
    izleme.pozisyonlari_yonet(cfg_gk)
    assert mt5.D.pozisyonlar == []


def test_ana_dongu_strateji_sinifinda_sembol_yoksa_baslamaz(cfg_gk, mt5, monkeypatch, tmp_path):
    import logging
    import yaml
    mt5.sembol_ekle("EURUSD")                        # sadece forex var, strateji kripto istiyor
    yol = tmp_path / "c.yaml"
    yol.write_text(yaml.safe_dump(cfg_gk, allow_unicode=True), encoding="utf-8")
    monkeypatch.setattr(main.sys, "argv", ["main.py", "--bir-kez", "--config", str(yol)])
    try:
        with pytest.raises(SystemExit, match="Taranacak sembol bulunamadı"):
            main.main()
    finally:
        for h in list(logging.getLogger().handlers):
            logging.getLogger().removeHandler(h)
            h.close()


def test_replace_ile_hedefli_surum(cfg_gk):
    cfg_gk["strateji"]["gunluk_kirilim"].update(tp_gunluk_atr=1.0, max_mum=120)
    p = backtest.Prm.cfgden(cfg_gk)
    assert p.tp_r == 0.5 and p.max_mum == 120 and replace(p, min_skor=5).min_skor == 5
