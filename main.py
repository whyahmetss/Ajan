"""AJAN BOT - ana döngü
Tarayıcı -> Filtre (kod) -> 4 LLM ajanı -> Oy sayımı -> Yargıç (Claude/Grok/ikisi)
-> [isteğe bağlı TradingAgents] -> Risk/lot (kod) -> Emir (mod'a göre) -> İzleme

Çalıştır:  python main.py            (sürekli çalışır)
           python main.py --bir-kez  (tek tarama yapıp çıkar, test için)
Durdurmak: Ctrl+C   |  Yeni işlemleri durdurmak: klasöre "DURDUR" adında boş dosya koy
"""
import argparse
import logging
import time

import MetaTrader5 as mt5
import yaml
from dotenv import load_dotenv

from bot import ajanlar, haber, izleme, karar, kayit, mt5_baglanti as mb, risk, tarayici
from bot.derin_analiz import DerinAnaliz
from bot.emir import piyasa_emri
from bot.llm import LLM

log = logging.getLogger("ana")


def aday_isle(aday, cfg, llm, derin, olaylar, siniflar):
    k = cfg["log_klasoru"]
    temel = {"sembol": aday["sembol"], "sinif": aday["sinif"], "yon": aday["yon"], "skor": aday["skor"]}
    log.info("── ADAY %s %s (skor %d): %s", aday["yon"], aday["sembol"], aday["skor"], ", ".join(aday["nedenler"]))

    plan = risk.plan_olustur(aday, cfg)
    if plan is None:
        return

    # 5. ajan: FİLTRE (kod) — para harcamadan önce
    ok, sorunlar = risk.filtre_ajani(aday, plan, cfg, olaylar, siniflar)
    if not ok:
        log.info("   Filtre RED: %s", "; ".join(sorunlar))
        kayit.karar_yaz(k, {**temel, "asama": "filtre", "sonuc": "RED", "neden": "; ".join(sorunlar)})
        return
    lot_test, n = risk.lot_hesapla(plan, cfg, 0.5)
    if lot_test <= 0:
        log.info("   Lot RED: %s", n)
        kayit.karar_yaz(k, {**temel, "asama": "lot", "sonuc": "RED", "neden": n})
        return

    # 4 LLM ajanı (paralel)
    baglam = ajanlar.baglam_olustur(aday, plan, olaylar, risk.pozisyon_ozeti(cfg["magic"], siniflar))
    oylar = ajanlar.ajanlari_calistir(llm, cfg, baglam)
    for o in oylar:
        log.info("   %-5s [%s] %-6s %3d  %s", o["ajan"], o["model"], o["oy"], o["guven"],
                 " / ".join(o["gerekceler"][:2]))
    gecti, neden = karar.oy_sayimi(oylar, cfg)
    if not gecti:
        log.info("   Oy RED: %s", neden)
        kayit.karar_yaz(k, {**temel, "asama": "oy", "sonuc": "RED", "neden": neden, "oylar": oylar})
        return

    # Yargıç
    sonuc = karar.karar_ver(llm, cfg, baglam, oylar)
    log.info("   Yargıç: %s", sonuc["ozet"])
    if not sonuc["onay"]:
        kayit.karar_yaz(k, {**temel, "asama": "yargic", "sonuc": "RED", "neden": sonuc["ozet"], "oylar": oylar})
        return

    if derin.aktif:
        log.info("   TradingAgents derin analiz çalışıyor (birkaç dakika sürebilir)...")
        d_ok, d_neden = derin.onayliyor_mu(aday)
        log.info("   %s", d_neden)
        if not d_ok:
            kayit.karar_yaz(k, {**temel, "asama": "derin", "sonuc": "RED", "neden": d_neden})
            return

    # LLM'ler düşünürken durum değişmiş olabilir: planı ve filtreyi tazele
    plan = risk.plan_olustur(aday, cfg)
    ok, sorunlar = risk.filtre_ajani(aday, plan, cfg, olaylar, siniflar)
    lot, lot_not = risk.lot_hesapla(plan, cfg, sonuc["carpan"]) if ok else (0, "; ".join(sorunlar))
    if lot <= 0:
        log.info("   Son kontrol RED: %s", lot_not)
        kayit.karar_yaz(k, {**temel, "asama": "son_kontrol", "sonuc": "RED", "neden": lot_not})
        return

    ozet = (f"{plan['yon']} {lot} lot {plan['sembol']} @ {plan['giris']} | SL {plan['sl']} | "
            f"TP {plan['tp']} | {lot_not}")
    mod = cfg["mod"]
    if mod == "sinyal":
        log.info("   SİNYAL (emir atılmadı): %s", ozet)
        kayit.karar_yaz(k, {**temel, "asama": "sinyal", "sonuc": "SINYAL", "lot": lot, "neden": ozet, "oylar": oylar})
        return
    if mod == "onay":
        print(f"\n>>> İŞLEM ÖNERİSİ: {ozet}\n>>> {sonuc['ozet']}")
        if input(">>> Açılsın mı? (e/h): ").strip().lower() not in ("e", "evet", "y"):
            kayit.karar_yaz(k, {**temel, "asama": "kullanici", "sonuc": "RED", "lot": lot, "neden": "Kullanıcı reddetti"})
            return
        plan = risk.plan_olustur(aday, cfg)   # onay beklerken fiyat oynadı

    basarili, mesaj = piyasa_emri(plan, lot, cfg, yorum=f"ajanbot s{aday['skor']}")
    log.info("   EMİR %s: %s", "OK" if basarili else "HATA", mesaj)
    kayit.karar_yaz(k, {**temel, "asama": "emir", "sonuc": "ACILDI" if basarili else "HATA",
                        "lot": lot, "neden": mesaj, "oylar": oylar, "yargic": sonuc["yargiclar"]})


def tarama_turu(cfg, llm, derin):
    olaylar = haber.takvim(cfg)
    adaylar = tarayici.tara(cfg)
    siniflar = {s["ad"]: s["sinif"] for s in mb.semboller(cfg)}
    for a in adaylar:
        try:
            aday_isle(a, cfg, llm, derin, olaylar, siniflar)
        except Exception:
            log.exception("Aday işlenirken hata: %s", a["sembol"])
    log.info("Tur bitti. Bu oturumda toplam LLM çağrısı: %d", llm.cagri_sayisi)


def sunucu_zamani(ref: list[str]) -> int:
    z = 0
    for s in ref:
        t = mt5.symbol_info_tick(s)
        if t is not None:
            z = max(z, int(t.time))
    return z


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--bir-kez", action="store_true", help="Tek tarama yap ve çık")
    ap.add_argument("--config", default="config.yaml")
    args = ap.parse_args()

    load_dotenv()
    with open(args.config, encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    kayit.log_kur(cfg["log_klasoru"])
    if cfg["mod"] not in ("sinyal", "onay", "otomatik"):
        raise SystemExit("config: mod sinyal | onay | otomatik olmalı")

    mb.baglan(cfg["sadece_demo"])
    llm, derin = LLM(cfg), DerinAnaliz(cfg)
    liste = mb.semboller(cfg)
    log.info("Mod: %s | Zaman dilimi: %s | Taranacak sembol: %d | Karar: %s",
             cfg["mod"], cfg["zaman_dilimi"], len(liste), cfg["karar_modu"])
    if not liste:
        raise SystemExit("Taranacak sembol bulunamadı. 'python araclar.py semboller' ile kontrol et.")

    if args.bir_kez:
        tarama_turu(cfg, llm, derin)
        mt5.shutdown()
        return

    ref = [s["ad"] for s in liste[:25]]
    periyot = mb.DAKIKA[cfg["zaman_dilimi"]] * 60
    son_mum = None
    try:
        while True:
            try:
                izleme.pozisyonlari_yonet(cfg)
                sz = sunucu_zamani(ref)
                mum = sz // periyot
                if sz and mum != son_mum and (sz % periyot) >= cfg["mum_kapanis_gecikme_sn"]:
                    log.info("════ Tarama başlıyor (son kapanan mum) ════")
                    tarama_turu(cfg, llm, derin)
                    son_mum = mum
            except Exception:
                log.exception("Döngü hatası (bot çalışmaya devam ediyor)")
                if not mt5.terminal_info():
                    log.warning("MT5 bağlantısı koptu, yeniden bağlanılıyor...")
                    mb.baglan(cfg["sadece_demo"])
            time.sleep(20)
    except KeyboardInterrupt:
        log.info("Kullanıcı durdurdu. Açık pozisyonlar SL/TP ile broker'da korunmaya devam eder.")
    finally:
        mt5.shutdown()


if __name__ == "__main__":
    main()
