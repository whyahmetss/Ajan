"""RİSK KATMANI - tamamen kod. LLM buradaki hiçbir sayıyı değiştiremez.
- İşlem planı (giriş / stop / hedef)
- 5. ajan: FİLTRE (sert kurallar)
- Lot hesabı: bakiyenin sabit yüzdesi kadar risk. LLM lotu ne artırabilir ne azaltabilir.
- Günlük zarar limiti: broker sunucu gününe göre, deal geçmişinden hesaplanır (yeniden
  başlatmaya dayanıklı) ve açık pozisyonların stop riskini de sayar.
"""
import logging
import MetaTrader5 as mt5

from . import haber, mt5_baglanti as mb
from .strateji import adima_yuvarla
from .yollar import yol

log = logging.getLogger("risk")
DURDUR_DOSYASI = yol("DURDUR")     # Bu isimde bir dosya oluşturursan bot yeni işlem açmaz

# Dışarıdan para hareketi (yatırma/çekme, kredi, bonus): günlük kâr/zarara sayılmaz
DIS_HAREKET = {mt5.DEAL_TYPE_BALANCE, mt5.DEAL_TYPE_CREDIT, mt5.DEAL_TYPE_BONUS}


def plan_olustur(aday: dict, cfg, referans: dict | None = None) -> dict | None:
    """Giriş / stop / hedef. referans = LLM'lere gösterilen plan: kayma kontrolü (emir.py)
    her zaman o fiyata göre yapılır, plan sonradan tazelense bile.
    Stratejinin kendi çıkışı varsa (aday["cikis"]: stop mesafesi, hedef oranı, zaman stopu) o kullanılır;
    hedef oranı yoksa pozisyon HEDEFSİZ açılır (tp = 0) ve zaman stopuyla kapanır."""
    r = cfg["risk"]
    bilgi = mt5.symbol_info(aday["sembol"])
    tick = mt5.symbol_info_tick(aday["sembol"])
    if bilgi is None or tick is None:
        return None
    al = aday["yon"] == "AL"
    giris = tick.ask if al else tick.bid
    spread = tick.ask - tick.bid
    cikis = aday.get("cikis")
    if cikis:
        mesafe, tp_r, max_mum = cikis["sl_mesafe"], cikis.get("tp_r"), int(cikis.get("max_mum") or 0)
    else:
        mesafe, tp_r, max_mum = r["sl_atr_carpan"] * aday["atr"], r["tp_r"], 0
    # Broker'ın izin verdiği minimum stop mesafesi + spread
    mesafe = max(mesafe, (bilgi.trade_stops_level + 5) * bilgi.point + spread)
    sl = mb.fiyat_normalize(giris - mesafe if al else giris + mesafe, bilgi)
    tp = mb.fiyat_normalize(giris + tp_r * mesafe if al else giris - tp_r * mesafe, bilgi) if tp_r else 0.0
    plan = {"sembol": aday["sembol"], "yon": aday["yon"], "giris": giris, "sl": sl, "tp": tp,
            "mesafe": abs(giris - sl), "rr": tp_r, "max_mum": max_mum, "spread": spread, "atr": aday["atr"]}
    ref = referans or plan
    plan["analiz_giris"] = ref.get("analiz_giris", ref["giris"])
    plan["analiz_mesafe"] = ref.get("analiz_mesafe", ref["mesafe"])
    return plan


def bot_pozisyonlari(magic: int) -> list:
    """Botun pozisyonları. MT5 cevap vermezse MT5Hatasi (hata 'pozisyon yok' sanılmaz)."""
    return [p for p in mb.pozisyonlar() if p.magic == magic]


def pozisyon_ozeti(magic: int, siniflar: dict) -> list[dict]:
    out = []
    for p in bot_pozisyonlari(magic):
        out.append({"sembol": p.symbol, "yon": "AL" if p.type == mt5.POSITION_TYPE_BUY else "SAT",
                    "lot": p.volume, "giris": p.price_open, "kar": round(p.profit, 2),
                    "sinif": siniflar.get(p.symbol, "?")})
    return out


def gunluk_durum() -> dict:
    """Bugünün (broker sunucu günü) kâr/zararı ve en kötü senaryo.
    gun_basi    : sunucu gece yarısındaki bakiye (deal geçmişinden geri hesaplanır)
    gerceklesen : bugün kapanan işlemler + komisyon + swap + ücretler
    sonuc       : gerceklesen + açık pozisyonların anlık kâr/zararı
    en_kotu     : gerceklesen + açık pozisyonların hepsi STOP olursa oluşacak sonuç"""
    hesap = mt5.account_info()
    if hesap is None:
        raise mb.MT5Hatasi("account_info başarısız")
    simdi = mb.sunucu_simdi()
    gun_bas = simdi // 86400 * 86400
    # Geniş pencere iste, kesin süzmeyi deal zamanıyla (sunucu saati) kendimiz yapalım
    deals = mt5.history_deals_get(gun_bas - 2 * 86400, simdi + 86400)
    if deals is None:
        raise mb.MT5Hatasi(f"history_deals_get başarısız: {mt5.last_error()}")
    islem = dis = 0.0
    for d in deals:
        if d.time < gun_bas:
            continue
        tutar = d.profit + d.commission + d.swap + getattr(d, "fee", 0.0)
        if d.type in DIS_HAREKET:
            dis += tutar
        else:
            islem += tutar
    en_kotu_acik, sl_siz = 0.0, []
    for p in mb.pozisyonlar():
        if not p.sl:
            sl_siz.append(p.symbol)
            continue
        tip = mt5.ORDER_TYPE_BUY if p.type == mt5.POSITION_TYPE_BUY else mt5.ORDER_TYPE_SELL
        k = mt5.order_calc_profit(tip, p.symbol, p.volume, p.price_open, p.sl)
        if k is None:
            raise mb.MT5Hatasi(f"order_calc_profit başarısız ({p.symbol})")
        en_kotu_acik += k + p.swap
    return {"gun_basi": hesap.balance - islem - dis, "bakiye": hesap.balance,
            "gerceklesen": islem, "sonuc": islem + (hesap.equity - hesap.balance),
            "en_kotu": islem + en_kotu_acik, "sl_siz": sl_siz}


def filtre_ajani(aday: dict, plan: dict | None, cfg, olaylar: list, siniflar: dict,
                 takvim_ok: bool = True) -> tuple[bool, list[str]]:
    """5. AJAN. Herhangi bir kural ihlali = işlem yok. LLM'lerden ÖNCE ve emirden hemen ÖNCE
    çalışır. MT5 verisi alınamazsa işlem yok (güvenli taraf)."""
    try:
        return _filtre(aday, plan, cfg, olaylar, siniflar, takvim_ok)
    except mb.MT5Hatasi as ex:
        return False, [f"MT5 verisi alınamadı: {ex}"]


def _filtre(aday, plan, cfg, olaylar, siniflar, takvim_ok):
    r = cfg["risk"]
    sorun = []
    if DURDUR_DOSYASI.exists():
        sorun.append("DURDUR dosyası var (manuel acil durdurma)")
    if plan is None:
        return False, sorun + ["Plan oluşturulamadı (sembol/tick bilgisi yok)"]

    g = gunluk_durum()
    limit = g["gun_basi"] * r["gunluk_max_zarar_yuzde"] / 100
    yeni_risk = g["bakiye"] * r["islem_basi_risk_yuzde"] / 100
    if g["sonuc"] <= -limit:
        sorun.append(f"Günlük zarar limiti doldu ({g['sonuc']:.2f} <= {-limit:.2f})")
    elif g["sl_siz"]:
        sorun.append(f"Stopsuz açık pozisyon var ({', '.join(g['sl_siz'])}): açık risk hesaplanamıyor")
    elif g["en_kotu"] - yeni_risk < -limit * (1 + 1e-9):     # tolerans: kayan nokta gürültüsü
        sorun.append(f"Açık risk dahil günlük limit aşılır (bugün {g['gerceklesen']:.2f}, açık "
                     f"stoplar {g['en_kotu'] - g['gerceklesen']:.2f}, yeni işlem {-yeni_risk:.2f}, "
                     f"limit {-limit:.2f})")

    poz = bot_pozisyonlari(cfg["magic"])
    if len(poz) >= r["max_acik_pozisyon"]:
        sorun.append(f"Max açık pozisyon ({len(poz)})")
    if any(p.symbol == aday["sembol"] for p in poz):
        sorun.append("Bu sembolde zaten pozisyon var")

    dov = haber.sembol_dovizleri(aday["sembol"], aday["sinif"])
    for dv in sorted(dov):
        n = sum(1 for p in poz if dv in haber.sembol_dovizleri(p.symbol, siniflar.get(p.symbol, "forex")))
        if n >= r["max_ayni_doviz"]:
            sorun.append(f"{dv} dövizine bağlı zaten {n} pozisyon var")

    bilgi = mt5.symbol_info(aday["sembol"])
    if bilgi is not None:
        tm = bilgi.trade_mode
        if tm in (mt5.SYMBOL_TRADE_MODE_DISABLED, mt5.SYMBOL_TRADE_MODE_CLOSEONLY):
            sorun.append("Sembolde yeni işlem açılamıyor (trade_mode)")
        elif tm == mt5.SYMBOL_TRADE_MODE_LONGONLY and aday["yon"] == "SAT":
            sorun.append("Sembolde sadece alış açılabiliyor")
        elif tm == mt5.SYMBOL_TRADE_MODE_SHORTONLY and aday["yon"] == "AL":
            sorun.append("Sembolde sadece satış açılabiliyor")

    if cfg["haber"]["aktif"]:
        if not takvim_ok and cfg["haber"].get("takvim_yoksa_islem_yok", True):
            sorun.append("Güncel ekonomik takvim alınamadı (haber filtresi çalışamıyor)")
        b = haber.blackout_var_mi(olaylar, dov, cfg["haber"]["blackout_dk"])
        if b:
            sorun.append(f"Haber blackout: {b}")

    tick = mt5.symbol_info_tick(aday["sembol"])
    if tick and (tick.ask - tick.bid) > 0.25 * plan["mesafe"]:
        sorun.append("Spread stop mesafesine göre çok geniş")

    return (len(sorun) == 0), sorun


def lot_hesapla(plan: dict, cfg) -> tuple[float, str]:
    """Stop olursa kaybedilecek para <= bakiye x islem_basi_risk_yuzde. LLM'den bağımsız."""
    sym = plan["sembol"]
    bilgi = mt5.symbol_info(sym)
    hesap = mt5.account_info()
    if bilgi is None or hesap is None:
        return 0.0, "MT5 sembol/hesap bilgisi alınamadı"
    risk_para = hesap.balance * cfg["risk"]["islem_basi_risk_yuzde"] / 100
    tip = mt5.ORDER_TYPE_BUY if plan["yon"] == "AL" else mt5.ORDER_TYPE_SELL

    kayip_1lot = mt5.order_calc_profit(tip, sym, 1.0, plan["giris"], plan["sl"])
    if kayip_1lot is None or kayip_1lot == 0:
        tv = getattr(bilgi, "trade_tick_value_loss", 0) or bilgi.trade_tick_value
        if not bilgi.trade_tick_size or not tv:
            return 0.0, "Tick bilgisi yok"
        kayip_1lot = abs(plan["giris"] - plan["sl"]) / bilgi.trade_tick_size * tv
    kayip_1lot = abs(kayip_1lot)
    if kayip_1lot <= 0:
        return 0.0, "1 lot kayıp hesaplanamadı"

    lot = adima_yuvarla(risk_para / kayip_1lot, bilgi.volume_step, bilgi.volume_max)
    if lot < bilgi.volume_min:
        return 0.0, (f"Min lot ({bilgi.volume_min}) bile riskten fazla "
                     f"(izin verilen risk {risk_para:.2f}, min lot riski {kayip_1lot * bilgi.volume_min:.2f})")

    marj = mt5.order_calc_margin(tip, sym, lot, plan["giris"])
    if marj is None:
        return 0.0, "Marjin hesaplanamadı"
    if marj > hesap.margin_free * 0.5:
        return 0.0, f"Marjin yetersiz (gereken {marj:.2f}, serbest {hesap.margin_free:.2f})"
    return lot, f"risk {lot * kayip_1lot:.2f} / izin {risk_para:.2f} {hesap.currency}"
