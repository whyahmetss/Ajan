"""İşlem günlüğü: her kararı (açılsın ya da açılmasın) CSV ve JSONL olarak yazar.
Sonra 'hangi ajan haklı çıktı' analizini bu kayıtlarla yaparız."""
import csv
import json
import logging
import os
from datetime import datetime


def log_kur(klasor: str):
    os.makedirs(klasor, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(name)-9s %(levelname)-7s %(message)s",
        datefmt="%H:%M:%S",
        handlers=[logging.StreamHandler(),
                  logging.FileHandler(os.path.join(klasor, "bot.log"), encoding="utf-8")])
    for gurultu in ("httpx", "anthropic", "openai", "urllib3"):
        logging.getLogger(gurultu).setLevel(logging.WARNING)


def karar_yaz(klasor: str, kayit: dict):
    kayit = {"zaman": datetime.now().isoformat(timespec="seconds"), **kayit}
    with open(os.path.join(klasor, "kararlar.jsonl"), "a", encoding="utf-8") as f:
        f.write(json.dumps(kayit, ensure_ascii=False, default=str) + "\n")
    yol = os.path.join(klasor, "kararlar.csv")
    yeni = not os.path.exists(yol)
    alanlar = ["zaman", "sembol", "sinif", "yon", "skor", "asama", "sonuc", "lot", "neden"]
    with open(yol, "a", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=alanlar, extrasaction="ignore", delimiter=";")
        if yeni:
            w.writeheader()
        w.writerow(kayit)
