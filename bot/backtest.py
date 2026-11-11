"""BACKTEST - stratejinin dürüstlük testi.

Ne test edilir: tarayıcının kural tabanlı stratejisi (canlıda kullanılan AYNI strateji.yon_skoru),
stop/hedef/başa baş/takip eden stop (canlı izlemeyle AYNI strateji.yeni_stop), spread,
komisyon, swap ve portföy kuralları (max pozisyon, aynı döviz, tur başına max aday, günlük limit,
min lot uygunluğu, spread/stop oranı).
Ne test EDİLMEZ: LLM ajanları (LLM'ler geçmişi eğitimden "biliyor"; katkıları demo kayıtlarıyla
ölçülür), haber filtresi (geçmiş takvim yok), marjin kontrolü.

Kurallar (kendimizi kandırmamak için):
- Sinyal mum KAPANIŞINDA oluşur, giriş BİR SONRAKİ mumun açılışından yapılır. Sonraki mum
  ardışık değilse (seans / hafta sonu boşluğu) giriş YOK: canlıda kapalı piyasaya emir atılamaz.
- Alışta spread girişe, satışta çıkışa eklenir. Komisyon ve swap R'den düşülür.
- Aynı mumda hem stop hem hedef görülürse STOP sayılır (kötümser varsayım).
- Fiyat stopun ötesinde açılırsa (gap) çıkış açılış fiyatından yapılır.
- Bölümler TAKVİME göredir (bütün semboller için aynı tarihler). Son %20 KİLİTLİ: sınırı ilk
  çalıştırmada kayitlar/kilit.json'a yazılır ve sabit kalır; ayarlar bitene kadar dokunulmaz,
  en sonda BİR kez (--kilidi-ac) test edilir. Kalan dönemin ilk %70'i eğitim, son %30'u test.
- Max düşüş, işlemler ÇIKIŞ ZAMANINA göre sıralanarak hesaplanır.
- Her min_skor eşiği AYRI simüle edilir (eşik değişince işlem dizisi de değişir).
"""
import hashlib
import inspect
import json
import logging
import math
import re
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from . import haber, indikator, strateji
from .strateji import DAKIKA, UST_TREND_MIN_BAR, adima_yuvarla, trend_degeri, yeni_stop, yon_skoru
from .yollar import yol

log = logging.getLogger("backtest")

BILGI_ALANLARI = ("point", "digits", "trade_tick_size", "trade_tick_value", "trade_tick_value_loss",
                  "trade_contract_size", "volume_min", "volume_step", "volume_max", "trade_stops_level",
                  "swap_mode", "swap_long", "swap_short", "swap_rollover3days", "currency_base",
                  "currency_profit", "path")
MUM_KOLONLARI = ["time", "open", "high", "low", "close", "tick_volume", "spread"]
GEREKLI_BILGI = ("point", "trade_tick_size", "trade_tick_value", "volume_min", "volume_step", "volume_max")


# ------------------------------------------------------------------ parametreler ve veri
@dataclass(frozen=True)
class Prm:
    sl_atr_carpan: float
    tp_r: float
    breakeven_r: float
    trailing_baslangic_r: float
    trailing_atr_carpan: float
    min_skor: int
    max_spread_atr_orani: float
    risk_yuzde: float
    spread_carpani: float = 1.0
    komisyon_lot: float = 0.0       # hesap para birimi, lot başına gidiş-dönüş
    swap: bool = True
    bakiye: float = 10000.0         # min lot uygunluğu ve % getiri için varsayılan hesap

    @classmethod
    def cfgden(cls, cfg, **degisen) -> "Prm":
        r, t, bt = cfg["risk"], cfg["tarayici"], cfg.get("backtest") or {}
        p = cls(sl_atr_carpan=r["sl_atr_carpan"], tp_r=r["tp_r"], breakeven_r=r["breakeven_r"],
                trailing_baslangic_r=r["trailing_baslangic_r"], trailing_atr_carpan=r["trailing_atr_carpan"],
                min_skor=t["min_skor"], max_spread_atr_orani=t["max_spread_atr_orani"],
                risk_yuzde=r["islem_basi_risk_yuzde"], spread_carpani=bt.get("spread_carpani", 1.0),
                komisyon_lot=bt.get("komisyon_lot", 0.0), swap=bt.get("swap", True),
                bakiye=bt.get("bakiye", 10000.0))
        return replace(p, **degisen)

    def stop_kurallari(self) -> dict:
        return {"breakeven_r": self.breakeven_r, "trailing_baslangic_r": self.trailing_baslangic_r,
                "trailing_atr_carpan": self.trailing_atr_carpan}


@dataclass
class SembolVerisi:
    ad: str
    sinif: str
    bilgi: dict
    t: np.ndarray           # mum açılış zamanı (broker sunucu saati, epoch saniyesi gibi)
    o: np.ndarray
    h: np.ndarray
    l: np.ndarray
    c: np.ndarray
    sp_pt: np.ndarray       # mumun spread'i (point)
    atr: np.ndarray
    yon: np.ndarray
    skor: np.ndarray
    ardisik: np.ndarray     # i+1. mum i. mumun hemen ardından mı geliyor?
    hafta_sonu: bool        # Cumartesi mumu var mı (7/24 piyasa)
    periyot: int
    isinma: int


@dataclass(frozen=True)
class Kesim:
    """Giriş penceresi [bas, bit) ve çıkışlar için kullanılabilecek son zaman (veri_bit hariç)."""
    ad: str
    bas: int
    bit: int
    veri_bit: int

    def son_index(self, v: SembolVerisi) -> int:
        return int(np.searchsorted(v.t, self.veri_bit, side="left")) - 1


def _ust_trend_dizisi(t_alt: np.ndarray, ust: pd.DataFrame, p_alt: int, p_ust: int) -> np.ndarray:
    """Her alt mum için, o mum KAPANDIĞINDA kapanmış son üst-zaman-dilimi mumunun trendi
    (canlıdaki strateji.ust_trend ile aynı kural: 210 mumdan azsa 0)."""
    if ust is None or len(ust) == 0:
        return np.zeros(len(t_alt), np.int8)
    u = indikator.ekle(ust.sort_values("time").reset_index(drop=True))
    tr = np.asarray(trend_degeri(u["ema50"].to_numpy(), u["ema200"].to_numpy(), u["close"].to_numpy()),
                    dtype=np.int8)
    tr[: UST_TREND_MIN_BAR - 1] = 0
    hazir = u["time"].to_numpy(np.int64) + p_ust                  # üst mumun kapanış anı
    pos = np.searchsorted(hazir, t_alt + p_alt, side="right") - 1  # kapanmış son üst mum
    return np.where(pos >= 0, tr[np.clip(pos, 0, None)], 0).astype(np.int8)


def _kod_ozeti() -> str:
    kaynak = (inspect.getsource(strateji.yon_skoru) + inspect.getsource(strateji.trend_degeri)
              + inspect.getsource(indikator) + inspect.getsource(_ust_trend_dizisi) + str(UST_TREND_MIN_BAR))
    return hashlib.sha1(kaynak.encode()).hexdigest()[:12]


def sinyaller(ad: str, d: pd.DataFrame, ut: np.ndarray, bas: int, onbellek: Path | None):
    """Her mum için (yön, skor): canlıdaki AYNI strateji.yon_skoru ile. Yavaş olduğu için
    veri + kod özetine göre diske önbelleklenir (kod değişirse önbellek geçersiz olur)."""
    h = hashlib.sha1()
    for x in (ad.encode(), d["time"].to_numpy(np.int64).tobytes(), d["close"].to_numpy().tobytes(),
              ut.tobytes(), str(bas).encode(), _kod_ozeti().encode()):
        h.update(x)
    dosya = None
    if onbellek is not None:
        dosya = Path(onbellek) / f"sinyal_{re.sub(r'[^A-Za-z0-9]', '_', ad)}_{h.hexdigest()[:16]}.npz"
        if dosya.exists():
            z = np.load(dosya)
            return z["yon"], z["skor"]
    n = len(d)
    yon, skor = np.zeros(n, np.int8), np.zeros(n, np.int16)
    for i in range(max(bas, 2), n):
        p = d.iloc[i - 2:i + 1]
        sa, _ = yon_skoru(p, 1, int(ut[i]))
        ss, _ = yon_skoru(p, -1, int(ut[i]))
        yon[i], skor[i] = (1, sa) if sa >= ss else (-1, ss)
    if dosya is not None:
        dosya.parent.mkdir(parents=True, exist_ok=True)
        np.savez_compressed(dosya, yon=yon, skor=skor)
    return yon, skor


def veri_hazirla(ad: str, sinif: str, bilgi: dict, alt: pd.DataFrame, ust: pd.DataFrame, cfg,
                 onbellek: Path | None = None) -> SembolVerisi | None:
    zd, ust_zd = cfg["zaman_dilimi"], cfg["ust_zaman_dilimi"]
    p, pu = DAKIKA[zd] * 60, DAKIKA[ust_zd] * 60
    isinma = int((cfg.get("backtest") or {}).get("isinma_bar", cfg["tarama_bar"]))
    eksik = [a for a in GEREKLI_BILGI if not (bilgi.get(a) or 0) > 0]
    if eksik:
        log.warning("%s: sembol bilgisi eksik/geçersiz (%s), atlandı", ad, ", ".join(eksik))
        return None
    alt = alt.sort_values("time").drop_duplicates("time").reset_index(drop=True)
    if len(alt) < isinma + 100:
        log.warning("%s: yetersiz veri (%d mum, ısınma %d)", ad, len(alt), isinma)
        return None
    d = indikator.ekle(alt)
    t = alt["time"].to_numpy(np.int64)
    ut = _ust_trend_dizisi(t, ust, p, pu)
    yon, skor = sinyaller(ad, d, ut, isinma, onbellek)
    ardisik = np.zeros(len(t), bool)
    ardisik[:-1] = (t[1:] - t[:-1]) == p
    cumartesi = (((t // 86400) + 3) % 7) == 5                    # 1970-01-01 Perşembe; Pzt=0
    return SembolVerisi(ad=ad, sinif=sinif, bilgi=bilgi, t=t, o=d["open"].to_numpy(float),
                        h=d["high"].to_numpy(float), l=d["low"].to_numpy(float), c=d["close"].to_numpy(float),
                        sp_pt=d["spread"].to_numpy(float), atr=d["atr"].to_numpy(float), yon=yon, skor=skor,
                        ardisik=ardisik, hafta_sonu=bool(cumartesi.mean() > 0.01), periyot=p, isinma=isinma)


# ------------------------------------------------------------------ maliyetler
def _gece_sayisi(giris_t: int, cikis_t: int, uc_kat_gun: int, hafta_sonu: bool) -> int:
    """Pozisyonun taşındığı swap gecesi (sunucu gece yarıları). 5 gün açık piyasalarda Cumartesi/Pazar
    geceleri sayılmaz, swap_rollover3days günü 3 kat sayılır (hafta sonu karşılığı)."""
    toplam = 0
    for g in range(giris_t // 86400 + 1, cikis_t // 86400 + 1):   # g. günün başındaki gece yarısı
        biten = (g - 1 + 4) % 7                                    # biten günün haftanın günü (Pazar=0)
        if hafta_sonu:
            toplam += 1
        elif biten not in (6, 0):
            toplam += 3 if biten == uc_kat_gun else 1
    return toplam


def _swap_R(v: SembolVerisi, prm: Prm, al: bool, giris_t: int, cikis_t: int, m: float, fiyat: float) -> float:
    """Swap'ın R cinsinden toplamı (pozitif = kazanç). Sembolün BUGÜNKÜ swap oranı kullanılır."""
    b = v.bilgi
    mod, deger = b.get("swap_mode") or 0, b.get("swap_long" if al else "swap_short") or 0.0
    if not prm.swap or not deger or mod == 0:
        return 0.0
    gece = _gece_sayisi(giris_t, cikis_t, b.get("swap_rollover3days", 3), v.hafta_sonu)
    if gece == 0:
        return 0.0
    if mod == 1:                                   # point
        birim = deger * b["point"] / m
    elif mod == 4:                                 # hesap para birimi
        birim = deger * b["trade_tick_size"] / (m * b["trade_tick_value"])
    elif mod in (5, 6):                            # yıllık faiz %
        birim = deger / 100 / 360 * fiyat / m
    elif mod in (2, 3):                            # baz / marjin para birimi
        birim = deger * fiyat / (b["trade_contract_size"] * m)
    else:                                          # yeniden açma modları modellenmedi
        return 0.0
    return gece * birim


# ------------------------------------------------------------------ işlem simülasyonu
def islem_simule(v: SembolVerisi, i: int, prm: Prm, son: int | None = None) -> dict | None:
    """i. mumun kapanışındaki sinyalle i+1. mumun açılışında giriş. None = canlıda da açılmazdı
    (spread stop mesafesinin %25'inden büyük ya da min lot bile riskten fazla)."""
    n_son = len(v.t) - 1 if son is None else min(son, len(v.t) - 1)
    j = i + 1
    if j > n_son:
        return None
    b, mult = v.bilgi, prm.spread_carpani
    point = b["point"]
    o, h, l, c, atr, sp_pt = v.o, v.h, v.l, v.c, v.atr, v.sp_pt
    al = bool(v.yon[i] > 0)
    spj = sp_pt[j] * point * mult
    m = max(prm.sl_atr_carpan * atr[i], ((b.get("trade_stops_level") or 0) + 5) * point + spj)
    if not m > 0 or spj > 0.25 * m:
        return None
    kayip_lot = m / b["trade_tick_size"] * b["trade_tick_value"]       # 1 lotun stopta kaybı
    if not kayip_lot > 0:
        return None
    lot = adima_yuvarla(prm.bakiye * prm.risk_yuzde / 100 / kayip_lot, b["volume_step"], b["volume_max"])
    if lot <= 0 or lot < b["volume_min"]:
        return None

    giris = o[j] + spj if al else o[j]
    sl = giris - m if al else giris + m
    tp = giris + prm.tp_r * m if al else giris - prm.tp_r * m
    kur = prm.stop_kurallari()
    durum, be_k, cikis, kc = "SL", None, None, None
    for k in range(j, n_son + 1):
        spk = sp_pt[k] * point * mult
        if al:
            if k > j and o[k] <= sl:
                cikis, kc = o[k], k; break
            if l[k] <= sl:
                cikis, kc = sl, k; break
            if k > j and o[k] >= tp:
                cikis, kc, durum = o[k], k, "TP"; break
            if h[k] >= tp:
                cikis, kc, durum = tp, k, "TP"; break
        else:
            ao, ah, al_ = o[k] + spk, h[k] + spk, l[k] + spk
            if k > j and ao >= sl:
                cikis, kc = ao, k; break
            if ah >= sl:
                cikis, kc = sl, k; break
            if k > j and ao <= tp:
                cikis, kc, durum = ao, k, "TP"; break
            if al_ <= tp:
                cikis, kc, durum = tp, k, "TP"; break
        yeni = yeni_stop(al, giris, m, c[k], spk, atr[k], point, kur)
        if yeni is not None and ((al and yeni > sl) or (not al and yeni < sl)):
            sl = yeni
            kar = (c[k] - giris) if al else (giris - (c[k] + spk))
            durum = "TRAIL" if kar >= kur["trailing_baslangic_r"] * m else "BE"
            if be_k is None and ((sl - giris) if al else (giris - sl)) >= 0:
                be_k = k
    if cikis is None:                          # veri bitti: son kapanıştan değerle
        kc, durum = n_son, "SON"
        cikis = c[n_son] if al else c[n_son] + sp_pt[n_son] * point * mult
    R_brut = ((cikis - giris) if al else (giris - cikis)) / m
    kom = prm.komisyon_lot / kayip_lot
    swp = _swap_R(v, prm, al, int(v.t[j]), int(v.t[kc]), m, giris)
    return {"sembol": v.ad, "sinif": v.sinif, "yon": "AL" if al else "SAT", "skor": int(v.skor[i]),
            "i": int(i), "k": int(kc), "giris_t": int(v.t[j]), "cikis_t": int(v.t[kc]), "cikis": durum,
            "R_brut": float(R_brut), "R": float(R_brut - kom + swp), "maliyet_R": float(kom - swp),
            "risk_yuzde": float(lot * kayip_lot / prm.bakiye * 100), "be_k": be_k}


def _adaylar(v: SembolVerisi, prm: Prm, kesim: Kesim) -> dict:
    """Tarayıcı koşullarını sağlayan her sinyal mumu -> işlem sonucu. None: canlıda aday listesinde
    yer kaplar ama filtre/lot aşamasında reddedilirdi."""
    out, son = {}, kesim.son_index(v)
    point, mult = v.bilgi["point"], prm.spread_carpani
    for i in np.flatnonzero((v.skor >= prm.min_skor) & v.ardisik):
        i = int(i)
        if i < v.isinma or i + 1 > son or not (kesim.bas <= v.t[i + 1] < kesim.bit):
            continue
        a = v.atr[i]
        if not a > 0 or v.sp_pt[i] * point * mult / a > prm.max_spread_atr_orani:
            continue
        out[i] = islem_simule(v, i, prm, son)
    return out


def tek_simule(v: SembolVerisi, prm: Prm, kesim: Kesim) -> list[dict]:
    """Sembol tek başına: aynı anda tek pozisyon; çıkış mumunun kapanışında yeni sinyal alınabilir
    (canlıda pozisyon mum içinde kapanır, tarama mum kapanışında yapılır)."""
    out, i_min = [], -1
    for i, tr in sorted(_adaylar(v, prm, kesim).items()):
        if tr is None or i < i_min:
            continue
        out.append(tr)
        i_min = tr["k"]
    return out


def portfoy_simule(veriler: list[SembolVerisi], prm: Prm, kesim: Kesim, cfg) -> list[dict]:
    """Canlıdaki portföy kuralları, zaman sırasıyla: her mum kapanışında skor sırasına göre en iyi
    max_aday aday; sembolde pozisyon varsa, max pozisyon, aynı döviz limiti ya da açık risk dahil
    günlük zarar limiti aşılıyorsa işlem açılmaz."""
    r, max_aday = cfg["risk"], cfg["tarayici"]["max_aday"]
    tarama: dict = {}
    for sira, v in enumerate(veriler):
        for i, tr in _adaylar(v, prm, kesim).items():
            tarama.setdefault(int(v.t[i]) + v.periyot, []).append((int(v.skor[i]), sira, v, tr))
    acik, alinan, gunluk = [], [], {}
    for T in sorted(tarama):
        kalan = []
        for p in acik:
            if p["kapanis_T"] <= T:
                g = p["tr"]["cikis_t"] // 86400
                gunluk[g] = gunluk.get(g, 0.0) + p["tr"]["R"] * p["tr"]["risk_yuzde"]
            else:
                kalan.append(p)
        acik = kalan
        for _, _, v, tr in sorted(tarama[T], key=lambda x: (-x[0], x[1]))[:max_aday]:
            if tr is None or any(p["tr"]["sembol"] == v.ad for p in acik):
                continue
            if len(acik) >= r["max_acik_pozisyon"]:
                continue
            dov = haber.sembol_dovizleri(v.ad, v.sinif)
            if any(sum(1 for p in acik if d in p["dov"]) >= r["max_ayni_doviz"] for d in dov):
                continue
            acik_risk = sum(p["tr"]["risk_yuzde"] for p in acik if p["be_T"] is None or p["be_T"] > T)
            if gunluk.get(T // 86400, 0.0) - acik_risk - prm.risk_yuzde < -r["gunluk_max_zarar_yuzde"]:
                continue
            be_T = None if tr["be_k"] is None else int(v.t[tr["be_k"]]) + v.periyot
            acik.append({"tr": tr, "dov": dov, "kapanis_T": tr["cikis_t"] + v.periyot, "be_T": be_T})
            alinan.append(tr)
    return alinan


def simule(veriler, prm: Prm, kesim: Kesim, cfg, portfoy: bool = True) -> list[dict]:
    if portfoy:
        return portfoy_simule(veriler, prm, kesim, cfg)
    return [tr for v in veriler for tr in tek_simule(v, prm, kesim)]


# ------------------------------------------------------------------ metrikler ve dönemler
def _dusus(x: np.ndarray) -> float:
    kum = np.concatenate([[0.0], np.cumsum(x)])
    return float((np.maximum.accumulate(kum) - kum).max())


def metrikler(islemler: list[dict]) -> dict:
    """Max düşüş, işlemler ÇIKIŞ zamanına göre sıralanarak hesaplanır. getiri_% bileşiksizdir
    (her işlemin gerçek risk yüzdesi x R toplamı)."""
    if not islemler:
        return {"islem": 0}
    df = pd.DataFrame(islemler).sort_values(["cikis_t", "giris_t"], kind="stable")
    R = df["R"].to_numpy(float)
    yuzde = R * df["risk_yuzde"].to_numpy(float)
    kazanc, kayip = R[R > 0].sum(), -R[R < 0].sum()
    return {"islem": int(len(R)), "kazanma_%": round(float(100 * (R > 0).mean()), 1),
            "basabas_%": round(float(100 * (df["cikis"] == "BE").mean()), 1),
            "ort_R": round(float(R.mean()), 3), "toplam_R": round(float(R.sum()), 1),
            "kar_faktoru": round(float(kazanc / kayip), 2) if kayip > 0 else math.inf,
            "max_dusus_R": round(_dusus(R), 1), "getiri_%": round(float(yuzde.sum()), 1),
            "max_dusus_%": round(_dusus(yuzde), 1)}


def _kilit_dosyasi(cfg) -> Path:
    return yol(cfg.get("log_klasoru", "kayitlar")) / "kilit.json"


def _kilit_oku(cfg) -> dict:
    try:
        with open(_kilit_dosyasi(cfg), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def _kilit_yaz(cfg, veri: dict):
    d = _kilit_dosyasi(cfg)
    d.parent.mkdir(parents=True, exist_ok=True)
    with open(d, "w", encoding="utf-8") as f:
        json.dump(veri, f, ensure_ascii=False, indent=2, default=str)


def donemler(veriler: list[SembolVerisi], cfg) -> dict:
    """Takvim bazlı bölümler (tüm semboller için aynı tarihler). Kilit sınırı ilk seferde
    kaydedilir; sonradan daha yeni veri eklense bile kilitli dönem geliştirmeye karışmaz."""
    bt = cfg.get("backtest") or {}
    T0 = min(int(v.t[v.isinma]) for v in veriler)
    T1 = max(int(v.t[-1]) + v.periyot for v in veriler)
    kilit = _kilit_oku(cfg)
    if "kilit_baslangic" in kilit:
        TK = int(kilit["kilit_baslangic"])
    else:
        TK = (T0 + int((1 - bt.get("kilit_orani", 0.2)) * (T1 - T0))) // 86400 * 86400
        _kilit_yaz(cfg, {**kilit, "kilit_baslangic": TK,
                         "kilit_baslangic_tarih": str(pd.to_datetime(TK, unit="s")),
                         "olusturma_utc": datetime.now(timezone.utc).isoformat(timespec="seconds")})
    if TK <= T0:
        raise SystemExit("Kilit sınırı verinin başından önce: kayitlar/kilit.json'u kontrol et.")
    oran = (T1 - TK) / (T1 - T0)
    if not 0.1 <= oran <= 0.35:
        log.warning("Kilitli dönem şu anki verinin %%%.0f'i (hedef %%%.0f). Sınır ilk çalıştırmada sabitlendi; "
                    "ilk çalıştırma kısa veriyle yapıldıysa kayitlar/kilit.json'u silip tam veriyle yeniden "
                    "oluşturmak gerekebilir (kilitli döneme henüz bakılmadıysa).", 100 * oran,
                    100 * bt.get("kilit_orani", 0.2))
    TE = (T0 + int(bt.get("egitim_orani", 0.7) * (TK - T0))) // 86400 * 86400
    return {"egitim": Kesim("egitim", T0, TE, TK), "TEST": Kesim("TEST", TE, TK, TK),
            "KILITLI": Kesim("KILITLI", TK, T1, T1 + 1)}


def _tarih(t: int) -> str:
    return str(pd.to_datetime(t, unit="s").date())


def calistir(cfg, veriler: list[SembolVerisi], skorlar: list[int], portfoy: bool = True,
             kilitli: bool = False, zorla: bool = False, prm: Prm | None = None):
    if not veriler:
        print("Test edilecek veri yok.")
        return None
    prm0 = prm or Prm.cfgden(cfg)
    dn = donemler(veriler, cfg)
    mod = "PORTFÖY (canlı kuralları)" if portfoy else "SEMBOL BAŞINA (portföy kuralları yok)"
    print("\n" + "=" * 92)
    print(f" BACKTEST {cfg['zaman_dilimi']} | {len(veriler)} sembol | {mod} | işlem başı risk %{prm0.risk_yuzde}")
    print(f" eğitim {_tarih(dn['egitim'].bas)} → {_tarih(dn['egitim'].bit)} | "
          f"TEST {_tarih(dn['TEST'].bas)} → {_tarih(dn['TEST'].bit)} | "
          f"KİLİTLİ {_tarih(dn['KILITLI'].bas)} → {_tarih(dn['KILITLI'].bit)}  (tarihler sunucu saati)")
    print(f" maliyet: spread x{prm0.spread_carpani}, komisyon {prm0.komisyon_lot}/lot, "
          f"swap {'açık' if prm0.swap else 'kapalı'}")
    print("=" * 92)

    if kilitli:
        kilit = _kilit_oku(cfg)
        if kilit.get("acilislar") and not zorla:
            raise SystemExit(f"KİLİTLİ dönem daha önce test edildi ({kilit['acilislar'][-1]['zaman_utc']}). "
                             "Tekrar bakmak testi geçersiz kılar. Yine de bakmak için --zorla.")
        isl = simule(veriler, prm0, dn["KILITLI"], cfg, portfoy)
        m = metrikler(isl)
        print(pd.DataFrame([{"bolum": "KILITLI", "min_skor": prm0.min_skor, **m}]).to_string(index=False))
        kilit.setdefault("acilislar", []).append(
            {"zaman_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
             "parametreler": prm0.__dict__, "portfoy": portfoy, "sonuc": m, "zorla": zorla})
        _kilit_yaz(cfg, kilit)
        return pd.DataFrame([{**x, "bolum": "KILITLI"} for x in isl])

    satirlar, kayit = [], []
    for sk in skorlar:
        p = replace(prm0, min_skor=sk)
        for b in ("egitim", "TEST"):
            isl = simule(veriler, p, dn[b], cfg, portfoy)
            satirlar.append({"min_skor": sk, "bolum": b, **metrikler(isl)})
            if sk == prm0.min_skor:
                kayit += [{**x, "bolum": b} for x in isl]
    print("Her eşik ayrı simüle edildi. Max düşüş çıkış zamanı sırasıyla; getiri bileşiksiz.")
    print(pd.DataFrame(satirlar).to_string(index=False))

    if kayit:
        df = pd.DataFrame(kayit)
        print(f"\nVarlık sınıfına göre (min_skor = {prm0.min_skor}):")
        for (b, sinif), g in df.groupby(["bolum", "sinif"]):
            print(f"  {b:7s} {sinif:8s}", metrikler(g.to_dict("records")))
    print("\nNASIL OKUNUR: Karar TEST satırına göre verilir; ayar seçimi eğitimde yapılır. TEST'te")
    print("kar_faktoru < 1.2 veya ort_R < 0.05 ise strateji canlıya hazır DEĞİL. Eğitim iyi, test kötüyse")
    print("aşırı uyum var. Dahil değil: LLM katmanı, haber filtresi, marjin. KİLİTLİ dönem: en sonda bir kez.")
    return pd.DataFrame(kayit)


# ------------------------------------------------------------------ veri kaynakları
def mt5ten_cek(ad: str, cfg, yil: float):
    """Canlı terminalden (demo) ≥ yil yıllık veri + ısınma payı. (alt, ust, bilgi) ya da None."""
    import MetaTrader5 as mt5
    from . import mt5_baglanti as mb
    if not mb.sec(ad):
        return None
    si, tick = mt5.symbol_info(ad), mt5.symbol_info_tick(ad)
    if si is None or tick is None:
        return None
    simdi = datetime.now(timezone.utc)
    out = []
    for zd, pay in ((cfg["zaman_dilimi"], 90), (cfg["ust_zaman_dilimi"], 300)):
        r = mt5.copy_rates_range(ad, mb.ZAMAN_DILIMLERI[zd], simdi - timedelta(days=365 * yil + pay),
                                 simdi + timedelta(days=1))
        if r is None or len(r) == 0:
            return None
        df = pd.DataFrame(r)
        df = df[df["time"] < mb.mum_baslangici(int(tick.time), zd)]     # oluşmakta olan mumu at
        out.append(df[MUM_KOLONLARI].reset_index(drop=True))
    return out[0], out[1], {a: getattr(si, a, None) for a in BILGI_ALANLARI}


def mt5ten_yukle(cfg, liste: list[dict], yil: float, onbellek: Path | None = None) -> list[SembolVerisi]:
    veriler = []
    for s in liste:
        try:
            r = mt5ten_cek(s["ad"], cfg, yil)
            v = veri_hazirla(s["ad"], s["sinif"], r[2], r[0], r[1], cfg, onbellek) if r else None
        except Exception as ex:
            log.warning("%s yüklenemedi: %s", s["ad"], ex)
            continue
        if v is not None:
            veriler.append(v)
            log.info("%-12s %-7s %s → %s (%d mum)", v.ad, v.sinif, _tarih(int(v.t[0])), _tarih(int(v.t[-1])), len(v.t))
    return veriler


def dosyadan_yukle(klasor, cfg, sadece: list[str] | None = None, onbellek: Path | None = None) -> list[SembolVerisi]:
    klasor = yol(klasor)
    with open(klasor / "semboller.json", encoding="utf-8") as f:
        meta = json.load(f)
    zd, ust_zd = cfg["zaman_dilimi"], cfg["ust_zaman_dilimi"]
    veriler = []
    for s in meta["semboller"]:
        if sadece and s["ad"] not in sadece:
            continue
        if zd not in s["dosyalar"] or ust_zd not in s["dosyalar"]:
            raise SystemExit(f"{s['ad']}: {zd}/{ust_zd} verisi yok (indirilen: {list(s['dosyalar'])})")
        alt = pd.read_csv(klasor / s["dosyalar"][zd])
        ust = pd.read_csv(klasor / s["dosyalar"][ust_zd])
        v = veri_hazirla(s["ad"], s["sinif"], s["bilgi"], alt, ust, cfg, onbellek)
        if v is not None:
            veriler.append(v)
            log.info("%-12s %-7s %s → %s (%d mum)", v.ad, v.sinif, _tarih(int(v.t[0])), _tarih(int(v.t[-1])), len(v.t))
    return veriler


def dosyaya_kaydet(klasor, ad: str, sinif: str, bilgi: dict, alt: pd.DataFrame, ust: pd.DataFrame, cfg) -> dict:
    klasor = yol(klasor)
    klasor.mkdir(parents=True, exist_ok=True)
    guvenli = re.sub(r"[^A-Za-z0-9_.-]", "_", ad)
    dosyalar = {}
    for zd, df in ((cfg["zaman_dilimi"], alt), (cfg["ust_zaman_dilimi"], ust)):
        dosyalar[zd] = f"{guvenli}_{zd}.csv.gz"
        df[MUM_KOLONLARI].to_csv(klasor / dosyalar[zd], index=False)
    return {"ad": ad, "sinif": sinif, "bilgi": bilgi, "dosyalar": dosyalar,
            "ilk": _tarih(int(alt["time"].iloc[0])), "son": _tarih(int(alt["time"].iloc[-1])), "mum": int(len(alt))}
