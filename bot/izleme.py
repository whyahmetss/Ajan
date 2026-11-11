"""5. AŞAMA - İZLEME: açık pozisyonları yönetir.

Backtest ile BİREBİR aynı kural: her mum KAPANDIĞINDA, kapanan mumun verisiyle bir kez değerlendirilir.
- Zaman stopu (gunluk_kirilim): giriş mumundan itibaren max_mum mum kapandıysa pozisyon piyasadan
  kapatılır. Bu stratejide başa baş / takip eden stop YOKTUR.
- Başa baş + takip eden stop (trend_skor): alış için kapanış, satış için kapanış + mumun spread'i.
  Stop SADECE kâr yönünde hareket eder, asla geri gitmez.
Mum içi anlık fiyatla tetiklenmez; böylece canlı sonuçlar backtest'le karşılaştırılabilir ve broker'a
her birkaç saniyede bir istek gitmez."""
import logging
import re

import MetaTrader5 as mt5

from . import emir, indikator, mt5_baglanti as mb, risk, strateji
from .strateji import yeni_stop  # backtest AYNI fonksiyonu kullanır

log = logging.getLogger("izleme")

_islenen: dict = {}      # pozisyon ticket -> en son değerlendirilen mumun açılış zamanı


def pozisyonlari_yonet(cfg):
    """Ana döngüde sık çağrılır; her pozisyon için her kapanan mumu bir kez işler."""
    acik = risk.bot_pozisyonlari(cfg["magic"])
    biletler = {p.ticket for p in acik}
    for t in [t for t in _islenen if t not in biletler]:
        del _islenen[t]
    for p in acik:
        try:
            _pozisyon_yonet(p, cfg)
        except Exception:
            log.exception("Pozisyon yönetilemedi: %s #%s", p.symbol, p.ticket)


def zaman_stopu(p, cfg) -> int:
    """Pozisyonun zaman stopu (mum). Emir yorumundaki 'z<N>' etiketinden okunur; etiket yoksa (broker
    yorumu değiştirdiyse) aktif strateji gunluk_kirilim ise onun ayarı kullanılır. 0 = zaman stopu yok."""
    m = re.search(r"\bz(\d+)\b", getattr(p, "comment", "") or "")
    if m:
        return int(m.group(1))
    ad, sp = strateji.aktif(cfg)
    if ad == "gunluk_kirilim" and "ajanbot s" not in (getattr(p, "comment", "") or ""):
        return strateji.gunluk_kirilim_cikis(sp)["max_mum"]
    return 0


def _pozisyon_yonet(p, cfg):
    r, zd = cfg["risk"], cfg["zaman_dilimi"]
    periyot = mb.DAKIKA[zd] * 60
    bilgi = mt5.symbol_info(p.symbol)
    tick = mt5.symbol_info_tick(p.symbol)
    if bilgi is None or tick is None:
        return
    hedef = mb.mum_baslangici(int(tick.time), zd) - periyot     # en son kapanmış mum
    giris_mumu = mb.mum_baslangici(int(p.time), zd)
    if hedef < giris_mumu or _islenen.get(p.ticket, -1) >= hedef:
        return          # giriş mumu henüz kapanmadı ya da bu mum zaten değerlendirildi
    z = zaman_stopu(p, cfg)
    df = mb.mumlar(p.symbol, zd, max(200, z + 50), int(tick.time))
    if df is None:
        return
    son = df.iloc[-1]
    if int(son.zaman_sn) < giris_mumu:
        return

    if z:               # zaman stopu olan strateji: başa baş / takip yok
        gecen = int((df["zaman_sn"] >= giris_mumu).sum())       # giriş mumundan beri kapanan mum sayısı
        _islenen[p.ticket] = hedef                               # başarısız olursa bir sonraki mumda tekrar denenir
        if gecen >= z:
            ok, mesaj = emir.pozisyon_kapat(p, cfg, f"zaman {z}")
            (log.info if ok else log.warning)("%s zaman stopu (%d mum): %s", p.symbol, gecen, mesaj)
        return

    _islenen[p.ticket] = hedef
    if p.tp == 0:
        return
    al = p.type == mt5.POSITION_TYPE_BUY
    # İlk risk mesafesi TP'den geri hesaplanır (TP hiç değiştirilmez): TP = giriş ± tp_r * R
    R = abs(p.tp - p.price_open) / r["tp_r"]
    if R <= 0:
        return
    atr = float(indikator.atr(df).iloc[-1])
    sp = float(son.spread) * bilgi.point
    yeni = yeni_stop(al, p.price_open, R, float(son.close), sp, atr, bilgi.point, r)
    if yeni is None:
        return
    yeni = mb.fiyat_normalize(yeni, bilgi)
    if p.sl and ((al and yeni <= p.sl) or (not al and yeni >= p.sl)):
        return          # sadece iyileştir, asla geri alma
    fiyat = tick.bid if al else tick.ask
    min_mesafe = (bilgi.trade_stops_level + 2) * bilgi.point
    if (fiyat - yeni if al else yeni - fiyat) < min_mesafe:
        log.info("%s önerilen stop %s fiyata çok yakın/ötesinde (fiyat %s), değiştirilmedi",
                 p.symbol, yeni, fiyat)
        return
    donma = (getattr(bilgi, "trade_freeze_level", 0) or 0) * bilgi.point
    if donma and any(seviye and abs(fiyat - seviye) <= donma for seviye in (p.sl, p.tp)):
        log.info("%s fiyat dondurma seviyesinde, stop değiştirilemez", p.symbol)
        return
    kar_r = ((son.close - p.price_open) if al else (p.price_open - son.close - sp)) / R
    if emir.stop_guncelle(p, yeni):
        log.info("%s stop %s -> %s (mum kapanışında kâr %.2fR)", p.symbol, p.sl, yeni, kar_r)
