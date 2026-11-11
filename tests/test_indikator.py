"""İndikatörler: elle hesaplanmış bilinen değerler ve bağımsız döngü referansıyla."""
import numpy as np
import pandas as pd
import pytest

from bot import indikator


def test_ema_bilinen_degerler():
    # span=3 -> alpha=0.5, ilk değer tohum: 1, 1.5, 2.25, 3.125, 4.0625
    e = indikator.ema(pd.Series([1.0, 2, 3, 4, 5]), 3)
    assert e.tolist() == pytest.approx([1, 1.5, 2.25, 3.125, 4.0625])


def test_atr_bilinen_degerler():
    df = pd.DataFrame({"high": [10.0, 12, 11], "low": [8.0, 9, 10], "close": [9.0, 11, 10.5]})
    # TR = [2, 3, 1]; n=2 -> alpha=0.5: 2, 2.5, 1.75
    assert indikator.atr(df, 2).tolist() == pytest.approx([2, 2.5, 1.75])


def _rsi_referans(kapanis, n=14):
    """Wilder RSI, düz döngü (indikator.rsi ile aynı tohumlama)."""
    up = dn = None
    out = [50.0]
    for i in range(1, len(kapanis)):
        d = kapanis[i] - kapanis[i - 1]
        u, w = max(d, 0.0), max(-d, 0.0)
        up = u if up is None else up + (u - up) / n
        dn = w if dn is None else dn + (w - dn) / n
        if dn == 0:
            out.append(100.0 if up > 0 else 50.0)
        else:
            out.append(100 - 100 / (1 + up / dn))
    return out


def test_rsi_dongu_referansiyla_ayni():
    rng = np.random.default_rng(3)
    c = list(100 + np.cumsum(rng.normal(0, 1, 300)))
    assert indikator.rsi(pd.Series(c)).tolist() == pytest.approx(_rsi_referans(c), abs=1e-9)


def test_rsi_hic_dusus_yoksa_100():
    # D2 regresyonu: eskiden 50 dönüyordu
    assert indikator.rsi(pd.Series(np.arange(1.0, 40.0))).iloc[-1] == 100.0


def test_rsi_sabit_fiyat_50_ve_surekli_dusus_0():
    assert indikator.rsi(pd.Series([5.0] * 30)).iloc[-1] == 50.0
    assert indikator.rsi(pd.Series(np.arange(40.0, 1.0, -1))).iloc[-1] == pytest.approx(0.0)


def test_adx_aralik_ve_trendde_yuksek():
    n = 300
    c = np.linspace(100, 160, n)
    df = pd.DataFrame({"high": c + 0.5, "low": c - 0.5, "close": c})
    a = indikator.adx(df)
    assert ((a >= 0) & (a <= 100)).all()
    assert a.iloc[-1] > 50                       # kesintisiz trend


def test_donchian_gelecek_bilgisi_icermez():
    rng = np.random.default_rng(1)
    c = 100 + np.cumsum(rng.normal(0, 1, 120))
    df = pd.DataFrame({"open": c, "high": c + 1, "low": c - 1, "close": c})
    d = indikator.ekle(df)
    for i in (25, 60, 119):
        assert d.dc_ust.iloc[i] == df.high.iloc[i - 20:i].max()     # i. mum dahil DEĞİL
        assert d.dc_alt.iloc[i] == df.low.iloc[i - 20:i].min()


def test_indikatorler_nedensel():
    """i. mumdaki değerler, i'den sonraki veriler değişince değişmemeli (lookahead yok)."""
    rng = np.random.default_rng(7)
    c = 100 + np.cumsum(rng.normal(0, 1, 400))
    df = pd.DataFrame({"open": c, "high": c + 1, "low": c - 1, "close": c})
    d1 = indikator.ekle(df)
    df2 = df.copy()
    df2.iloc[300:, :] *= 1.5                      # geleceği boz
    d2 = indikator.ekle(df2)
    kol = ["ema20", "ema50", "ema200", "rsi", "atr", "adx", "dc_ust", "dc_alt"]
    pd.testing.assert_frame_equal(d1.loc[:299, kol], d2.loc[:299, kol])
