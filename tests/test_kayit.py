"""Kayıt: API anahtarları loga ve kayıt dosyalarına yazılmaz; kayıt içeriği; eski CSV biçimi."""
import json
import logging

from bot import kayit

ANAHTAR = "sk-ant-api03-GIZLIANAHTAR1234567890"
XAI = "xai-GIZLI0987654321abcdef"


def test_maskele(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", ANAHTAR)
    metin = f"hata: anahtar {ANAHTAR} ve Incorrect API key provided: xai-ab***************cd9 ve {XAI}"
    temiz = kayit.maskele(metin)
    assert ANAHTAR not in temiz and XAI not in temiz and "xai-ab" not in temiz
    assert kayit.maskele("sıradan mesaj") == "sıradan mesaj"


def test_log_dosyasina_anahtar_yazilmaz(tmp_path, monkeypatch):
    monkeypatch.setenv("XAI_API_KEY", XAI)
    kayit.log_kur(tmp_path)
    try:
        logging.getLogger("test").warning("istek başarısız, anahtar=%s", XAI)
        try:
            raise RuntimeError(f"Incorrect API key provided: {XAI}")
        except RuntimeError:
            logging.getLogger("test").exception("hata")
        for h in logging.getLogger().handlers:
            h.flush()
        icerik = (tmp_path / "bot.log").read_text(encoding="utf-8")
        assert XAI not in icerik and "***" in icerik
    finally:
        for h in list(logging.getLogger().handlers):
            logging.getLogger().removeHandler(h)
            h.close()


def test_karar_kaydi_ve_maskeleme(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", ANAHTAR)
    kayit.karar_yaz(tmp_path, {"sembol": "EURUSD", "mum_zamani": "2024-01-01 10:00:00", "sonuc": "RED",
                               "neden": f"HATA: {ANAHTAR}", "plan": {"giris": 1.1, "sl": 1.09, "tp": 1.12}})
    satir = (tmp_path / "kararlar.jsonl").read_text(encoding="utf-8")
    assert ANAHTAR not in satir and json.loads(satir)["zaman_utc"].endswith("+00:00")
    csv = (tmp_path / "kararlar.csv").read_text(encoding="utf-8-sig")
    assert csv.splitlines()[0] == ";".join(kayit.CSV_ALANLAR) and ANAHTAR not in csv and "1.09" in csv
    assert kayit.islenmis_adaylar(tmp_path) == {("EURUSD", "2024-01-01 10:00:00")}


def test_eski_csv_bicimi_ayri_dosyaya_tasinir(tmp_path):
    (tmp_path / "kararlar.csv").write_text("zaman;sembol;sonuc\n1;2;3\n", encoding="utf-8-sig")
    kayit.karar_yaz(tmp_path, {"sembol": "X"})
    assert len(list(tmp_path.glob("kararlar_eski_*.csv"))) == 1
    assert (tmp_path / "kararlar.csv").read_text(encoding="utf-8-sig").startswith("zaman;zaman_utc")
