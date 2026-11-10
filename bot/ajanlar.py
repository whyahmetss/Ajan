"""2. AŞAMA - 5 AJAN
  1) Boğa   (LLM)  : işlemin lehine en güçlü argümanlar
  2) Ayı    (LLM)  : işlemin aleyhine riskler, veto hakkı var
  3) Makro  (LLM)  : haber takvimi, döviz/endeks/kripto bağlamı
  4) Risk   (LLM)  : SL/TP yerleşimi, R:R ve zamanlama kalitesi
  5) Filtre (KOD)  : spread, haber blackout, pozisyon/döviz limitleri, günlük zarar.
                     LLM değildir, kandırılamaz, önce o çalışır.
"""
import json
import logging
from concurrent.futures import ThreadPoolExecutor

from . import haber

log = logging.getLogger("ajanlar")

ORTAK = (
    "Sen profesyonel bir trading masasında çalışan bir analistsin. Sana bir sembol için "
    "teknik veriler, son mumlar, yaklaşan ekonomik olaylar ve açık pozisyonlar verilecek. "
    "Gelecek fiyatları bilemezsin; uydurma bilgi, uydurma haber kullanma. "
    "Sadece verilen veriye dayan. Emin değilsen NOTR de. Türkçe yaz, kısa ve net ol."
)

SEMA = (
    '{"oy": "DESTEK" | "KARSI" | "NOTR", "guven": 0-100 arası tamsayı, '
    '"gerekceler": ["en fazla 4 kısa madde"]}'
)

ROLLER = {
    "boga": ("BOĞA ANALİSTİ: Önerilen yöndeki işlemin lehine olan en güçlü kanıtları bul. "
             "Ama dürüst ol: kanıt zayıfsa DESTEK verme, NOTR de. Trend, momentum, kurulum "
             "kalitesi ve üst zaman dilimi uyumuna bak."),
    "ayi": ("AYI ANALİSTİ (ŞEYTANIN AVUKATI): Bu işlemi neden AÇMAMALIYIZ? Geç giriş, aşırı "
            "uzamış hareket, ters yönlü üst trend, yakın direnç/destek, sahte kırılım ihtimali, "
            "haber riski, düşük likidite saatleri gibi riskleri ara. Ciddi risk varsa KARSI, "
            "işlem temizse DESTEK, arada ise NOTR ver. 'KARSI' + yüksek güven işlemi iptal eder."),
    "makro": ("MAKRO / HABER ANALİSTİ: Yaklaşan ekonomik olaylar, ilgili dövizlerin genel "
              "durumu ve varlık sınıfına (forex/endeks/kripto/emtia) özgü riskleri değerlendir. "
              "Önümüzdeki saatlerde yüksek etkili veri varsa bunun pozisyona etkisini yorumla. "
              "Bilmediğin güncel haberleri UYDURMA; sadece verilen takvime dayan."),
    "risk": ("RİSK ANALİSTİ: Önerilen giriş, stop ve hedefin mantıklı olup olmadığını değerlendir. "
             "Stop gürültü içinde mi kalıyor? Hedef önünde engel var mı? R:R gerçekçi mi? "
             "Mevcut açık pozisyonlarla korelasyon riski var mı? Lot hesabını SEN yapmıyorsun, "
             "kod yapıyor; sen sadece kurulumun risk kalitesini oyla."),
}


def baglam_olustur(aday: dict, plan: dict, olaylar: list, pozisyonlar: list) -> str:
    dov = haber.sembol_dovizleri(aday["sembol"], aday["sinif"])
    veri = {
        "sembol": aday["sembol"], "varlik_sinifi": aday["sinif"],
        "onerilen_yon": aday["yon"], "tarayici_skoru": aday["skor"],
        "tarayici_nedenleri": aday["nedenler"],
        "ust_zaman_dilimi_trendi": {1: "yukari", -1: "asagi", 0: "yatay"}[aday["ust_trend"]],
        "fiyat": aday["fiyat"], "ATR": round(aday["atr"], aday["digits"]),
        "RSI": round(aday["rsi"], 1), "ADX": round(aday["adx"], 1),
        "EMA20": round(aday["ema20"], aday["digits"]), "EMA50": round(aday["ema50"], aday["digits"]),
        "EMA200": round(aday["ema200"], aday["digits"]),
        "plan": {"giris": plan["giris"], "stop": plan["sl"], "hedef": plan["tp"], "R_R": plan["rr"]},
        "son_24_mum [zaman, acilis, yuksek, dusuk, kapanis]": aday["son_mumlar"],
        "yaklasan_ekonomik_olaylar": haber.yakin_olaylar(olaylar, dov),
        "acik_pozisyonlar": pozisyonlar,
    }
    return json.dumps(veri, ensure_ascii=False, default=str)


def _ajan_calistir(llm, ad: str, saglayici: str, baglam: str) -> dict:
    sistem = f"{ORTAK}\n\nROLÜN: {ROLLER[ad]}\n\nÇıktı şeması: {SEMA}"
    try:
        r = llm.sor_json(saglayici, sistem, baglam)
        oy = str(r.get("oy", "NOTR")).upper()
        if oy not in ("DESTEK", "KARSI", "NOTR"):
            oy = "NOTR"
        guven = max(0, min(100, int(r.get("guven", 0))))
        return {"ajan": ad, "model": saglayici, "oy": oy, "guven": guven,
                "gerekceler": list(r.get("gerekceler", []))[:4]}
    except Exception as ex:
        log.warning("Ajan %s başarısız: %s", ad, ex)
        # Hata = güvenli taraf: NOTR (destek sayılmaz)
        return {"ajan": ad, "model": saglayici, "oy": "NOTR", "guven": 0, "gerekceler": [f"HATA: {ex}"]}


def ajanlari_calistir(llm, cfg, baglam: str) -> list[dict]:
    """4 LLM ajanını paralel çalıştırır (süre ~ tek çağrı kadar)."""
    atama = cfg["ajanlar"]
    with ThreadPoolExecutor(max_workers=4) as ex:
        isler = [ex.submit(_ajan_calistir, llm, ad, atama[ad], baglam) for ad in ROLLER]
        return [i.result() for i in isler]
