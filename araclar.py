"""Yardımcı komutlar

  python araclar.py semboller              -> Broker'daki semboller ve botun onları hangi sınıfa koyduğu
  python araclar.py modeller               -> Claude ve Grok hesabında açık model isimleri
  python araclar.py backtest               -> Stratejiyi geçmiş veride test et (varsayılan 20000 mum)
  python araclar.py backtest --bar 30000 --skorlar 50,60,70,80
  python araclar.py backtest --semboller EURUSD,XAUUSD,US100,BTCUSD
"""
import argparse
import os
from collections import Counter

import yaml
from dotenv import load_dotenv


def cfg_yukle(yol="config.yaml"):
    with open(yol, encoding="utf-8") as f:
        return yaml.safe_load(f)


def semboller_komut(cfg):
    import MetaTrader5 as mt5
    from bot import mt5_baglanti as mb
    mb.baglan(sadece_demo=False)      # sadece okuma yapar, emir atmaz
    say = Counter()
    for s in mt5.symbols_get() or []:
        sinif = mb.sinif_bul(s, cfg["piyasalar"])
        say[sinif or "ELENDİ"] += 1
        print(f"{s.name:16s} {str(sinif or '-'):8s} {s.path}")
    print("\nÖzet:", dict(say))
    print("Yanlış sınıflanan varsa config.yaml > piyasalar kelimelerini düzenle.")
    mt5.shutdown()


def modeller_komut():
    if os.getenv("ANTHROPIC_API_KEY"):
        import anthropic
        print("CLAUDE modelleri:")
        for m in anthropic.Anthropic().models.list(limit=50).data:
            print("  ", m.id)
    else:
        print("ANTHROPIC_API_KEY yok")
    if os.getenv("XAI_API_KEY"):
        from openai import OpenAI
        c = OpenAI(api_key=os.environ["XAI_API_KEY"], base_url="https://api.x.ai/v1")
        print("GROK modelleri:")
        for m in c.models.list().data:
            print("  ", m.id)
    else:
        print("XAI_API_KEY yok")


def backtest_komut(cfg, bar, skorlar, sembol_listesi):
    import MetaTrader5 as mt5
    from bot import backtest, kayit, mt5_baglanti as mb
    kayit.log_kur(cfg["log_klasoru"])
    mb.baglan(sadece_demo=False)
    if sembol_listesi:
        cfg["manuel_semboller"] = sembol_listesi
    liste = mb.semboller(cfg)
    print(f"{len(liste)} sembol test edilecek...")
    df = backtest.calistir(cfg, liste, bar, skorlar)
    if df is not None:
        yol = os.path.join(cfg["log_klasoru"], "backtest_islemler.csv")
        df.to_csv(yol, index=False, sep=";", encoding="utf-8-sig")
        print(f"\nTüm işlemler: {yol}")
    mt5.shutdown()


if __name__ == "__main__":
    load_dotenv()
    ap = argparse.ArgumentParser()
    ap.add_argument("komut", choices=["semboller", "modeller", "backtest"])
    ap.add_argument("--bar", type=int, default=20000)
    ap.add_argument("--skorlar", default="50,60,70,80")
    ap.add_argument("--semboller", default="")
    a = ap.parse_args()
    cfg = cfg_yukle()
    if a.komut == "semboller":
        semboller_komut(cfg)
    elif a.komut == "modeller":
        modeller_komut()
    else:
        backtest_komut(cfg, a.bar, [int(x) for x in a.skorlar.split(",")],
                       [x.strip() for x in a.semboller.split(",") if x.strip()])
