"""Uçtan uca aday akışı (sahte MT5 + sahte LLM, para harcamaz): sinyal/onay/otomatik modları,
kayma iptali, onay sonrası yeniden kontrol, zaman aşımı, tekrar engelleme, kayıt içeriği."""
import json
import threading
from types import SimpleNamespace as NS

import pytest

import main
from bot import kayit, mt5_baglanti as mb, risk
from bot.derin_analiz import DerinAnaliz
from tests.yardimci import PAZARTESI, onaylayan_llm

SIMDI = PAZARTESI + 2 * 86400 + 15 * 3600


@pytest.fixture
def ortam(mt5, monkeypatch):
    monkeypatch.setattr(mb, "sunucu_simdi", lambda: SIMDI)
    mt5.sembol_ekle("EURUSD")
    mt5.tick_ayarla("EURUSD", 1.10000, 1.10010, SIMDI)
    main._islenmis.clear()
    return mt5


def _aday(mum="2024-01-03 14:00:00"):
    return {"sembol": "EURUSD", "sinif": "forex", "yon": "AL", "skor": 75, "nedenler": ["test"],
            "ust_trend": 1, "mum_zamani": mum, "mum_zaman_sn": 0, "fiyat": 1.1, "atr": 0.0010,
            "rsi": 60.0, "adx": 30.0, "ema20": 1.099, "ema50": 1.098, "ema200": 1.09,
            "spread": 0.0001, "digits": 5, "son_mumlar": []}


def _kayitlar(cfg):
    with open(f"{cfg['log_klasoru']}/kararlar.jsonl", encoding="utf-8") as f:
        return [json.loads(x) for x in f]


def _isle(cfg, llm, aday=None):
    main.aday_isle(aday or _aday(), cfg, llm, DerinAnaliz(cfg), [], True, {})


def test_sinyal_modu_emir_atmaz_ve_tam_kayit_yazar(cfg, ortam):
    _isle(cfg, onaylayan_llm())
    k = _kayitlar(cfg)[-1]
    assert k["sonuc"] == "SINYAL" and ortam.D.gonderilen == []
    assert k["mum_zamani"] == "2024-01-03 14:00:00" and k["zaman_utc"].endswith("+00:00")
    assert {"giris", "sl", "tp", "mesafe", "atr"} <= set(k["plan"]) and k["lot"] == 0.33
    assert len(k["oylar"]) == 4 and len(k["yargiclar"]) == 2 and "llm_kullanim" in k


def test_otomatik_mod_emir_acar(cfg, ortam):
    cfg["mod"] = "otomatik"
    _isle(cfg, onaylayan_llm())
    assert _kayitlar(cfg)[-1]["sonuc"] == "ACILDI" and len(ortam.D.gonderilen) == 1


def test_filtre_LLMden_once_calisir(cfg, ortam):
    risk.DURDUR_DOSYASI.write_text("")
    llm = onaylayan_llm()
    _isle(cfg, llm)
    assert llm.cagrilar == [] and _kayitlar(cfg)[-1]["asama"] == "filtre"


def test_ayi_hatasinda_yargica_gidilmez(cfg, ortam):
    llm = onaylayan_llm(ayi=RuntimeError("grok çöktü"))
    _isle(cfg, llm)
    assert _kayitlar(cfg)[-1]["asama"] == "oy" and not any(c.startswith("yargic") for c in llm.cagrilar)


def test_LLM_dusunurken_fiyat_kacarsa_iptal(cfg, ortam):
    """Y3: LLM'ler çalışırken fiyat 0.8R kaçtı -> son kontrolde iptal, emir yok."""
    cfg["mod"] = "otomatik"

    def kacir():
        ortam.tick_ayarla("EURUSD", 1.10120, 1.10130, SIMDI + 60)
        return {"oy": "DESTEK", "guven": 80, "gerekceler": ["x"]}
    _isle(cfg, onaylayan_llm(boga=kacir))
    k = _kayitlar(cfg)[-1]
    assert k["asama"] == "son_kontrol" and "uzaklaştı" in k["neden"] and ortam.D.gonderilen == []


def test_onay_beklerken_durum_degisirse_emir_yok(cfg, ortam, monkeypatch):
    """Y4: onaydan sonra filtre baştan çalışır (ör. bu arada DURDUR konuldu)."""
    cfg["mod"] = "onay"

    def onayla(metin, sure):
        risk.DURDUR_DOSYASI.write_text("")
        return True
    monkeypatch.setattr(main, "onay_iste", onayla)
    _isle(cfg, onaylayan_llm())
    k = _kayitlar(cfg)[-1]
    assert k["asama"] == "son_kontrol" and "DURDUR" in k["neden"] and ortam.D.gonderilen == []


@pytest.mark.parametrize("cevap, neden", [(None, "gelmedi"), (False, "reddetti")])
def test_onay_zaman_asimi_ve_ret(cfg, ortam, monkeypatch, cevap, neden):
    cfg["mod"] = "onay"
    monkeypatch.setattr(main, "onay_iste", lambda m, s: cevap)
    _isle(cfg, onaylayan_llm())
    k = _kayitlar(cfg)[-1]
    assert k["asama"] == "kullanici" and neden in k["neden"] and ortam.D.gonderilen == []


def test_onay_verilirse_emir(cfg, ortam, monkeypatch):
    cfg["mod"] = "onay"
    monkeypatch.setattr(main, "onay_iste", lambda m, s: True)
    _isle(cfg, onaylayan_llm())
    assert _kayitlar(cfg)[-1]["sonuc"] == "ACILDI"


def test_ayni_mum_iki_kez_LLMe_gitmez(cfg, ortam):
    llm = onaylayan_llm()
    _isle(cfg, llm)
    n = len(llm.cagrilar)
    _isle(cfg, llm)
    assert len(llm.cagrilar) == n
    main._islenmis.clear()                                    # yeniden başlatma
    main._islenmis.update(kayit.islenmis_adaylar(cfg["log_klasoru"]))
    _isle(cfg, llm)
    assert len(llm.cagrilar) == n
    _isle(cfg, llm, _aday(mum="2024-01-03 15:00:00"))         # yeni mum: değerlendirilir
    assert len(llm.cagrilar) > n


def test_onay_iste_zaman_asimi_ve_eski_girdi(monkeypatch):
    monkeypatch.setattr(main, "_okuyucu_baslat", lambda: None)
    main._girdi.put("e\n")                                     # geç yazılmış eski cevap: atılmalı
    assert main.onay_iste("?", 0.1) is None
    threading.Timer(0.05, lambda: main._girdi.put("e\n")).start()
    assert main.onay_iste("?", 2) is True
    threading.Timer(0.05, lambda: main._girdi.put("hayır\n")).start()
    assert main.onay_iste("?", 2) is False


def test_sadece_demo_false_ise_baslamaz(tmp_path):
    c = tmp_path / "c.yaml"
    c.write_text("mod: sinyal\nsadece_demo: false\n", encoding="utf-8")
    with pytest.raises(SystemExit, match="sadece_demo"):
        main.ayarlari_yukle(NS(config=str(c), mod=None))


def test_mod_komut_satirindan(tmp_path):
    c = tmp_path / "c.yaml"
    c.write_text("mod: otomatik\nsadece_demo: true\n", encoding="utf-8")
    assert main.ayarlari_yukle(NS(config=str(c), mod="sinyal"))["mod"] == "sinyal"


# ------------------------------------------------------------------ ana döngü (sonsuz döngü, uyku ile kesilir)
@pytest.fixture
def dongu(cfg, mt5, monkeypatch, tmp_path):
    import logging
    import yaml
    import time as _time
    mt5.sembol_ekle("EURUSD")
    mt5.tick_ayarla("EURUSD", 1.1, 1.1001, _time.time() - 5)
    yol = tmp_path / "c.yaml"
    yol.write_text(yaml.safe_dump(cfg, allow_unicode=True), encoding="utf-8")
    monkeypatch.setattr(main.sys, "argv", ["main.py", "--config", str(yol)])
    sayac = {"baglan": 0, "uyku": 0, "tarama": 0}
    orijinal = mb.baglan

    def baglan():
        sayac["baglan"] += 1
        return orijinal()
    monkeypatch.setattr(mb, "baglan", baglan)
    monkeypatch.setattr(main, "tarama_turu", lambda *a: sayac.__setitem__("tarama", sayac["tarama"] + 1))
    yield sayac
    for h in list(logging.getLogger().handlers):
        logging.getLogger().removeHandler(h)
        h.close()


def test_dongu_baglanti_kopunca_yeniden_baglanir(dongu, mt5, monkeypatch):
    mt5.D.terminal_bagli = False                    # ilk turda broker bağlantısı yok

    def uyu(sn):
        dongu["uyku"] += 1
        mt5.D.terminal_bagli = True                 # sonra geri geliyor
        if dongu["uyku"] >= 3:
            raise KeyboardInterrupt
    monkeypatch.setattr(main.time, "sleep", uyu)
    main.main()                                     # SystemExit yok, Ctrl+C ile temiz çıkış
    assert dongu["baglan"] >= 2                     # açılış + yeniden bağlanma


def test_dongu_calisirken_gercek_hesaba_gecilirse_durur(dongu, mt5, monkeypatch):
    def uyu(sn):
        mt5.D.hesap.trade_mode = mt5.ACCOUNT_TRADE_MODE_REAL
    monkeypatch.setattr(main.time, "sleep", uyu)
    with pytest.raises(SystemExit, match="DEMO değil"):
        main.main()


def test_dongu_mum_kapanisinda_bir_kez_tarar(dongu, mt5, monkeypatch):
    zaman = {"t": PAZARTESI + 10 * 3600 + 30}
    monkeypatch.setattr(mb, "sunucu_simdi", lambda: zaman["t"])

    def uyu(sn):
        dongu["uyku"] += 1
        zaman["t"] += 600 if dongu["uyku"] < 8 else 0
        if dongu["uyku"] >= 8:
            raise KeyboardInterrupt
    monkeypatch.setattr(main.time, "sleep", uyu)
    main.main()
    assert dongu["tarama"] == 2                     # 10:00:30 ve 11:00:30 mumları (yedi 10 dk'lık adım)


def test_yollar_proje_klasorune_gore():
    from bot.yollar import PROJE, yol
    assert yol("DURDUR") == PROJE / "DURDUR" and yol("/mutlak/yol").is_absolute()
    assert (PROJE / "config.yaml").exists()
