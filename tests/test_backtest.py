"""Backtest motoru: el yapımı mumlarla bilinen sonuçlar, maliyetler, boşluk kuralı, portföy kuralları,
zaman sıralı düşüş, eşik başına ayrı simülasyon, takvim bölümleri ve kilitli dönem."""
import json
from dataclasses import replace

import numpy as np
import pandas as pd
import pytest

from bot import backtest
from tests.yardimci import PAZARTESI, STANDART_BILGI, rastgele_mumlar, ust_mumlar

H = 3600
DUZ = (1.1, 1.1, 1.1, 1.1)


def _sv(ad, satirlar, sinyaller, sinif="forex", atr=0.001, sp_pt=0, zamanlar=None, **bilgi):
    n = len(satirlar)
    t = np.array(zamanlar if zamanlar is not None else [PAZARTESI + i * H for i in range(n)], np.int64)
    o, h, l, c = (np.array(x, float) for x in zip(*satirlar))
    yon, skor = np.zeros(n, np.int8), np.zeros(n, np.int16)
    for i, (y, s) in sinyaller.items():
        yon[i], skor[i] = y, s
    ardisik = np.zeros(n, bool)
    ardisik[:-1] = (t[1:] - t[:-1]) == H
    b = {"point": 1e-5, "digits": 5, "trade_tick_size": 1e-5, "trade_tick_value": 1.0,
         "trade_contract_size": 100000.0, "volume_min": 0.01, "volume_step": 0.01, "volume_max": 100.0,
         "trade_stops_level": 0, "swap_mode": 0, "swap_long": 0.0, "swap_short": 0.0, "swap_rollover3days": 3}
    b.update(bilgi)
    return backtest.SembolVerisi(ad, sinif, b, t, o, h, l, c, np.full(n, float(sp_pt)), np.full(n, atr),
                                 yon, skor, ardisik, False, H, 0)


@pytest.fixture
def prm(cfg):
    return backtest.Prm.cfgden(cfg)


# ------------------------------------------------------------------ tek işlem: bilinen sonuçlar
def test_alis_hedef(prm):
    v = _sv("EURUSD", [DUZ, (1.1, 1.1031, 1.0995, 1.102)], {0: (1, 80)})
    tr = backtest.islem_simule(v, 0, prm)
    assert tr["cikis"] == "TP" and tr["R"] == pytest.approx(2.0) and tr["k"] == 1


def test_ayni_mumda_stop_ve_hedef_stop_sayilir(prm):
    v = _sv("EURUSD", [DUZ, (1.1, 1.1031, 1.098, 1.1)], {0: (1, 80)})
    assert backtest.islem_simule(v, 0, prm)["R"] == pytest.approx(-1.0)


def test_gap_stopun_otesinde_acilistan_cikar(prm):
    v = _sv("EURUSD", [DUZ, (1.1, 1.1005, 1.099, 1.0992), (1.097, 1.0975, 1.096, 1.097)], {0: (1, 80)})
    tr = backtest.islem_simule(v, 0, prm)
    assert tr["k"] == 2 and tr["R"] == pytest.approx(-2.0)


def test_basa_bas_kapanisa_gore(prm):
    v = _sv("EURUSD", [DUZ, (1.1, 1.1018, 1.0996, 1.1016), (1.1016, 1.1017, 1.0999, 1.1)], {0: (1, 80)})
    tr = backtest.islem_simule(v, 0, prm)
    assert tr["cikis"] == "BE" and tr["R"] == pytest.approx(2e-5 / 0.0015) and tr["be_k"] == 1


def test_mum_ici_tepe_basa_bas_tetiklemez(prm):
    """Canlı izleme ile aynı: kapanış 1R altındaysa mum içi tepe stopu taşımaz."""
    v = _sv("EURUSD", [DUZ, (1.1, 1.1025, 1.0996, 1.1005), (1.1005, 1.1006, 1.0999, 1.1)], {0: (1, 80)})
    tr = backtest.islem_simule(v, 0, prm)
    assert tr["be_k"] is None and tr["cikis"] == "SON"


def test_satis_spread_ask_ile(prm):
    v = _sv("EURUSD", [DUZ, (1.1, 1.10145, 1.0995, 1.1)], {0: (-1, 80)}, sp_pt=10)
    tr = backtest.islem_simule(v, 0, prm)              # SL 1.1015; bid tepe 1.10145 < SL ama ask 1.10155 > SL
    assert tr["R"] == pytest.approx(-1.0) and tr["yon"] == "SAT"


def test_komisyon_R_den_duser(prm):
    v = _sv("EURUSD", [DUZ, (1.1, 1.1031, 1.0995, 1.102)], {0: (1, 80)})
    tr = backtest.islem_simule(v, 0, replace(prm, komisyon_lot=7.0))
    assert tr["R"] == pytest.approx(2.0 - 7.0 / 150.0) and tr["maliyet_R"] == pytest.approx(7.0 / 150.0)


def test_spread_carpani_maliyeti_artirir(prm):
    """Hedefe ulaşan işlem her spread'de 2R'dir (R girişe göre); maliyet, hedefin uzaklaşmasıyla gelir."""
    v = _sv("EURUSD", [DUZ, (1.1, 1.10307, 1.0995, 1.102)], {0: (1, 80)}, sp_pt=5)
    r1 = backtest.islem_simule(v, 0, prm)                            # TP 1.10305: ulaşıldı
    r2 = backtest.islem_simule(v, 0, replace(prm, spread_carpani=2.0))   # TP 1.10310: kaçtı
    assert r1["cikis"] == "TP" and r1["R"] == pytest.approx(2.0)
    assert r2["cikis"] == "SON" and r2["R"] == pytest.approx((1.102 - 1.1001) / 0.0015)


def test_canlida_acilmayacak_islemler_simule_edilmez(prm):
    v = _sv("EURUSD", [DUZ, DUZ], {0: (1, 80)}, sp_pt=100)            # spread > stopun %25'i
    assert backtest.islem_simule(v, 0, prm) is None
    v = _sv("EURUSD", [DUZ, DUZ], {0: (1, 80)}, volume_min=1.0)       # min lot riskten büyük
    assert backtest.islem_simule(v, 0, prm) is None


def test_gelecek_verisi_sonucu_degistirmez(prm):
    rng = np.random.default_rng(2)
    m = rastgele_mumlar(400, tohum=4)
    satir = list(zip(m["open"], m["high"], m["low"], m["close"]))
    v = _sv("EURUSD", satir, {100: (1, 80)})
    tr = backtest.islem_simule(v, 100, prm)
    for i in range(tr["k"] + 1, 400):
        v.o[i] *= 1 + rng.normal(0, 0.05); v.h[i] = v.o[i] * 1.1; v.l[i] = v.o[i] * 0.9; v.c[i] = v.o[i]
    assert backtest.islem_simule(v, 100, prm) == tr


# ------------------------------------------------------------------ swap
def test_gece_sayisi():
    pzt = PAZARTESI + 10 * H
    assert backtest._gece_sayisi(pzt, pzt + 3 * 86400, 3, False) == 5        # Sal, Çar, Per(x3)
    cuma = PAZARTESI + 4 * 86400 + 20 * H
    assert backtest._gece_sayisi(cuma, cuma + 2 * 86400 + 9 * H, 3, False) == 1   # hafta sonu yok
    assert backtest._gece_sayisi(cuma, cuma + 2 * 86400 + 9 * H, 3, True) == 3    # 7/24 piyasa


def test_swap_puan_modu(prm):
    v = _sv("EURUSD", [DUZ] * 3, {0: (1, 80)}, swap_mode=1, swap_long=-5.0)
    assert backtest._swap_R(v, prm, True, PAZARTESI, PAZARTESI + 86400 + H, 0.0015, 1.1) == pytest.approx(-5e-5 / 0.0015)
    assert backtest._swap_R(v, replace(prm, swap=False), True, PAZARTESI, PAZARTESI + 3 * 86400, 0.0015, 1.1) == 0


# ------------------------------------------------------------------ giriş kuralları
def test_ardisik_olmayan_mumda_giris_yok(prm):
    """O4b: sinyalden sonraki mum seans/hafta sonu boşluğundan sonra geliyorsa giriş yok."""
    zaman = [PAZARTESI, PAZARTESI + H, PAZARTESI + 5 * H, PAZARTESI + 6 * H]
    v = _sv("EURUSD", [DUZ] * 4, {1: (1, 80), 2: (1, 80)}, zamanlar=zaman)
    k = backtest.Kesim("x", 0, 10 ** 12, 10 ** 12)
    assert list(backtest._adaylar(v, prm, k)) == [2]


def _kaybeden(ad, sinyal_mumu, n=12, sinif="forex"):
    satir = [DUZ] * n
    satir[sinyal_mumu + 1] = (1.1, 1.1001, 1.098, 1.0985)          # bir sonraki mumda stop
    return _sv(ad, satir, {sinyal_mumu: (1, 80)}, sinif=sinif)


def _kazanan(ad, sinyal_mumu=0, skor=80, n=12, sinif="forex"):
    satir = [DUZ] * n
    satir[sinyal_mumu + 4] = (1.1, 1.1031, 1.0999, 1.103)          # 4 mum sonra hedef
    return _sv(ad, satir, {sinyal_mumu: (1, skor)}, sinif=sinif)


TUM = backtest.Kesim("tum", 0, 10 ** 12, 10 ** 12)


def test_portfoy_tur_basina_max_aday(cfg, prm):
    cfg["risk"].update(max_ayni_doviz=9, max_acik_pozisyon=9)
    v = [_kazanan(f"S{i}USD", skor=90 - i) for i in range(5)]
    alinan = backtest.portfoy_simule(v, prm, TUM, cfg)
    assert sorted(x["sembol"] for x in alinan) == ["S0USD", "S1USD", "S2USD"]


def test_portfoy_ayni_doviz_ve_max_pozisyon(cfg, prm):
    v = [_kazanan(a, skor=s) for a, s in (("EURUSD", 90), ("GBPUSD", 85), ("AUDUSD", 80))]
    assert len(backtest.portfoy_simule(v, prm, TUM, cfg)) == 2            # max_ayni_doviz 2
    cfg["risk"].update(max_ayni_doviz=9, max_acik_pozisyon=1)
    assert len(backtest.portfoy_simule(v, prm, TUM, cfg)) == 1


def test_portfoy_ayni_sembolde_tek_pozisyon(cfg, prm):
    satir = [DUZ] * 12
    satir[5] = (1.1, 1.1031, 1.0999, 1.103)
    v = _sv("EURUSD", satir, {0: (1, 80), 2: (1, 80), 5: (1, 80)})
    alinan = backtest.portfoy_simule([v], prm, TUM, cfg)
    assert [x["i"] for x in alinan] == [0, 5]                 # 2 açıkken alınmaz; çıkış mumunda yeni sinyal alınır


def test_portfoy_gunluk_zarar_limiti(cfg, prm):
    """4 stop (-%1.98) sonrası yeni işlem riskiyle limit aşılır: 5. işlem açılmaz."""
    cfg["risk"].update(max_ayni_doviz=9)
    v = [_kaybeden(f"S{k}XXX", 2 * k, sinif="kripto") for k in range(5)]
    alinan = backtest.portfoy_simule(v, prm, TUM, cfg)
    assert len(alinan) == 4 and all(x["R"] == pytest.approx(-1.0) for x in alinan)


def test_tek_sembol_modunda_portfoy_kurali_yok(cfg, prm):
    v = [_kazanan(a) for a in ("EURUSD", "GBPUSD", "AUDUSD", "NZDUSD")]
    assert len(backtest.simule(v, prm, TUM, cfg, portfoy=False)) == 4


# ------------------------------------------------------------------ metrikler ve bölümler
def test_max_dusus_zaman_sirasiyla(cfg):
    """Y6: sembol sırasıyla birleştirilse bile düşüş çıkış zamanına göre hesaplanır."""
    a = [{"sembol": "A", "giris_t": t, "cikis_t": t + 1, "R": 1.0, "risk_yuzde": 0.5, "cikis": "TP"} for t in (10, 30, 50)]
    b = [{"sembol": "B", "giris_t": t, "cikis_t": t + 1, "R": -1.0, "risk_yuzde": 0.5, "cikis": "SL"} for t in (20, 40, 60)]
    m = backtest.metrikler(a + b)
    assert m["max_dusus_R"] == 1.0 and m["max_dusus_%"] == 0.5 and m["getiri_%"] == 0.0


def test_metrikler_basabas_ayri_sayilir():
    x = [{"giris_t": 1, "cikis_t": 2, "R": 0.01, "risk_yuzde": 0.5, "cikis": "BE"},
         {"giris_t": 3, "cikis_t": 4, "R": 2.0, "risk_yuzde": 0.5, "cikis": "TP"},
         {"giris_t": 5, "cikis_t": 6, "R": -1.0, "risk_yuzde": 0.5, "cikis": "SL"}]
    m = backtest.metrikler(x)
    assert m["basabas_%"] == pytest.approx(33.3) and m["kar_faktoru"] == 2.01 and m["islem"] == 3


def _sentetik_veriler(cfg, n=3200, adet=2):
    out = []
    for k in range(adet):
        h1 = rastgele_mumlar(n, tohum=20 + k, hafta_sonu=True)
        v = backtest.veri_hazirla(f"S{k}USD", "kripto", {"point": 1e-5, "digits": 5, "trade_tick_size": 1e-5,
                                  "trade_tick_value": 1.0, "volume_min": 0.01, "volume_step": 0.01,
                                  "volume_max": 100.0, "trade_stops_level": 0},
                                  pd.DataFrame(h1), pd.DataFrame(ust_mumlar(h1)), cfg, onbellek=None)
        out.append(v)
    return out


def test_her_esik_ayri_simule_edilir(cfg, monkeypatch):
    """Y7: her min_skor için ayrı simülasyon; süzme ile aynı sonuç vermez."""
    veriler = _sentetik_veriler(cfg)
    prm = backtest.Prm.cfgden(cfg)
    k = backtest.Kesim("x", 0, 10 ** 12, 10 ** 12)
    s50 = [x for v in veriler for x in backtest.tek_simule(v, replace(prm, min_skor=50), k)]
    s60 = [x for v in veriler for x in backtest.tek_simule(v, replace(prm, min_skor=60), k)]
    assert len(s60) != len([x for x in s50 if x["skor"] >= 60])
    cagri = []
    orijinal = backtest.simule
    monkeypatch.setattr(backtest, "simule", lambda v, p, kes, c, portfoy=True: cagri.append((p.min_skor, kes.ad)) or orijinal(v, p, kes, c, portfoy))
    backtest.calistir(cfg, veriler, [50, 60])
    assert cagri == [(50, "egitim"), (50, "TEST"), (60, "egitim"), (60, "TEST")]


def test_takvim_bolumleri_ve_kilit(cfg, capsys):
    """Y8: bölümler tüm semboller için aynı tarih; kilit sınırı kalıcı; kilitli dönem bir kez."""
    veriler = _sentetik_veriler(cfg)
    veriler[1].t = veriler[1].t + 30 * 86400                 # ikinci sembol 1 ay sonra başlıyor
    dn = backtest.donemler(veriler, cfg)
    assert dn["egitim"].bit == dn["TEST"].bas and dn["TEST"].bit == dn["KILITLI"].bas
    assert dn["egitim"].veri_bit == dn["TEST"].veri_bit == dn["KILITLI"].bas      # geliştirme kilide taşmaz
    kilit = json.loads((backtest._kilit_dosyasi(cfg)).read_text(encoding="utf-8"))
    veriler[0].t = np.append(veriler[0].t, veriler[0].t[-1] + 400 * 86400)        # daha yeni veri gelse bile
    assert backtest.donemler(veriler, cfg)["KILITLI"].bas == kilit["kilit_baslangic"]
    veriler[0].t = veriler[0].t[:-1]
    for v in veriler:                                                               # hiçbir işlem kilide taşmaz
        for b in ("egitim", "TEST"):
            for x in backtest.tek_simule(v, backtest.Prm.cfgden(cfg, min_skor=40), dn[b]):
                assert x["cikis_t"] < dn["KILITLI"].bas
    backtest.calistir(cfg, veriler, [60], kilitli=True)
    with pytest.raises(SystemExit, match="daha önce"):
        backtest.calistir(cfg, veriler, [60], kilitli=True)
    backtest.calistir(cfg, veriler, [60], kilitli=True, zorla=True)
    assert len(json.loads(backtest._kilit_dosyasi(cfg).read_text(encoding="utf-8"))["acilislar"]) == 2


def test_dosyaya_kaydet_ve_yukle(cfg, tmp_path):
    h1 = rastgele_mumlar(1500, tohum=3)
    bilgi = {"point": 1e-5, "digits": 5, "trade_tick_size": 1e-5, "trade_tick_value": 1.0,
             "volume_min": 0.01, "volume_step": 0.01, "volume_max": 100.0, "trade_stops_level": 0}
    m = backtest.dosyaya_kaydet(tmp_path, "EURUSD.m", "forex", bilgi, pd.DataFrame(h1),
                                pd.DataFrame(ust_mumlar(h1)), cfg)
    (tmp_path / "semboller.json").write_text(json.dumps({"semboller": [m]}), encoding="utf-8")
    v = backtest.dosyadan_yukle(tmp_path, cfg)[0]
    assert v.ad == "EURUSD.m" and len(v.t) == 1500 and np.allclose(v.c, h1["close"])


def test_calistir_uctan_uca(cfg, capsys):
    veriler = _sentetik_veriler(cfg)
    df = backtest.calistir(cfg, veriler, [60])
    cikti = capsys.readouterr().out
    assert "egitim" in cikti and "TEST" in cikti and "KİLİTLİ" in cikti
    assert df is not None and set(df["bolum"]) <= {"egitim", "TEST"}


def test_eksik_sembol_bilgisi_atlanir(cfg):
    h1 = rastgele_mumlar(1500, tohum=3)
    v = backtest.veri_hazirla("X", "forex", {**STANDART_BILGI, "trade_tick_size": 0}, pd.DataFrame(h1),
                              pd.DataFrame(ust_mumlar(h1)), cfg, onbellek=None)
    assert v is None


def test_sembole_ozgu_yuzde_komisyon(prm):
    v = _sv("EURUSD", [DUZ, (1.1, 1.1031, 1.0995, 1.102)], {0: (1, 80)}, komisyon_yuzde=0.007)
    tr = backtest.islem_simule(v, 0, prm)
    assert tr["R"] == pytest.approx(2.0 - 0.00007 * 1.1 / 0.0015)          # ~0.05R
