"""Karar raporu: planların sanal sonucu, ajan isabeti, LLM onay/ret karşılaştırması."""
import json
from datetime import datetime, timezone

import pandas as pd
import pytest

from bot import backtest, kayit, rapor
from tests.yardimci import PAZARTESI, STANDART_BILGI, elle_mumlar

H = 3600
RISK = {"breakeven_r": 1.0, "trailing_baslangic_r": 1.5, "trailing_atr_carpan": 1.2}


def _df(satirlar, spread_pt=0):
    return pd.DataFrame(elle_mumlar(satirlar, spread_pt=spread_pt))


def _kayit(yon="AL", giris=100.0, sl=98.0, tp=0.0, max_mum=0, strateji="gunluk_kirilim", **ek):
    return {"sembol": "BTCUSD", "yon": yon, "strateji": strateji, "mum_zamani": str(pd.Timestamp(PAZARTESI, unit="s")),
            "plan": {"giris": giris, "sl": sl, "tp": tp, "max_mum": max_mum}, **ek}


def test_plan_sonucu_stop_hedef_zaman():
    stop = rapor.plan_sonucu(_kayit(), _df([(100, 101, 99, 100), (100, 100.5, 97.5, 98)]), 0.01, RISK)
    assert stop == {"R": -1.0, "cikis": "SL", "mum": 2}
    gap = rapor.plan_sonucu(_kayit(), _df([(100, 101, 99, 100), (96, 97, 95, 96)]), 0.01, RISK)
    assert gap["R"] == pytest.approx(-2.0)                                          # stopun ötesinde açılış
    hedef = rapor.plan_sonucu(_kayit(tp=101.0), _df([(100, 101.5, 99.5, 101)]), 0.01, RISK)
    assert hedef["cikis"] == "TP" and hedef["R"] == pytest.approx(0.5)
    ikisi = rapor.plan_sonucu(_kayit(tp=101.0), _df([(100, 101.5, 97, 100)]), 0.01, RISK)
    assert ikisi["cikis"] == "SL"                                                   # aynı mumda ikisi: stop
    zaman = rapor.plan_sonucu(_kayit(max_mum=3), _df([(100, 104, 99, 103)] * 5), 0.01, RISK)
    assert zaman == {"R": pytest.approx(1.5), "cikis": "ZAMAN", "mum": 3}
    acik = rapor.plan_sonucu(_kayit(max_mum=10), _df([(100, 104, 99, 103)] * 2), 0.01, RISK)
    assert acik["cikis"] == "ACIK"
    satis = rapor.plan_sonucu(_kayit("SAT", 100.0, 102.0), _df([(100, 101.95, 99, 100)], spread_pt=10), 0.01, RISK)
    assert satis["cikis"] == "SL"                                                   # ask = 101.95 + 0.10 >= 102
    assert rapor.plan_sonucu({"yon": "AL"}, _df([(1, 1, 1, 1)]), 0.01, RISK) is None


def test_plan_sonucu_backtest_ile_ayni(cfg_gk):
    """Rapordaki sanal sonuç, aynı plan için backtest motorunun sonucuyla aynıdır."""
    import numpy as np
    rng = np.random.default_rng(3)
    c = 100 * np.exp(np.cumsum(rng.normal(0.0005, 0.01, 400)))
    o = np.r_[100.0, c[:-1]]
    satir = list(zip(o, np.maximum(o, c) * 1.004, np.minimum(o, c) * 0.996, c))
    mumlar = _df(satir)
    n = len(satir)
    bilgi = {**STANDART_BILGI, "point": 0.01, "trade_tick_size": 0.01, "trade_tick_value": 0.01}
    yon = np.zeros(n, np.int8); yon[::37] = 1
    v = backtest.SembolVerisi("BTCUSD", "kripto", bilgi, mumlar["time"].to_numpy(np.int64), *(mumlar[k].to_numpy(float)
                              for k in ("open", "high", "low", "close")), np.zeros(n), np.full(n, 1.0), yon,
                              np.where(yon == 1, 110.0, 0.0), np.r_[np.ones(n - 1, bool), False], True, H, 0,
                              atr_stop=np.full(n, 3.0))
    prm = backtest.Prm.cfgden(cfg_gk, max_mum=24, bakiye=1e9, swap=False)
    karsilastirilan = 0
    for i in np.flatnonzero(yon == 1)[:-1]:
        tr = backtest.islem_simule(v, int(i), prm)
        giris = float(v.o[i + 1])
        k = _kayit(giris=giris, sl=giris - 6.0, max_mum=24)
        s = rapor.plan_sonucu(k, mumlar.iloc[i + 1:].reset_index(drop=True), 0.01, RISK)
        assert s["R"] == pytest.approx(tr["R_brut"], abs=1e-9) and s["cikis"] == tr["cikis"].replace("SON", "ACIK"), i
        karsilastirilan += 1
    assert karsilastirilan >= 8


def test_rapor_ajan_isabeti_ve_LLM_karsilastirmasi(cfg_gk, tmp_path):
    simdi = datetime.now(timezone.utc).isoformat(timespec="seconds")

    def oylar(boga, ayi):
        return [{"ajan": "boga", "model": "c", "oy": boga, "guven": 70, "gerekceler": []},
                {"ajan": "ayi", "model": "g", "oy": ayi, "guven": 70, "gerekceler": []}]
    kayitlar = [
        _kayit(max_mum=2, sembol="KAZANAN", asama="sinyal", sonuc="SINYAL", llm_mod="gozlem", llm_onay=True,
               oylar=oylar("DESTEK", "NOTR"), yargiclar=[{"model": "claude", "karar": "ONAY"}], zaman_utc=simdi,
               llm_maliyet_usd=0.01),
        _kayit(max_mum=2, sembol="KAYBEDEN", asama="sinyal", sonuc="SINYAL", llm_mod="gozlem", llm_onay=False,
               oylar=oylar("DESTEK", "KARSI"), yargiclar=[{"model": "claude", "karar": "RED"}], zaman_utc=simdi,
               llm_maliyet_usd=0.01),
        {"sembol": "PLANSIZ", "asama": "plan", "sonuc": "RED", "zaman_utc": simdi},
    ]
    veri = {"KAZANAN": _df([(100, 103, 99.5, 102), (102, 105, 101, 104)]),
            "KAYBEDEN": _df([(100, 100.5, 97, 97.5), (97.5, 98, 96, 97)])}
    df = rapor.sonuclari_hesapla(kayitlar, lambda s, t: (veri[s], 0.01), cfg_gk)
    assert list(df["sembol"]) == ["KAZANAN", "KAYBEDEN"] and list(df["cikis"]) == ["ZAMAN", "SL"]
    assert df["R"].tolist() == pytest.approx([2.0, -1.0])
    metin = rapor.rapor_metni(kayitlar, df)
    assert "Toplam kayıt: 3" in metin and "aday başına 0.0100 USD" in metin
    assert "LLM ONAY : {'aday': 1, 'sonuclanan': 1, 'kazanma_%': 100.0, 'ort_R': 2.0" in metin
    assert "LLM RED  : {'aday': 1, 'sonuclanan': 1, 'kazanma_%': 0.0, 'ort_R': -1.0" in metin
    assert "boga   isabet %50 (2 oy)" in metin                 # iki DESTEK: biri kazandı, biri kaybetti
    assert "ayi    isabet %100 (1 oy)" in metin                # KARSI dediği kaybetti
    assert "claude  ONAY 1 aday, ort +2.00R; RED 1 aday, ort -1.00R" in metin
    assert "Toplam kayıt: 0" in rapor.rapor_metni([{**k, "zaman_utc": "2020-01-01T00:00:00+00:00"} for k in kayitlar],
                                                  df.assign(zaman_utc="2020-01-01T00:00:00+00:00"), gun=7)


def test_kayitlari_oku_ve_bos_rapor(tmp_path):
    assert rapor.kayitlari_oku(tmp_path) == []
    kayit.karar_yaz(tmp_path, {"sembol": "X", "asama": "filtre", "sonuc": "RED"})
    (tmp_path / "kararlar.jsonl").open("a", encoding="utf-8").write("bozuk satır\n")
    k = rapor.kayitlari_oku(tmp_path)
    assert len(k) == 1 and json.dumps(k[0])
    assert "Sanal sonucu hesaplanabilen aday yok" in rapor.rapor_metni(k, pd.DataFrame())
