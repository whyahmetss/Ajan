"""MetaTrader 5 bağlantısı, demo kilidi, sunucu saati, sembol listesi ve mum verisi.

Saat notu: MT5'teki bütün zamanlar (mum, tick, deal) broker SUNUCU saatidir ve epoch saniyesi
gibi kodlanmıştır (UTC değildir). Sunucu-UTC farkı en taze tick'ten tahmin edilir ve saklanır.
"""
import json
import logging
import os
import time
from decimal import ROUND_HALF_UP, Decimal

import pandas as pd
import MetaTrader5 as mt5

from .strateji import DAKIKA  # noqa: F401  (dışarıya mb.DAKIKA olarak açık)
from .yollar import yol

log = logging.getLogger("mt5")

ZAMAN_DILIMLERI = {
    "M1": mt5.TIMEFRAME_M1, "M5": mt5.TIMEFRAME_M5, "M15": mt5.TIMEFRAME_M15,
    "M30": mt5.TIMEFRAME_M30, "H1": mt5.TIMEFRAME_H1, "H4": mt5.TIMEFRAME_H4,
    "D1": mt5.TIMEFRAME_D1,
}

GERCEK_HESAP_MESAJI = ("GÜVENLİK: Bağlı hesap DEMO değil. Bu bot gerçek hesapta hiçbir şey "
                       "çalıştırmaz (kod seviyesinde kilit, config ile kapatılamaz). Bot durduruldu.")


class BaglantiHatasi(RuntimeError):
    """MT5 terminaline ya da broker sunucusuna ulaşılamıyor (geçici). Bot yeniden bağlanmayı dener."""


class MT5Hatasi(RuntimeError):
    """MT5 bir sorguya cevap vermedi (None döndü). Güvenli taraf: yeni işlem açılmaz."""


# ------------------------------------------------------------------ bağlantı ve demo kilidi
def baglan():
    """MT5'e bağlanır. Hesap DEMO değilse SystemExit (config'den bağımsız)."""
    kw = {}
    if os.getenv("MT5_PATH"):
        kw["path"] = os.getenv("MT5_PATH")
    if os.getenv("MT5_LOGIN"):
        kw["login"] = int(os.getenv("MT5_LOGIN"))
        kw["password"] = os.getenv("MT5_PASSWORD", "")
        kw["server"] = os.getenv("MT5_SERVER", "")
    _secili.clear()
    if not mt5.initialize(**kw):
        raise BaglantiHatasi(f"MT5'e bağlanılamadı: {mt5.last_error()} "
                             f"(MT5 terminali açık mı? 'Algo Trading' butonu yeşil mi?)")
    hesap = mt5.account_info()
    if hesap is None:
        raise BaglantiHatasi("Hesap bilgisi alınamadı. MT5'te giriş yaptığından emin ol.")
    demo = hesap_demo_mu(hesap)
    log.info("Bağlandı: %s | %s | Bakiye %.2f %s | %s", hesap.login, hesap.server,
             hesap.balance, hesap.currency, "DEMO" if demo else "GERÇEK HESAP")
    if not demo:
        mt5.shutdown()
        raise SystemExit(GERCEK_HESAP_MESAJI)
    return hesap


def hesap_demo_mu(hesap=None) -> bool:
    hesap = hesap if hesap is not None else mt5.account_info()
    return hesap is not None and hesap.trade_mode == mt5.ACCOUNT_TRADE_MODE_DEMO


def demo_kilidi():
    """Her döngü turunda çağrılır: bot çalışırken terminalde başka (gerçek) hesaba geçilirse durdurur."""
    hesap = mt5.account_info()
    if hesap is None:
        raise BaglantiHatasi("Hesap bilgisi alınamadı")
    if not hesap_demo_mu(hesap):
        log.critical(GERCEK_HESAP_MESAJI)
        mt5.shutdown()
        raise SystemExit(GERCEK_HESAP_MESAJI)
    return hesap


def baglanti_saglikli() -> bool:
    """Terminal açık VE broker sunucusuna bağlı mı?"""
    t = mt5.terminal_info()
    return t is not None and bool(getattr(t, "connected", False))


# ------------------------------------------------------------------ sunucu saati
_saat = {"ofset": None, "sabit": False, "dosya": None, "son_tick": 0}


def saat_ayarla(cfg):
    """Sunucu-UTC farkını config'den (sunucu_utc_ofset_saat) ya da önceki çalışmadan yükler."""
    _saat.update(ofset=None, sabit=False, son_tick=0,
                 dosya=yol(cfg.get("log_klasoru", "kayitlar")) / "sunucu_ofset.json")
    sabit = cfg.get("sunucu_utc_ofset_saat")
    if sabit is not None:
        _saat.update(ofset=int(round(float(sabit) * 3600)), sabit=True)
        return
    try:
        with open(_saat["dosya"], encoding="utf-8") as f:
            _saat["ofset"] = int(json.load(f)["ofset_sn"])
    except (OSError, ValueError, KeyError, TypeError):
        pass


def ofset_guncelle(isimler) -> bool:
    """En taze tick'ten sunucu-UTC farkını (tam saat) tahmin eder. Tick 2 dakikadan eskiyse
    (piyasalar kapalı) tahmin yapılmaz, önceki değer kullanılır."""
    en_taze = 0
    for ad in isimler:
        t = mt5.symbol_info_tick(ad)
        if t is not None:
            en_taze = max(en_taze, int(t.time))
    _saat["son_tick"] = max(_saat["son_tick"], en_taze)
    if _saat["sabit"]:
        return True
    if not en_taze:
        return False
    ham = en_taze - time.time()
    saat = round(ham / 3600)
    if abs(ham - saat * 3600) > 120 or not -12 <= saat <= 14:
        return False
    yeni = int(saat * 3600)
    if yeni != _saat["ofset"]:
        log.info("Broker sunucu saati: UTC%+d", saat)
        _saat["ofset"] = yeni
        if _saat["dosya"] is not None:
            try:
                _saat["dosya"].parent.mkdir(parents=True, exist_ok=True)
                with open(_saat["dosya"], "w", encoding="utf-8") as f:
                    json.dump({"ofset_sn": yeni}, f)
            except OSError:
                pass
    return True


def ofset_sn() -> int | None:
    """Sunucu saati - UTC (saniye). Bilinmiyorsa None."""
    return _saat["ofset"]


def sunucu_simdi() -> int:
    """Şu anki broker sunucu saati (epoch gibi kodlanmış). Fark bilinmiyorsa en taze tick zamanı."""
    if _saat["ofset"] is not None:
        return int(time.time()) + _saat["ofset"]
    if _saat["son_tick"]:
        return _saat["son_tick"]
    raise MT5Hatasi("Sunucu saati bilinmiyor (henüz taze tick yok)")


# ------------------------------------------------------------------ sembol sınıfları
DOVIZ_KODLARI = {
    "USD", "EUR", "GBP", "JPY", "CHF", "AUD", "NZD", "CAD", "SEK", "NOK", "DKK", "PLN", "HUF",
    "CZK", "TRY", "ZAR", "MXN", "SGD", "HKD", "CNH", "CNY", "RUB", "ILS", "THB", "INR", "KRW",
    "BRL", "CLP", "COP", "IDR", "TWD", "PHP", "MYR", "SAR", "AED", "KWD", "QAR", "RON", "BGN",
    "ISK",
}
KRIPTO_ONEK = ("BTC", "ETH", "SOL", "XRP", "DOGE", "LTC", "ADA", "BNB", "DOT", "LINK", "AVAX",
               "MATIC", "TRX", "SHIB", "BCH", "XLM", "UNI", "ATOM", "ETC", "XMR", "FIL", "NEAR",
               "TON", "SUI", "PEPE")
EMTIA_ONEK = ("XAU", "XAG", "XPT", "XPD", "XNG", "XCU", "USOIL", "UKOIL", "WTI", "BRENT", "NGAS",
              "NATGAS", "COPPER", "CRUDE", "GOLD", "SILVER")
ENDEKS_ONEK = ("US30", "US100", "US500", "US2000", "USTEC", "NAS100", "SPX", "SP500", "DJ30",
               "GER30", "GER40", "DE30", "DE40", "DAX", "UK100", "FTSE", "JP225", "JPN225",
               "NIKKEI", "FRA40", "CAC40", "EU50", "STOXX50", "ESP35", "SPA35", "IT40", "ITA40",
               "AUS200", "HK50", "CHINA50", "CN50", "SWI20", "NETH25")
VARSAYILAN_HARIC_YOL = ("stock", "share", "equit", "etf", "bond", "hisse")


def sinif_bul(sym, piyasalar: dict, haric_yol=VARSAYILAN_HARIC_YOL):
    """Önce broker'ın klasör yolu (config'deki sıra = öncelik), yol bir şey söylemiyorsa isim.
    Hisse/ETF/tahvil klasörleri elenir. İsimden 'forex' demek için ilk 6 harf iki geçerli
    döviz kodu olmalı (ör. 'NVIDIA' forex sayılmaz)."""
    yol_ = (sym.path or "").lower()
    if any(k in yol_ for k in haric_yol):
        return None
    for sinif, kelimeler in piyasalar.items():
        if any(k in yol_ for k in kelimeler):
            return sinif
    ad = "".join(ch for ch in sym.name.upper() if ch.isalnum())
    harf = "".join(ch for ch in ad if ch.isalpha())
    if ad.startswith(KRIPTO_ONEK):
        return "kripto"
    if ad.startswith(EMTIA_ONEK):
        return "emtia"
    if ad.startswith(ENDEKS_ONEK):
        return "endeks"
    if len(harf) >= 6 and harf[:3] in DOVIZ_KODLARI and harf[3:6] in DOVIZ_KODLARI:
        return "forex"
    return None


def semboller(cfg) -> list[dict]:
    """Taranacak sembolleri döndürür: [{'ad':..., 'sinif':...}, ...]"""
    tum = mt5.symbols_get()
    if tum is None:
        raise MT5Hatasi(f"symbols_get başarısız: {mt5.last_error()}")
    manuel = set(cfg.get("manuel_semboller") or [])
    haric = set(cfg.get("haric_semboller") or [])
    haric_yol = tuple(cfg.get("haric_yol_kelimeleri", VARSAYILAN_HARIC_YOL))
    sonuc = []
    for s in tum:
        if s.name in haric or (manuel and s.name not in manuel):
            continue
        if s.trade_mode in (mt5.SYMBOL_TRADE_MODE_DISABLED, mt5.SYMBOL_TRADE_MODE_CLOSEONLY):
            continue
        sinif = sinif_bul(s, cfg["piyasalar"], haric_yol)
        if sinif is None and not manuel:
            continue
        sonuc.append({"ad": s.name, "sinif": sinif or "diger"})
    return sonuc


# ------------------------------------------------------------------ fiyat, pozisyon, mum
def fiyat_normalize(fiyat: float, bilgi) -> float:
    """Fiyatı sembolün tick adımına (trade_tick_size) yuvarlar; endekslerde adım 0.25/0.5/1 olabilir."""
    ts = float(getattr(bilgi, "trade_tick_size", 0) or 0)
    if ts <= 0:
        return round(float(fiyat), bilgi.digits)
    adim = Decimal(repr(ts))
    q = (Decimal(repr(float(fiyat))) / adim).to_integral_value(rounding=ROUND_HALF_UP) * adim
    return round(float(q), bilgi.digits)


def pozisyonlar() -> list:
    """Açık pozisyonlar. MT5 hata verirse (None) MT5Hatasi: hata 'pozisyon yok' sanılmasın."""
    p = mt5.positions_get()
    if p is None:
        raise MT5Hatasi(f"positions_get başarısız: {mt5.last_error()}")
    return list(p)


_secili: set = set()


def sec(ad: str) -> bool:
    """Sembolü Market Watch'a bir kez ekler (her taramada tekrar tekrar çağırmaz)."""
    if ad in _secili:
        return True
    if mt5.symbol_select(ad, True):
        _secili.add(ad)
        return True
    return False


def tickler(isimler) -> dict:
    """{sembol: son tick (yoksa None)}"""
    out = {}
    for ad in isimler:
        sec(ad)
        out[ad] = mt5.symbol_info_tick(ad)
    return out


def mum_baslangici(zaman: int, zd: str) -> int:
    """Verilen sunucu zamanının içinde bulunduğu mumun açılış zamanı (H1/H4/D1 sunucu saatine hizalı)."""
    p = DAKIKA[zd] * 60
    return int(zaman) // p * p


def mumlar(sembol: str, zd: str, adet: int, tick_zamani: int | None = None) -> pd.DataFrame | None:
    """Sadece KAPANMIŞ mumlar (gelecek bilgisi sızmasın).
    Kapanmış mum = açılışı, sembolün son tick'inin içinde bulunduğu mumdan ÖNCE olan mum.
    Tick zamanı sunucu saatinin alt sınırıdır; bu yüzden bilgisayar saati kaysa bile oluşmakta
    olan mum asla 'kapanmış' sayılmaz."""
    if not sec(sembol):
        return None
    if tick_zamani is None:
        t = mt5.symbol_info_tick(sembol)
        if t is None:
            return None
        tick_zamani = int(t.time)
    r = mt5.copy_rates_from_pos(sembol, ZAMAN_DILIMLERI[zd], 0, adet + 1)
    if r is None or len(r) == 0:
        return None
    df = pd.DataFrame(r)
    df = df[df["time"] < mum_baslangici(tick_zamani, zd)].iloc[-adet:].copy()
    if len(df) < 50:
        return None
    df["zaman_sn"] = df["time"].astype("int64")            # sunucu saati, epoch saniyesi gibi
    df["time"] = pd.to_datetime(df["time"], unit="s")      # broker SUNUCU saati (UTC değil)
    return df.reset_index(drop=True)
