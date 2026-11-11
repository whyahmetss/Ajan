"""Araştırma motoru (numba) ile botun backtest motoru aynı işlem sonuçlarını verir.
numba kurulu değilse atlanır (bot numba'ya ihtiyaç duymaz)."""
from dataclasses import replace

import numpy as np
import pandas as pd
import pytest

pytest.importorskip("numba")

from arastirma import motor  # noqa: E402
from bot import backtest  # noqa: E402
from tests.yardimci import STANDART_BILGI, rastgele_mumlar, ust_mumlar  # noqa: E402

BILGI = {**STANDART_BILGI, "swap_mode": 5, "swap_long": -3.0, "swap_short": -2.0, "swap_rollover3days": 3}


@pytest.fixture(scope="module")
def sem():
    h1 = rastgele_mumlar(6000, tohum=9, hafta_sonu=False, spread_pt=14)
    return motor.sembol_kur("EURUSD", "forex", BILGI, pd.DataFrame(h1), pd.DataFrame(ust_mumlar(h1)), 1000)


@pytest.mark.parametrize("spread_carpani", [1.0, 2.0])
def test_tek_islem_sonuclari_ayni(cfg, sem, spread_carpani):
    rng = np.random.default_rng(1)
    idx = np.sort(rng.choice(np.arange(1000, 5900), 400, replace=False))
    yon = rng.choice([-1, 1], len(idx)).astype(np.int8)
    prm = backtest.Prm.cfgden(cfg, spread_carpani=spread_carpani)
    cikis = motor.Cikis(prm.sl_atr_carpan, prm.tp_r, prm.breakeven_r, prm.trailing_baslangic_r, prm.trailing_atr_carpan, 0)
    v = motor.canonik(sem)
    v.yon = np.zeros(sem.n, np.int8)
    v.yon[idx] = yon
    df = motor.simule(sem, idx, yon, cikis, motor.Maliyet(spread_carpani=spread_carpani), sem.n - 1).set_index("i")
    karsilastirilan = 0
    for i in idx:
        tr = backtest.islem_simule(v, int(i), prm)
        if tr is None:
            assert i not in df.index
            continue
        r = df.loc[i]
        assert r.k == tr["k"] and motor.KOD_AD[r.kod] == tr["cikis"], i
        assert r.R_brut == pytest.approx(tr["R_brut"], abs=1e-9) and r.R == pytest.approx(tr["R"], abs=1e-9), i
        karsilastirilan += 1
    assert karsilastirilan > 300
    assert {"SL", "TP", "BE", "TRAIL"} <= {motor.KOD_AD[k] for k in df.kod.unique()}     # bütün çıkış türleri görüldü


def test_tek_pozisyon_secimi_ayni(cfg, sem):
    prm = backtest.Prm.cfgden(cfg, min_skor=60)
    cikis = motor.Cikis(prm.sl_atr_carpan, prm.tp_r, prm.breakeven_r, prm.trailing_baslangic_r, prm.trailing_atr_carpan, 0)
    kesim = backtest.Kesim("x", int(sem.t[2000]), int(sem.t[5000]), int(sem.t[5200]))
    beklenen = backtest.tek_simule(motor.canonik(sem), prm, kesim)
    mal = motor.Maliyet()
    maske, son = motor.uygun_mumlar(sem, kesim.bas, kesim.bit, kesim.veri_bit, prm.max_spread_atr_orani, mal)
    df = motor.tek(sem, maske & (sem.skor0 >= 60), sem.yon0, cikis, mal, son)
    assert len(df) == len(beklenen) > 50
    assert list(df["i"]) == [x["i"] for x in beklenen]
    assert df["R"].to_numpy() == pytest.approx([x["R"] for x in beklenen], abs=1e-9)
    assert motor.ozet(df)["ort_R"] == backtest.metrikler(beklenen)["ort_R"]


def test_zaman_stopu_ve_sabit_cikis(sem):
    cikis = motor.Cikis.sabit(tp_atr=1.0, sl_atr=2.0, max_mum=5)
    idx = np.arange(1500, 1700)
    df = motor.simule(sem, idx, np.ones(len(idx), np.int8), cikis, motor.Maliyet(swap=False), sem.n - 1)
    assert (df["k"] - df["i"] <= 5).all()                           # en geç 5. mumda kapanır
    assert set(df["kod"]) <= {motor.SL, motor.TP, motor.ZAMAN}      # başa baş / takip yok
    assert df.loc[df.kod == motor.TP, "R_brut"].min() >= 0.5 - 1e-9 and df["R_brut"].min() >= -1.0 - 1e-9 - 0.5
    z = df[df.kod == motor.ZAMAN].iloc[0]
    assert z.R_brut == pytest.approx((sem.c[z.k] - (sem.o[z.i + 1] + sem.sp[z.i + 1])) / (2.0 * sem.atr[z.i]), abs=1e-9)


def test_komsular_ve_istatistikler(sem):
    cikis = motor.Cikis.sabit(1.0, 2.0, 24)
    adlar = [a for a, _ in motor.komsular(cikis)]
    assert adlar == ["sl_atr x0.8", "sl_atr x1.2", "tp_r x0.8", "tp_r x1.2", "max_mum x0.8", "max_mum x1.2"]
    assert replace(cikis, max_mum=19) == dict(motor.komsular(cikis))["max_mum x0.8"]
    idx = np.arange(1500, 5000, 3)
    havuz = motor.simule(sem, idx, np.ones(len(idx), np.int8), cikis, motor.Maliyet(), sem.n - 1)
    iyi = havuz[havuz.R > havuz.R.quantile(0.6)]                  # bilerek iyi seçilmiş girişler
    assert motor.permutasyon(iyi, havuz, n=300)["p"] < 0.01
    rastgele = havuz.sample(400, random_state=1)
    assert motor.permutasyon(rastgele, havuz, n=300)["p"] > 0.05
    alt, ust = motor.blok_guven_araligi(havuz, n=300)
    assert alt < havuz.R.mean() < ust
    mc = motor.monte_carlo_dusus(havuz, n=200)
    assert mc["p95_%"] >= mc["medyan_%"] > 0


def test_gunluk_ozellikler_gelecek_bilgisi_icermez():
    """Günlük değerler (kapanış, SMA200, RSI2, ATR) her H1 mumunda DÜNE aittir ve gelecekten etkilenmez."""
    h1 = rastgele_mumlar(9000, tohum=4, hafta_sonu=False)
    a = motor.sembol_kur("X", "endeks", BILGI, pd.DataFrame(h1), pd.DataFrame(ust_mumlar(h1)), 1000)
    gun = a.t // 86400
    i = 7000
    dun_son = np.flatnonzero(gun < gun[i])[-1]
    assert a.d_c[i] == a.c[dun_son]                                  # dünkü kapanış
    assert a.gun_ilk[i] == (gun[i] != gun[i - 1])
    bozuk = h1.copy()
    kes = np.flatnonzero(gun == gun[i])[0]                           # bugünün ilk mumundan itibaren her şeyi boz
    for k in ("open", "high", "low", "close"):
        bozuk[k][kes:] *= 1.37
    b = motor.sembol_kur("X", "endeks", BILGI, pd.DataFrame(bozuk), pd.DataFrame(ust_mumlar(bozuk)), 1000)
    bugun = gun == gun[i]
    for k in ("d_c", "d_sma200", "d_sma5", "d_rsi2", "d_atr", "d_ret1"):
        assert np.array_equal(a.f[k][bugun], b.f[k][bugun], equal_nan=True), k      # bugünün mumları dünü görür
        assert np.array_equal(a.f[k][:kes], b.f[k][:kes], equal_nan=True), k
    from arastirma import deney
    m, yon, _ = deney.s_gunluk_dip(a)
    assert m.sum() > 0 and a.gun_ilk[m].all() and (yon[m] == 1).all()
    assert (a.d_rsi2[m] < 10).all() and (a.d_c[m] > a.d_sma200[m]).all()


def test_portfoy_kurallari_bot_motoruyla_ayni(cfg):
    """Araştırma motorunun portföy seçimi (max pozisyon, döviz limiti, tur başına aday, günlük limit),
    botun backtest motoruyla AYNI işlemleri seçer."""
    from arastirma import deney
    bilgi = {**BILGI, "volume_step": 1e-6, "volume_min": 1e-6}       # lot yuvarlama farkı olmasın
    semler = []
    for k, ad in enumerate(["EURUSD", "GBPUSD", "AUDJPY", "USDCAD", "NZDUSD", "EURGBP"]):
        h1 = rastgele_mumlar(5000, tohum=30 + k, hafta_sonu=False, spread_pt=10)
        semler.append(motor.sembol_kur(ad, "forex", bilgi, pd.DataFrame(h1), pd.DataFrame(ust_mumlar(h1)), 1000))
    prm = backtest.Prm.cfgden(cfg, min_skor=60)
    cikis = deney.varsayilan_cikis(cfg)
    t = semler[0].t
    kesim = backtest.Kesim("x", int(t[1200]), int(t[4700]), int(t[4900]))
    beklenen = backtest.portfoy_simule([motor.canonik(s) for s in semler], prm, kesim, cfg)
    df = deney.portfoy_islemleri(semler, deney.s_mevcut, cikis, motor.Maliyet(), kesim, cfg)
    assert len(beklenen) > 100
    assert sorted(zip(df["sembol"], df["i"])) == sorted((x["sembol"], x["i"]) for x in beklenen)
    assert motor.ozet(df)["toplam_R"] == backtest.metrikler(beklenen)["toplam_R"]
    tek = pd.concat([deney.islemler([s], deney.s_mevcut, cikis, motor.Maliyet(), kesim, cfg)[0] for s in semler])
    assert len(df) < len(tek)                                        # portföy kuralları gerçekten işlem eliyor


def test_gunluk_kirilim_arastirma_ve_bot_motoru_ayni(cfg_gk):
    """Araştırmada doğrulanan strateji (K2) ile bota konan strateji AYNI işlemleri ve AYNI sonuçları üretir."""
    from arastirma import deney
    from tests.sahte_mt5 import MUM_DTYPE
    from tests.yardimci import PAZARTESI
    n = 12000
    rng = np.random.default_rng(11)
    c = 100.0 * np.exp(np.cumsum(rng.normal(0.00015, 0.004, n)))
    o = np.r_[100.0, c[:-1]]
    h1 = np.zeros(n, MUM_DTYPE)
    h1["time"] = PAZARTESI + np.arange(n) * 3600
    h1["open"], h1["close"] = o, c
    h1["high"] = np.maximum(o, c) * (1 + np.abs(rng.normal(0, 0.002, n)))
    h1["low"] = np.minimum(o, c) * (1 - np.abs(rng.normal(0, 0.002, n)))
    h1["tick_volume"], h1["spread"] = 100, 3
    bilgi = {**BILGI, "point": 0.01, "trade_tick_size": 0.01, "trade_tick_value": 0.01, "swap_long": -20.0}
    sem = motor.sembol_kur("BTCUSD", "kripto", bilgi, pd.DataFrame(h1), pd.DataFrame(ust_mumlar(h1)), 1000)
    v = backtest.veri_hazirla("BTCUSD", "kripto", bilgi, pd.DataFrame(h1), pd.DataFrame(ust_mumlar(h1)), cfg_gk)
    kesim = backtest.Kesim("x", int(sem.t[1000]), int(sem.t[11500]), int(sem.t[11800]))
    bot = backtest.tek_simule(v, backtest.Prm.cfgden(cfg_gk, bakiye=1e9), kesim)
    ar, _ = deney.islemler([sem], deney.s_gunluk_kirilim, deney.TUTMA, motor.Maliyet(), kesim, cfg_gk)
    assert len(bot) == len(ar) > 15
    assert [x["i"] for x in bot] == list(ar["i"]) and [x["k"] for x in bot] == list(ar["k"])
    assert ar["R"].to_numpy() == pytest.approx([x["R"] for x in bot], abs=1e-9)
    assert {x["cikis"] for x in bot} <= {"SL", "ZAMAN", "SON"}                  # hedef, başa baş, takip yok
