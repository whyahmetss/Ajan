"""RİSK KATMANI - tamamen kod. LLM buradaki hiçbir sayıyı değiştiremez.
- İşlem planı (giriş / stop / hedef)
- 5. ajan: FİLTRE (sert kurallar)
- Lot hesabı (bakiyenin sabit yüzdesi kadar risk)
"""
import json
import logging
import math
import os
from datetime import datetime

import MetaTrader5 as mt5

from . import haber

log = logging.getLogger("risk")
DURDUR_DOSYASI = "DURDUR"     # Bu isimde bir dosya oluşturursan bot yeni işlem açmaz


def plan_olustur(aday: dict, cfg) -> dict | None:
    r = cfg["risk"]
    bilgi = mt5.symbol_info(aday["sembol"])
    tick = mt5.symbol_info_tick(aday["sembol"])
    if bilgi is None or tick is None:
        return None
    al = aday["yon"] == "AL"
    giris = tick.ask if al else tick.bid
    mesafe = r["sl_atr_carpan"] * aday["atr"]
    # Broker'ın izin verdiği minimum stop mesafesi + spread
    min_mesafe = (bilgi.trade_stops_level + 5) * bilgi.point + (tick.ask - tick.bid)
    mesafe = max(mesafe, min_mesafe)
    d = bilgi.digits
    sl = round(giris - mesafe if al else giris + mesafe, d)
    tp = round(giris + r["tp_r"] * mesafe if al else giris - r["tp_r"] * mesafe, d)
    return {"sembol": aday["sembol"], "yon": aday["yon"], "giris": round(giris, d),
            "sl": sl, "tp": tp, "mesafe": mesafe, "rr": r["tp_r"]}


def bot_pozisyonlari(magic: int) -> list:
    return [p for p in (mt5.positions_get() or []) if p.magic == magic]


def pozisyon_ozeti(magic: int, siniflar: dict) -> list[dict]:
    out = []
    for p in bot_pozisyonlari(magic):
        out.append({"sembol": p.symbol, "yon": "AL" if p.type == mt5.POSITION_TYPE_BUY else "SAT",
                    "lot": p.volume, "giris": p.price_open, "kar": round(p.profit, 2),
                    "sinif": siniflar.get(p.symbol, "?")})
    return out


GUN_DOSYASI = os.path.join("kayitlar", "gun_basi.json")


def gunluk_sonuc() -> tuple[float, float]:
    """(bugünkü değişim, gün başı bakiye). Gün başındaki bakiyeyi dosyaya yazar ve anlık
    equity ile kıyaslar: kapanan + açık işlemler, komisyon, swap dahil. Broker saat
    dilimi karmaşasından etkilenmez. (Bu demo hesabı sadece bot kullanmalı.)"""
    bugun = datetime.now().strftime("%Y-%m-%d")
    hesap = mt5.account_info()
    veri = {}
    try:
        with open(GUN_DOSYASI, encoding="utf-8") as f:
            veri = json.load(f)
    except Exception:
        pass
    if veri.get("gun") != bugun:
        veri = {"gun": bugun, "bakiye": hesap.balance}
        os.makedirs(os.path.dirname(GUN_DOSYASI), exist_ok=True)
        with open(GUN_DOSYASI, "w", encoding="utf-8") as f:
            json.dump(veri, f)
    return hesap.equity - veri["bakiye"], veri["bakiye"]


def filtre_ajani(aday: dict, plan: dict, cfg, olaylar: list, siniflar: dict) -> tuple[bool, list[str]]:
    """5. AJAN. Herhangi bir kural ihlali = işlem yok. LLM'lerden ÖNCE çalışır (para tasarrufu)."""
    r = cfg["risk"]
    sorun = []
    hesap = mt5.account_info()

    if os.path.exists(DURDUR_DOSYASI):
        sorun.append("DURDUR dosyası var (manuel acil durdurma)")

    gunluk, gun_basi = gunluk_sonuc()
    limit = -gun_basi * r["gunluk_max_zarar_yuzde"] / 100
    if gunluk <= limit:
        sorun.append(f"Günlük zarar limiti doldu ({gunluk:.2f} <= {limit:.2f})")

    poz = bot_pozisyonlari(cfg["magic"])
    if len(poz) >= r["max_acik_pozisyon"]:
        sorun.append(f"Max açık pozisyon ({len(poz)})")
    if any(p.symbol == aday["sembol"] for p in poz):
        sorun.append("Bu sembolde zaten pozisyon var")

    dov = haber.sembol_dovizleri(aday["sembol"], aday["sinif"])
    for dv in dov:
        n = sum(1 for p in poz if dv in haber.sembol_dovizleri(p.symbol, siniflar.get(p.symbol, "forex")))
        if n >= r["max_ayni_doviz"]:
            sorun.append(f"{dv} dövizine bağlı zaten {n} pozisyon var")

    if cfg["haber"]["aktif"]:
        b = haber.blackout_var_mi(olaylar, dov, cfg["haber"]["blackout_dk"])
        if b:
            sorun.append(f"Haber blackout: {b}")

    tick = mt5.symbol_info_tick(aday["sembol"])
    if tick and (tick.ask - tick.bid) > 0.25 * plan["mesafe"]:
        sorun.append("Spread stop mesafesine göre çok geniş")

    return (len(sorun) == 0), sorun


def lot_hesapla(plan: dict, cfg, carpan: float = 1.0) -> tuple[float, str]:
    """Stop olursa kaybedilecek para = bakiye x risk%. carpan=0.5 yarım pozisyon."""
    sym = plan["sembol"]
    bilgi = mt5.symbol_info(sym)
    hesap = mt5.account_info()
    risk_para = hesap.balance * cfg["risk"]["islem_basi_risk_yuzde"] / 100 * carpan
    tip = mt5.ORDER_TYPE_BUY if plan["yon"] == "AL" else mt5.ORDER_TYPE_SELL

    kayip_1lot = mt5.order_calc_profit(tip, sym, 1.0, plan["giris"], plan["sl"])
    if kayip_1lot is None or kayip_1lot == 0:
        if not bilgi.trade_tick_size:
            return 0.0, "Tick bilgisi yok"
        kayip_1lot = -abs(plan["giris"] - plan["sl"]) / bilgi.trade_tick_size * bilgi.trade_tick_value
    kayip_1lot = abs(kayip_1lot)
    if kayip_1lot <= 0:
        return 0.0, "1 lot kayıp hesaplanamadı"

    ham = risk_para / kayip_1lot
    adim = bilgi.volume_step or 0.01
    lot = math.floor(ham / adim + 1e-9) * adim     # 1e-9: 0.25/0.01 = 24.9999 kayan nokta hatasına karşı
    ondalik = max(0, -int(math.floor(math.log10(adim))))
    lot = round(min(lot, bilgi.volume_max), ondalik)
    if lot < bilgi.volume_min:
        return 0.0, (f"Min lot ({bilgi.volume_min}) bile riskten fazla "
                     f"(izin verilen risk {risk_para:.2f}, min lot riski {kayip_1lot * bilgi.volume_min:.2f})")

    marj = mt5.order_calc_margin(tip, sym, lot, plan["giris"])
    if marj is not None and marj > hesap.margin_free * 0.5:
        return 0.0, f"Marjin yetersiz (gereken {marj:.2f}, serbest {hesap.margin_free:.2f})"
    return lot, f"risk {risk_para:.2f} {hesap.currency}"
