"""Yardımcı komutlar (hepsi sadece DEMO hesapta çalışır, hiçbiri emir atmaz)

  python araclar.py semboller                 -> Broker'daki semboller ve botun onları hangi sınıfa koyduğu
  python araclar.py modeller                  -> Claude ve Grok hesabında açık model isimleri
  python araclar.py veri_indir --yil 4        -> Backtest verisini veri/ klasörüne indir (CSV)
  python araclar.py veri_indir --yil 4 --tick-gun 5   -> + mum spread'ini tick verisiyle karşılaştır
  python araclar.py backtest                  -> Canlı terminalden veri çekip test et (varsayılan 4 yıl)
  python araclar.py backtest --veri veri      -> İndirilmiş dosyalardan test et (MT5 gerekmez)
  python araclar.py backtest --skorlar 50,60,70,80 --semboller EURUSD,XAUUSD,US100,BTCUSD
  python araclar.py backtest --tek            -> Portföy kuralları olmadan, sembol başına
  python araclar.py backtest --kilidi-ac      -> KİLİTLİ son dönemi test et (strateji bitince, BİR kez)
  python araclar.py rapor                     -> Karar kayıtlarından rapor: ajanlar ne kadar isabetli, LLM katmanı
  python araclar.py rapor --gun 7                değer katıyor mu? (--gun: sadece son N gün)

Backtest ve veri_indir, config'teki aktif stratejinin (strateji.ad) sınıflarını ve kurallarını kullanır.
"""
import argparse
import json
import os
from collections import Counter
from datetime import datetime, timedelta, timezone

import yaml
from dotenv import load_dotenv

from bot.yollar import yol


def cfg_yukle(yol_="config.yaml"):
    with open(yol(yol_), encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    if cfg.get("sadece_demo") is not True:
        raise SystemExit("config: sadece_demo true olmalı. Bu bot gerçek hesapta çalışmaz.")
    return cfg


def _baglan():
    from bot import mt5_baglanti as mb
    try:
        mb.baglan()             # gerçek hesapsa SystemExit (sadece okuma yapsak bile)
    except mb.BaglantiHatasi as ex:
        raise SystemExit(str(ex))


def semboller_komut(cfg):
    import MetaTrader5 as mt5
    from bot import mt5_baglanti as mb
    _baglan()
    haric_yol = tuple(cfg.get("haric_yol_kelimeleri", mb.VARSAYILAN_HARIC_YOL))
    say = Counter()
    for s in mt5.symbols_get() or []:
        sinif = mb.sinif_bul(s, cfg["piyasalar"], haric_yol)
        say[sinif or "ELENDİ"] += 1
        print(f"{s.name:16s} {str(sinif or '-'):8s} {s.path}")
    print("\nÖzet:", dict(say))
    print("Yanlış sınıflanan varsa config.yaml > piyasalar / haric_yol_kelimeleri düzenle.")
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


def _liste(cfg, sembol_listesi):
    """Taranacak semboller: aktif stratejinin varlık sınıflarıyla sınırlı."""
    from bot import backtest, mt5_baglanti as mb
    if sembol_listesi:
        cfg["manuel_semboller"] = sembol_listesi
    izinli = backtest.izinli_siniflar(cfg)
    return [s for s in mb.semboller(cfg) if not izinli or s["sinif"] in izinli]


def spread_kontrol(ad: str, zd: str, gun: int) -> dict | None:
    """Son `gun` günün tick verisiyle, mumların 'spread' alanını mum açılışındaki gerçek spread ile
    karşılaştırır (backtest giriş maliyetini mum spread'inden alıyor)."""
    import MetaTrader5 as mt5
    import pandas as pd
    from bot import mt5_baglanti as mb
    simdi = datetime.now(timezone.utc)
    bas, bit = simdi - timedelta(days=gun), simdi + timedelta(days=1)
    tk = mt5.copy_ticks_range(ad, bas, bit, mt5.COPY_TICKS_INFO)
    mumlar = mt5.copy_rates_range(ad, mb.ZAMAN_DILIMLERI[zd], bas, bit)
    si = mt5.symbol_info(ad)
    if tk is None or len(tk) == 0 or mumlar is None or len(mumlar) == 0 or si is None:
        return None
    tk = pd.DataFrame(tk)[["time", "bid", "ask"]]
    tk = tk[(tk.bid > 0) & (tk.ask > 0)]
    tk["sp"] = (tk.ask - tk.bid) / si.point
    tk["mum"] = tk["time"] // (mb.DAKIKA[zd] * 60) * (mb.DAKIKA[zd] * 60)
    g = tk.groupby("mum")["sp"].agg(ilk="first", medyan="median", en_cok="max")
    m = pd.DataFrame(mumlar)[["time", "spread"]].set_index("time").join(g, how="inner")
    if m.empty:
        return None
    return {"sembol": ad, "mum": len(m), "mum_spread_medyan": float(m.spread.median()),
            "acilis_tick_spread_medyan": float(m.ilk.median()), "tick_spread_medyan": float(m.medyan.median()),
            "acilis_tick>mum_spread_%": round(float(100 * (m.ilk > m.spread).mean()), 1),
            "acilis/mum_oran_medyan": round(float((m.ilk / m.spread.where(m.spread > 0)).median()), 2)}


def veri_indir_komut(cfg, yil, klasor, sembol_listesi, tick_gun):
    import MetaTrader5 as mt5
    import pandas as pd
    from bot import backtest, kayit, mt5_baglanti as mb
    kayit.log_kur(yol(cfg["log_klasoru"]))
    _baglan()
    mb.saat_ayarla(cfg)
    liste = _liste(cfg, sembol_listesi)
    mb.ofset_guncelle([s["ad"] for s in liste])
    print(f"{len(liste)} sembol indirilecek ({yil} yıl + ısınma payı) -> {yol(klasor)}")
    meta, spreadler = [], []
    for s in liste:
        r = backtest.mt5ten_cek(s["ad"], cfg, yil)
        if r is None:
            print(f"  {s['ad']:14s} VERİ YOK")
            continue
        m = backtest.dosyaya_kaydet(klasor, s["ad"], s["sinif"], r[2], r[0], r[1], cfg)
        meta.append(m)
        kisa = " (İSTENENDEN KISA! MT5 > Araçlar > Seçenekler > Grafikler > 'Grafikteki maks. çubuk' artır)" \
            if (datetime.now() - datetime.fromisoformat(m["ilk"])).days < 365 * yil else ""
        print(f"  {s['ad']:14s} {s['sinif']:7s} {m['ilk']} → {m['son']}  {m['mum']} mum{kisa}")
        if tick_gun:
            sk = spread_kontrol(s["ad"], cfg["zaman_dilimi"], tick_gun)
            if sk:
                spreadler.append(sk)
    yol(klasor).mkdir(parents=True, exist_ok=True)
    with open(yol(klasor) / "semboller.json", "w", encoding="utf-8") as f:
        json.dump({"indirme_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                   "sunucu_utc_ofset_sn": mb.ofset_sn(), "zaman_dilimi": cfg["zaman_dilimi"],
                   "ust_zaman_dilimi": cfg["ust_zaman_dilimi"], "semboller": meta},
                  f, ensure_ascii=False, indent=1, default=str)
    if spreadler:
        df = pd.DataFrame(spreadler)
        df.to_csv(yol(klasor) / "spread_kontrol.csv", index=False, sep=";")
        print("\nMum spread'i ile mum açılışındaki gerçek (tick) spread karşılaştırması:")
        print(df.to_string(index=False))
    print("\nTamam. Bu klasörü (veri/) paylaşabilirsin; içinde hesap bilgisi yok.")
    mt5.shutdown()


def backtest_komut(cfg, a):
    from bot import backtest, kayit
    kayit.log_kur(yol(cfg["log_klasoru"]))
    sembol_listesi = [x.strip() for x in a.semboller.split(",") if x.strip()]
    onbellek = yol(cfg["log_klasoru"]) / "onbellek"
    if a.veri:
        veriler = backtest.dosyadan_yukle(a.veri, cfg, sembol_listesi or None, onbellek)
    else:
        import MetaTrader5 as mt5
        _baglan()
        liste = _liste(cfg, sembol_listesi)
        print(f"{len(liste)} sembol test edilecek ({a.yil} yıl)...")
        veriler = backtest.mt5ten_yukle(cfg, liste, a.yil, onbellek)
        mt5.shutdown()
    df = backtest.calistir(cfg, veriler, [int(x) for x in a.skorlar.split(",")], portfoy=not a.tek,
                           kilitli=a.kilidi_ac, zorla=a.zorla)
    if df is not None and len(df):
        ad = "backtest_kilitli.csv" if a.kilidi_ac else "backtest_islemler.csv"
        dosya = yol(cfg["log_klasoru"]) / ad
        df.to_csv(dosya, index=False, sep=";", encoding="utf-8-sig")
        print(f"\nTüm işlemler: {dosya}")


def rapor_komut(cfg, gun):
    """Kayıtlardaki her adayın planını sonraki gerçek fiyatlarla sanal olarak oynatır ve raporlar."""
    import MetaTrader5 as mt5
    import pandas as pd
    from bot import mt5_baglanti as mb, rapor
    klasor = yol(cfg["log_klasoru"])
    kayitlar = rapor.kayitlari_oku(klasor)
    if not kayitlar:
        print(f"Henüz karar kaydı yok ({klasor / 'kararlar.jsonl'}). Bot bir süre çalıştıktan sonra tekrar dene.")
        return
    _baglan()
    zd = cfg["zaman_dilimi"]

    def mum_getir(sembol, giris_t):
        mb.sec(sembol)
        si, tick = mt5.symbol_info(sembol), mt5.symbol_info_tick(sembol)
        r = mt5.copy_rates_range(sembol, mb.ZAMAN_DILIMLERI[zd], giris_t, int(tick.time) + 86400)
        df = pd.DataFrame(r)
        return df[df["time"] < mb.mum_baslangici(int(tick.time), zd)].reset_index(drop=True), si.point   # kapanmış mumlar

    df = rapor.sonuclari_hesapla(kayitlar, mum_getir, cfg)
    mt5.shutdown()
    metin = rapor.rapor_metni(kayitlar, df, gun)
    print(metin)
    dosya = klasor / f"rapor_{pd.Timestamp.now():%Y%m%d}.txt"
    dosya.write_text(metin, encoding="utf-8")
    if len(df):
        df.to_csv(klasor / "rapor_adaylar.csv", index=False, sep=";", encoding="utf-8-sig")
    print(f"\nRapor: {dosya}")


if __name__ == "__main__":
    load_dotenv(yol(".env"))
    ap = argparse.ArgumentParser()
    ap.add_argument("komut", choices=["semboller", "modeller", "veri_indir", "backtest", "rapor"])
    ap.add_argument("--gun", type=int, default=0, help="rapor: sadece son N gün (0 = hepsi)")
    ap.add_argument("--yil", type=float, default=4.0, help="Kaç yıllık veri (ısınma payı ayrıca eklenir)")
    ap.add_argument("--klasor", default="veri", help="veri_indir: kayıt klasörü")
    ap.add_argument("--tick-gun", type=int, default=0, help="veri_indir: spread kontrolü için kaç günlük tick")
    ap.add_argument("--veri", default="", help="backtest: indirilen veri klasörü (boşsa canlı terminal)")
    ap.add_argument("--skorlar", default="50,60,70,80")
    ap.add_argument("--semboller", default="")
    ap.add_argument("--tek", action="store_true", help="backtest: portföy kuralları olmadan")
    ap.add_argument("--kilidi-ac", action="store_true", help="backtest: KİLİTLİ dönemi test et (bir kez)")
    ap.add_argument("--zorla", action="store_true", help="backtest: kilitli dönem daha önce açıldıysa bile")
    a = ap.parse_args()
    cfg = cfg_yukle()
    if a.komut == "semboller":
        semboller_komut(cfg)
    elif a.komut == "modeller":
        modeller_komut()
    elif a.komut == "rapor":
        rapor_komut(cfg, a.gun or None)
    elif a.komut == "veri_indir":
        veri_indir_komut(cfg, a.yil, a.klasor, [x.strip() for x in a.semboller.split(",") if x.strip()], a.tick_gun)
    else:
        backtest_komut(cfg, a)
