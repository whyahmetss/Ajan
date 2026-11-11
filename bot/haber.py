"""Ekonomik takvim (ForexFactory haftalık JSON).
Takvim alınamazsa ya da önbellek bayatsa takvim() bunu bildirir (ok=False); filtre ajanı bu
durumda varsayılan olarak yeni işlem açmaz (config: haber.takvim_yoksa_islem_yok)."""
import logging
import time
from datetime import datetime, timedelta, timezone

import requests

log = logging.getLogger("haber")
_onbellek = {"zaman": 0.0, "veri": [], "son_deneme": 0.0}

ENDEKS_DOVIZ = {
    "US30": "USD", "US100": "USD", "US500": "USD", "NAS": "USD", "SPX": "USD", "USTEC": "USD",
    "DJ": "USD", "GER": "EUR", "DE40": "EUR", "DAX": "EUR", "EU50": "EUR", "STOXX": "EUR",
    "FRA": "EUR", "UK100": "GBP", "FTSE": "GBP", "JP225": "JPY", "JPN": "JPY", "NIKKEI": "JPY",
    "AUS200": "AUD", "HK50": "HKD",
}
DOVIZLER = {"USD", "EUR", "GBP", "JPY", "CHF", "AUD", "NZD", "CAD", "CNH", "TRY", "SEK", "NOK",
            "XAU", "XAG", "BTC", "ETH", "HKD", "MXN", "ZAR", "PLN", "SGD"}


def sembol_dovizleri(sembol: str, sinif: str) -> set[str]:
    s = sembol.upper()
    if sinif == "endeks":
        for k, v in ENDEKS_DOVIZ.items():
            if k in s:
                return {v}
        return {"USD"}
    if sinif == "kripto":
        return {"USD"}          # kripto genelde ABD verilerine tepki verir
    temiz = "".join(ch for ch in s if ch.isalpha())
    b, q = temiz[:3], temiz[3:6]
    return {x for x in (b, q) if x in DOVIZLER} or {"USD"}


def _hafta(ts: float) -> int:
    """Pazar başlangıçlı hafta numarası (ForexFactory 'thisweek' haftası, UTC)."""
    return int((ts // 86400 + 4) // 7)       # 1970-01-01 Perşembe; +4 -> hafta Pazar başlar


def takvim(cfg) -> tuple[list[dict], bool]:
    """(olaylar, takvim_ok). takvim_ok=False: güncel takvim yok, haber filtresi güvenilmez."""
    h = cfg["haber"]
    if not h["aktif"]:
        return [], True
    simdi = time.time()
    # Başarılı veri saatte bir yenilenir; başarısız denemeden sonra 5 dk beklenir (rate limit)
    if simdi - _onbellek["zaman"] >= 3600 and simdi - _onbellek["son_deneme"] >= 300:
        _onbellek["son_deneme"] = simdi
        try:
            r = requests.get(h["kaynak"], timeout=10, headers={"User-Agent": "Mozilla/5.0"})
            r.raise_for_status()
            veri = []
            for e in r.json():
                try:
                    z = datetime.fromisoformat(e["date"])
                except Exception:
                    continue
                if z.tzinfo is None:         # saat dilimi yoksa yorumlanamaz, atla
                    continue
                veri.append({"baslik": e.get("title"), "doviz": e.get("country"),
                             "zaman": z.astimezone(timezone.utc), "etki": e.get("impact"),
                             "beklenti": e.get("forecast"), "onceki": e.get("previous")})
            _onbellek.update(zaman=simdi, veri=veri)
            log.info("Ekonomik takvim güncellendi: %d olay", len(veri))
        except Exception as ex:
            log.warning("Takvim alınamadı (%s)", ex)
    yas = simdi - _onbellek["zaman"]
    ok = (_onbellek["zaman"] > 0 and yas <= h.get("max_bayatlik_saat", 6) * 3600
          and _hafta(_onbellek["zaman"]) == _hafta(simdi))
    if not ok:
        log.warning("Ekonomik takvim güncel değil (son başarılı alım: %s)",
                    "hiç" if not _onbellek["zaman"] else f"{yas / 3600:.1f} saat önce")
    return _onbellek["veri"], ok


def yakin_olaylar(olaylar, dovizler: set[str], saat: int = 24) -> list[dict]:
    simdi = datetime.now(timezone.utc)
    son = simdi + timedelta(hours=saat)
    out = []
    for e in olaylar:
        if e["doviz"] in dovizler and e["etki"] in ("High", "Medium") and simdi - timedelta(hours=2) <= e["zaman"] <= son:
            out.append({"ne": e["baslik"], "doviz": e["doviz"], "etki": e["etki"],
                        "kac_dk_sonra": int((e["zaman"] - simdi).total_seconds() / 60),
                        "beklenti": e["beklenti"], "onceki": e["onceki"]})
    return out


def blackout_var_mi(olaylar, dovizler: set[str], dk: int) -> str | None:
    simdi = datetime.now(timezone.utc)
    for e in olaylar:
        if e["doviz"] in dovizler and e["etki"] == "High":
            if abs((e["zaman"] - simdi).total_seconds()) <= dk * 60:
                return f"{e['doviz']} {e['baslik']} ({e['zaman']:%H:%M} UTC)"
    return None
