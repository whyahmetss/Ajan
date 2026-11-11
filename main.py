"""AJAN BOT - ana döngü
Tarayıcı -> Filtre (kod) -> 4 LLM ajanı -> Oy sayımı -> Yargıç (Claude/Grok/ikisi)
-> [isteğe bağlı TradingAgents] -> Son kontrol: plan + kayma + filtre + lot (kod)
-> Emir (mod'a göre) -> İzleme (her mum kapanışında)

Çalıştır:  python main.py                         (sürekli çalışır)
           python main.py --bir-kez               (tek tarama yapıp çıkar, test için)
           python main.py --bir-kez --mod sinyal  (config'i değiştirmeden, emir atmadan dene)
Durdurmak: Ctrl+C   |  Yeni işlemleri durdurmak: proje klasörüne "DURDUR" adında boş dosya koy
"""
import argparse
import logging
import queue
import sys
import threading
import time

import MetaTrader5 as mt5
import yaml
from dotenv import load_dotenv

from bot import ajanlar, haber, izleme, karar, kayit, mt5_baglanti as mb, risk, tarayici
from bot.derin_analiz import DerinAnaliz
from bot.emir import piyasa_emri
from bot.llm import LLM, kullanim_farki
from bot.yollar import yol

log = logging.getLogger("ana")
_islenmis: set = set()          # (sembol, mum_zamani): aynı mum için LLM'e iki kez para ödenmesin


def _plan_kaydi(plan: dict) -> dict:
    return {k: (round(plan[k], 8) if isinstance(plan.get(k), float) else plan.get(k))
            for k in ("giris", "sl", "tp", "mesafe", "atr", "spread", "analiz_giris")}


# ------------------------------------------------------------------ onay (zaman aşımlı)
_girdi: queue.Queue = queue.Queue()
_okuyucu = None


def _okuyucu_baslat():
    global _okuyucu
    if _okuyucu is None:
        def oku():
            for satir in sys.stdin:
                _girdi.put(satir)
        _okuyucu = threading.Thread(target=oku, daemon=True)
        _okuyucu.start()


def onay_iste(metin: str, zaman_asimi: float) -> bool | None:
    """True/False = kullanıcının cevabı, None = zaman aşımı. Geç yazılmış eski girdiler atılır."""
    _okuyucu_baslat()
    while True:
        try:
            _girdi.get_nowait()
        except queue.Empty:
            break
    print(metin, end="", flush=True)
    try:
        cevap = _girdi.get(timeout=zaman_asimi)
    except queue.Empty:
        print()
        return None
    return cevap.strip().lower() in ("e", "evet", "y", "yes")


# ------------------------------------------------------------------ aday işleme
def son_kontrol(aday, plan_llm, cfg, olaylar, siniflar, takvim_ok):
    """Emirden hemen önce: planı tazele, kaymayı LLM'lerin gördüğü plana göre ölç,
    filtreyi ve lotu baştan çalıştır. Dönüş: (plan, lot, not) ya da (None, 0, neden)."""
    plan = risk.plan_olustur(aday, cfg, referans=plan_llm)
    if plan is None:
        return None, 0.0, "Plan tazelenemedi (tick yok)"
    izin = cfg["risk"].get("max_kayma_r", 0.3)
    kayma = abs(plan["giris"] - plan["analiz_giris"])
    if kayma > izin * plan["analiz_mesafe"]:
        return None, 0.0, f"Fiyat analizden {kayma / plan['analiz_mesafe']:.2f}R uzaklaştı (izin {izin}R)"
    ok, sorunlar = risk.filtre_ajani(aday, plan, cfg, olaylar, siniflar, takvim_ok)
    if not ok:
        return None, 0.0, "; ".join(sorunlar)
    lot, lot_not = risk.lot_hesapla(plan, cfg)
    if lot <= 0:
        return None, 0.0, lot_not
    return plan, lot, lot_not


def aday_isle(aday, cfg, llm, derin, olaylar, takvim_ok, siniflar):
    k = yol(cfg["log_klasoru"])
    anahtar = (aday["sembol"], aday["mum_zamani"])
    if anahtar in _islenmis:
        log.info("── %s %s mumu zaten değerlendirildi, atlanıyor", *anahtar)
        return
    _islenmis.add(anahtar)
    temel = {"sembol": aday["sembol"], "sinif": aday["sinif"], "yon": aday["yon"], "skor": aday["skor"],
             "mum_zamani": aday["mum_zamani"], "mod": cfg["mod"]}
    once = llm.kullanim_kopya()

    def yaz(**alanlar):
        fark = kullanim_farki(once, llm.kullanim_kopya())
        kayit.karar_yaz(k, {**temel, **alanlar, "llm_kullanim": fark,
                            "llm_maliyet_usd": round(llm.maliyet_usd(fark)[0], 6)})

    log.info("── ADAY %s %s (skor %d, mum %s): %s", aday["yon"], aday["sembol"], aday["skor"],
             aday["mum_zamani"], ", ".join(aday["nedenler"]))

    plan_llm = risk.plan_olustur(aday, cfg)
    if plan_llm is None:
        return yaz(asama="plan", sonuc="RED", neden="Plan oluşturulamadı (tick yok)")
    pk = _plan_kaydi(plan_llm)

    # 5. ajan: FİLTRE (kod) — para harcamadan önce
    ok, sorunlar = risk.filtre_ajani(aday, plan_llm, cfg, olaylar, siniflar, takvim_ok)
    if not ok:
        log.info("   Filtre RED: %s", "; ".join(sorunlar))
        return yaz(asama="filtre", sonuc="RED", neden="; ".join(sorunlar), plan=pk)
    lot_test, n = risk.lot_hesapla(plan_llm, cfg)
    if lot_test <= 0:
        log.info("   Lot RED: %s", n)
        return yaz(asama="lot", sonuc="RED", neden=n, plan=pk)

    # 4 LLM ajanı (paralel)
    baglam = ajanlar.baglam_olustur(aday, plan_llm, olaylar, risk.pozisyon_ozeti(cfg["magic"], siniflar))
    oylar = ajanlar.ajanlari_calistir(llm, cfg, baglam)
    for o in oylar:
        log.info("   %-5s [%s] %-6s %3d  %s", o["ajan"], o["model"], o["oy"], o["guven"],
                 " / ".join(o["gerekceler"][:2]))
    gecti, neden = karar.oy_sayimi(oylar, cfg)
    if not gecti:
        log.info("   Oy RED: %s", neden)
        return yaz(asama="oy", sonuc="RED", neden=neden, oylar=oylar, plan=pk)

    # Yargıç
    sonuc = karar.karar_ver(llm, cfg, baglam, oylar)
    log.info("   Yargıç: %s", sonuc["ozet"])
    if not sonuc["onay"]:
        return yaz(asama="yargic", sonuc="RED", neden=sonuc["ozet"], oylar=oylar,
                   yargiclar=sonuc["yargiclar"], plan=pk)

    if derin.aktif:
        log.info("   TradingAgents derin analiz çalışıyor (birkaç dakika sürebilir)...")
        d_ok, d_neden = derin.onayliyor_mu(aday)
        log.info("   %s", d_neden)
        if not d_ok:
            return yaz(asama="derin", sonuc="RED", neden=d_neden, oylar=oylar,
                       yargiclar=sonuc["yargiclar"], plan=pk)

    ortak = {"oylar": oylar, "yargiclar": sonuc["yargiclar"]}
    # LLM'ler düşünürken durum değişmiş olabilir: plan, kayma, filtre ve lot baştan
    plan, lot, lot_not = son_kontrol(aday, plan_llm, cfg, olaylar, siniflar, takvim_ok)
    if plan is None:
        log.info("   Son kontrol RED: %s", lot_not)
        return yaz(asama="son_kontrol", sonuc="RED", neden=lot_not, plan=pk, **ortak)

    ozet = (f"{plan['yon']} {lot} lot {plan['sembol']} @ {plan['giris']} | SL {plan['sl']} | "
            f"TP {plan['tp']} | {lot_not}")
    mod = cfg["mod"]
    if mod == "sinyal":
        log.info("   SİNYAL (emir atılmadı): %s", ozet)
        return yaz(asama="sinyal", sonuc="SINYAL", lot=lot, neden=ozet, plan=_plan_kaydi(plan), **ortak)
    if mod == "onay":
        za = cfg.get("onay_zaman_asimi_sn", 120)
        cevap = onay_iste(f"\n>>> İŞLEM ÖNERİSİ: {ozet}\n>>> {sonuc['ozet']}\n"
                          f">>> Açılsın mı? (e/h, {za} sn içinde): ", za)
        if not cevap:
            neden = "Kullanıcı reddetti" if cevap is False else f"Onay {za} sn içinde gelmedi"
            return yaz(asama="kullanici", sonuc="RED", lot=lot, neden=neden, plan=_plan_kaydi(plan), **ortak)
        # Onay beklenirken durum değişmiş olabilir: her şey baştan kontrol edilir
        plan, lot, lot_not = son_kontrol(aday, plan_llm, cfg, olaylar, siniflar, takvim_ok)
        if plan is None:
            log.info("   Onay sonrası kontrol RED: %s", lot_not)
            return yaz(asama="son_kontrol", sonuc="RED", neden=f"Onay sonrası: {lot_not}", plan=pk, **ortak)

    basarili, mesaj = piyasa_emri(plan, lot, cfg, yorum=f"ajanbot s{aday['skor']}")
    log.info("   EMİR %s: %s", "OK" if basarili else "HATA", mesaj)
    yaz(asama="emir", sonuc="ACILDI" if basarili else "HATA", lot=lot, neden=mesaj,
        plan=_plan_kaydi(plan), **ortak)


def tarama_turu(cfg, llm, derin):
    olaylar, takvim_ok = haber.takvim(cfg)
    siniflar = {s["ad"]: s["sinif"] for s in mb.semboller(cfg)}
    for a in tarayici.tara(cfg):
        try:
            aday_isle(a, cfg, llm, derin, olaylar, takvim_ok, siniflar)
        except Exception:
            log.exception("Aday işlenirken hata: %s", a["sembol"])
    log.info("Tur bitti. LLM (oturum toplamı): %s", llm.ozet())


# ------------------------------------------------------------------ başlatma
def ayarlari_yukle(args) -> dict:
    load_dotenv(yol(".env"))
    with open(yol(args.config), encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    if args.mod:
        cfg["mod"] = args.mod
    if cfg["mod"] not in ("sinyal", "onay", "otomatik"):
        raise SystemExit("config: mod sinyal | onay | otomatik olmalı")
    if cfg.get("sadece_demo") is not True:
        raise SystemExit("config: sadece_demo true olmalı. Bu bot gerçek hesapta çalışmaz.")
    return cfg


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--bir-kez", action="store_true", help="Tek tarama yap ve çık")
    ap.add_argument("--config", default="config.yaml")
    ap.add_argument("--mod", choices=["sinyal", "onay"],
                    help="Bu çalıştırma için modu değiştir (otomatik sadece config'ten seçilebilir)")
    args = ap.parse_args()

    cfg = ayarlari_yukle(args)
    kayit.log_kur(yol(cfg["log_klasoru"]))
    mb.saat_ayarla(cfg)
    try:
        mb.baglan()
    except mb.BaglantiHatasi as ex:
        raise SystemExit(str(ex))
    llm, derin = LLM(cfg), DerinAnaliz(cfg)
    _islenmis.update(kayit.islenmis_adaylar(yol(cfg["log_klasoru"])))
    liste = mb.semboller(cfg)
    if not liste:
        raise SystemExit("Taranacak sembol bulunamadı. 'python araclar.py semboller' ile kontrol et.")
    isimler = [s["ad"] for s in liste]
    mb.ofset_guncelle(isimler)
    ofset = mb.ofset_sn()
    log.info("Mod: %s | Zaman dilimi: %s | Taranacak sembol: %d | Karar: %s | Sunucu saati: %s",
             cfg["mod"], cfg["zaman_dilimi"], len(liste), cfg["karar_modu"],
             "bilinmiyor" if ofset is None else f"UTC{ofset / 3600:+.0f}")

    if args.bir_kez:
        try:
            tarama_turu(cfg, llm, derin)
        finally:
            mt5.shutdown()
        return

    periyot = mb.DAKIKA[cfg["zaman_dilimi"]] * 60
    son_mum, son_ofset, bekleme = None, time.time(), 5
    try:
        while True:
            try:
                mb.demo_kilidi()                    # gerçek hesaba geçildiyse SystemExit
                if not mb.baglanti_saglikli():
                    raise mb.BaglantiHatasi("Terminal broker sunucusuna bağlı değil")
                if time.time() - son_ofset >= 60:
                    mb.ofset_guncelle(isimler)
                    son_ofset = time.time()
                izleme.pozisyonlari_yonet(cfg)      # her pozisyon için her kapanan mum bir kez
                sz = mb.sunucu_simdi()
                mum = sz // periyot
                if mum != son_mum and (sz % periyot) >= cfg["mum_kapanis_gecikme_sn"]:
                    log.info("════ Tarama başlıyor (son kapanan mum) ════")
                    tarama_turu(cfg, llm, derin)
                    son_mum = mum
                bekleme = 5
            except mb.BaglantiHatasi as ex:
                log.warning("MT5 bağlantı sorunu: %s. %d sn sonra yeniden bağlanılacak.", ex, bekleme)
                time.sleep(bekleme)
                bekleme = min(bekleme * 2, 300)
                try:
                    mb.baglan()                     # gerçek hesap ise SystemExit
                except mb.BaglantiHatasi as ex2:
                    log.warning("Yeniden bağlanılamadı: %s", ex2)
                continue
            except Exception:
                log.exception("Döngü hatası (bot çalışmaya devam ediyor)")
            time.sleep(5)
    except KeyboardInterrupt:
        log.info("Kullanıcı durdurdu. Açık pozisyonlar SL/TP ile broker'da korunmaya devam eder.")
    finally:
        mt5.shutdown()


if __name__ == "__main__":
    main()
