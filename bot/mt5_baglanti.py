"""MetaTrader 5 bağlantısı, sembol listesi ve mum verisi."""
import os
import logging

import pandas as pd
import MetaTrader5 as mt5

log = logging.getLogger("mt5")

ZAMAN_DILIMLERI = {
    "M1": mt5.TIMEFRAME_M1, "M5": mt5.TIMEFRAME_M5, "M15": mt5.TIMEFRAME_M15,
    "M30": mt5.TIMEFRAME_M30, "H1": mt5.TIMEFRAME_H1, "H4": mt5.TIMEFRAME_H4,
    "D1": mt5.TIMEFRAME_D1,
}
DAKIKA = {"M1": 1, "M5": 5, "M15": 15, "M30": 30, "H1": 60, "H4": 240, "D1": 1440}


def baglan(sadece_demo: bool = True):
    kw = {}
    if os.getenv("MT5_PATH"):
        kw["path"] = os.getenv("MT5_PATH")
    if os.getenv("MT5_LOGIN"):
        kw["login"] = int(os.getenv("MT5_LOGIN"))
        kw["password"] = os.getenv("MT5_PASSWORD", "")
        kw["server"] = os.getenv("MT5_SERVER", "")
    if not mt5.initialize(**kw):
        raise SystemExit(f"MT5'e bağlanılamadı: {mt5.last_error()}  "
                         f"(MT5 terminali açık mı? 'Algo Trading' butonu yeşil mi?)")
    hesap = mt5.account_info()
    if hesap is None:
        raise SystemExit("Hesap bilgisi alınamadı. MT5'te giriş yaptığından emin ol.")
    demo = hesap.trade_mode == mt5.ACCOUNT_TRADE_MODE_DEMO
    log.info("Bağlandı: %s | %s | Bakiye %.2f %s | %s",
             hesap.login, hesap.server, hesap.balance, hesap.currency,
             "DEMO" if demo else "GERÇEK HESAP")
    if sadece_demo and not demo:
        mt5.shutdown()
        raise SystemExit("GÜVENLİK: Bu bir GERÇEK hesap. config.yaml'da sadece_demo: true. Bot durduruldu.")
    return hesap


def sinif_bul(sym, piyasalar: dict):
    yol = (sym.path or "").lower()
    ad = sym.name.lower()
    for sinif, kelimeler in piyasalar.items():
        if any(k in yol for k in kelimeler):
            return sinif
    # Yol bir şey söylemiyorsa isimden tahmin
    if any(k in ad for k in ("btc", "eth", "sol", "xrp", "doge", "ltc", "ada", "bnb")):
        return "kripto"
    if any(k in ad for k in ("xau", "xag", "oil", "brent", "wti")):
        return "emtia"
    if any(k in ad for k in ("us30", "us100", "us500", "nas", "spx", "ger", "de40", "uk100", "jp225", "dax")):
        return "endeks"
    if len(ad) >= 6 and ad[:6].isalpha():
        return "forex"
    return None


def semboller(cfg) -> list[dict]:
    """Taranacak sembolleri döndürür: [{'ad':..., 'sinif':...}, ...]"""
    tum = mt5.symbols_get() or []
    manuel = set(cfg.get("manuel_semboller") or [])
    haric = set(cfg.get("haric_semboller") or [])
    sonuc = []
    for s in tum:
        if s.name in haric:
            continue
        if manuel and s.name not in manuel:
            continue
        if s.trade_mode == mt5.SYMBOL_TRADE_MODE_DISABLED:
            continue
        sinif = sinif_bul(s, cfg["piyasalar"])
        if sinif is None and not manuel:
            continue
        sonuc.append({"ad": s.name, "sinif": sinif or "diger"})
    return sonuc


def mumlar(sembol: str, zd: str, adet: int) -> pd.DataFrame | None:
    if not mt5.symbol_select(sembol, True):
        return None
    r = mt5.copy_rates_from_pos(sembol, ZAMAN_DILIMLERI[zd], 0, adet + 1)
    if r is None or len(r) < 50:
        return None
    df = pd.DataFrame(r)
    df["time"] = pd.to_datetime(df["time"], unit="s")   # broker sunucu saati
    df = df.iloc[:-1]          # son mum henüz kapanmadı, kullanma (gelecek bilgisi sızmasın)
    return df.reset_index(drop=True)


def son_tick_zamanlari(isimler: list[str]) -> dict[str, int]:
    """Her sembolün son tick zamanı (broker SUNUCU saatiyle, UTC değil)."""
    z = {}
    for ad in isimler:
        mt5.symbol_select(ad, True)
        t = mt5.symbol_info_tick(ad)
        z[ad] = int(t.time) if t is not None else 0
    return z


def piyasa_acik_mi(tick_zamani: int, sunucu_simdi: int, zd: str) -> bool:
    """Son fiyat, en taze sembole göre çok eskiyse piyasa kapalıdır (hafta sonu forex gibi).
    Broker saati UTC'den farklı olduğu için gerçek saatle değil, en taze tick ile kıyaslanır."""
    if tick_zamani == 0:
        return False
    return (sunucu_simdi - tick_zamani) / 60 < max(DAKIKA[zd], 15)
