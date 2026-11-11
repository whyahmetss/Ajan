"""İşlem günlüğü: her kararı (açılsın ya da açılmasın) CSV ve JSONL olarak yazar.
Sonra 'hangi ajan haklı çıktı' analizini bu kayıtlarla yaparız: her kayıtta planın kendisi
(giriş/SL/TP/ATR), analiz edilen mumun zamanı ve UTC zaman damgası bulunur.
API anahtarları ve parola, log ve kayıt dosyalarına yazılmadan önce maskelenir."""
import csv
import json
import logging
import os
import re
from collections import deque
from datetime import datetime, timezone

GIZLI_ENV = ("ANTHROPIC_API_KEY", "XAI_API_KEY", "MT5_PASSWORD")
_DESENLER = (re.compile(r"sk-ant-[A-Za-z0-9_\-*.]{4,}"), re.compile(r"xai-[A-Za-z0-9_\-*.]{4,}"))
CSV_ALANLAR = ["zaman", "zaman_utc", "mum_zamani", "sembol", "sinif", "yon", "skor", "asama",
               "sonuc", "lot", "giris", "sl", "tp", "neden"]


def maskele(metin: str) -> str:
    """Ortam değişkenlerindeki gizli değerleri ve anahtar kalıplarını *** ile değiştirir."""
    for ad in GIZLI_ENV:
        v = os.getenv(ad)
        if v and len(v) >= 6:
            metin = metin.replace(v, "***")
    for d in _DESENLER:
        metin = d.sub("***", metin)
    return metin


class MaskeliFormatter(logging.Formatter):
    def format(self, record):
        return maskele(super().format(record))


def log_kur(klasor):
    os.makedirs(klasor, exist_ok=True)
    fmt = MaskeliFormatter("%(asctime)s %(name)-9s %(levelname)-7s %(message)s",
                           datefmt="%Y-%m-%d %H:%M:%S")
    isleyiciler = [logging.StreamHandler(),
                   logging.FileHandler(os.path.join(klasor, "bot.log"), encoding="utf-8")]
    for h in isleyiciler:
        h.setFormatter(fmt)
    logging.basicConfig(level=logging.INFO, handlers=isleyiciler, force=True)
    for gurultu in ("httpx", "httpx2", "anthropic", "openai", "urllib3"):
        logging.getLogger(gurultu).setLevel(logging.WARNING)


def karar_yaz(klasor, kayit: dict):
    simdi = datetime.now()
    kayit = {"zaman": simdi.isoformat(timespec="seconds"),
             "zaman_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"), **kayit}
    os.makedirs(klasor, exist_ok=True)
    with open(os.path.join(klasor, "kararlar.jsonl"), "a", encoding="utf-8") as f:
        f.write(maskele(json.dumps(kayit, ensure_ascii=False, default=str)) + "\n")

    yol = os.path.join(klasor, "kararlar.csv")
    if os.path.exists(yol):
        with open(yol, encoding="utf-8-sig") as f:
            baslik = f.readline().strip()
        if baslik != ";".join(CSV_ALANLAR):        # eski biçim: yeni dosyaya geç
            os.replace(yol, os.path.join(klasor, f"kararlar_eski_{simdi:%Y%m%d_%H%M%S}.csv"))
    yeni = not os.path.exists(yol)
    plan = kayit.get("plan") or {}
    satir = {**kayit, "giris": plan.get("giris"), "sl": plan.get("sl"), "tp": plan.get("tp"),
             "neden": maskele(str(kayit.get("neden", "")))}
    with open(yol, "a", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=CSV_ALANLAR, extrasaction="ignore", delimiter=";")
        if yeni:
            w.writeheader()
        w.writerow(satir)


def islenmis_adaylar(klasor, n: int = 2000) -> set:
    """Son kayıtlardaki (sembol, mum_zamani) çiftleri: yeniden başlatmada aynı mum için
    LLM'lere tekrar para ödenmesin."""
    yol = os.path.join(klasor, "kararlar.jsonl")
    out = set()
    if not os.path.exists(yol):
        return out
    with open(yol, encoding="utf-8") as f:
        for satir in deque(f, maxlen=n):
            try:
                k = json.loads(satir)
            except ValueError:
                continue
            if k.get("mum_zamani"):
                out.add((k.get("sembol"), str(k["mum_zamani"])))
    return out
