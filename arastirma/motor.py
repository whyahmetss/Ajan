"""Araştırma motoru: hızlı (numba) işlem simülasyonu + istatistik araçları.

Simülasyon kuralları bot/backtest.py:islem_simule ile AYNIDIR (tests/test_arastirma.py her iki
motorun aynı sonucu verdiğini doğrular). Bu dosya sadece araştırma içindir; bot bunu kullanmaz.
"""
import json
from dataclasses import dataclass, replace

import numpy as np
import pandas as pd
from numba import njit

from bot import backtest, haber, indikator
from bot.yollar import yol

SL, TP, BE, TRAIL, SON, ZAMAN = 0, 1, 2, 3, 4, 5
KOD_AD = {SL: "SL", TP: "TP", BE: "BE", TRAIL: "TRAIL", SON: "SON", ZAMAN: "ZAMAN"}
SONSUZ = 1e18


@dataclass(frozen=True)
class Cikis:
    """Çıkış kuralları. be_r / tr_bas = SONSUZ: başa baş / takip eden stop kapalı. max_mum = 0: zaman stopu yok."""
    sl_atr: float = 1.5
    tp_r: float = 2.0
    be_r: float = 1.0
    tr_bas: float = 1.5
    tr_atr: float = 1.2
    max_mum: int = 0
    atr: str = "atr"            # stop/hedef hangi ATR ile ölçeklenir: "atr" (H1) ya da "d_atr" (günlük)

    @classmethod
    def sabit(cls, tp_atr: float, sl_atr: float, max_mum: int, atr: str = "atr") -> "Cikis":
        """Sabit hedef/stop (ATR cinsinden) + zaman stopu; başa baş ve takip yok."""
        return cls(sl_atr=sl_atr, tp_r=tp_atr / sl_atr, be_r=SONSUZ, tr_bas=SONSUZ, tr_atr=0.0, max_mum=max_mum, atr=atr)


@dataclass(frozen=True)
class Maliyet:
    spread_carpani: float = 1.0
    komisyon_carpani: float = 1.0    # sembolün kendi komisyonunun (bilgi["komisyon_yuzde"]) çarpanı
    komisyon_yuzde: float = 0.0      # EK komisyon: gidiş-dönüş, pozisyon değerinin yüzdesi (stres testi)
    swap: bool = True
    swap_carpani: float = 1.0        # finansman stres testi


@dataclass
class Sembol:
    ad: str
    sinif: str
    bilgi: dict
    f: dict                 # özellik dizileri: t,o,h,l,c,sp,atr,ema20,... (hepsi H1 mum başına)
    isinma: int
    hafta_sonu: bool

    def __getattr__(self, k):
        if k == "f" or k.startswith("__"):
            raise AttributeError(k)
        try:
            return self.f[k]
        except KeyError:
            raise AttributeError(k) from None

    @property
    def n(self) -> int:
        return len(self.f["t"])

    @property
    def periyot(self) -> int:
        return 3600


# ------------------------------------------------------------------ veri
def _gunluk(t, o, h, l, c) -> dict:
    """Sunucu gününe göre günlük mumlar ve her H1 mumu için DÜNKÜ (kapanmış) günlük değerler."""
    gun = t // 86400
    g = pd.DataFrame({"gun": gun, "o": o, "h": h, "l": l, "c": c}).groupby("gun").agg(
        o=("o", "first"), h=("h", "max"), l=("l", "min"), c=("c", "last"))
    kap = g["c"]
    gunluk_atr = indikator.atr(pd.DataFrame({"high": g["h"], "low": g["l"], "close": g["c"]}))
    tablo = pd.DataFrame({"d_c": kap, "d_sma200": kap.rolling(200).mean(), "d_sma50": kap.rolling(50).mean(),
                          "d_sma5": kap.rolling(5).mean(), "d_rsi2": indikator.rsi(kap, 2),
                          "d_ret1": kap.pct_change(), "d_atr": gunluk_atr, "d_h": g["h"], "d_l": g["l"],
                          "d_maks20": kap.rolling(20).max(), "d_min20": kap.rolling(20).min()})
    pos = np.searchsorted(g.index.to_numpy(), gun) - 1          # bir önceki gün
    out = {}
    for k in tablo:
        v = tablo[k].to_numpy(float)
        out[k] = np.where(pos >= 0, v[np.clip(pos, 0, None)], np.nan)
    out["gun_ilk"] = np.r_[True, gun[1:] != gun[:-1]]            # sunucu gününün ilk H1 mumu
    return out


def _utc_saat(t: np.ndarray) -> np.ndarray:
    ny = pd.to_datetime(t - 7 * 3600, unit="s").tz_localize("America/New_York", ambiguous=True,
                                                            nonexistent="shift_forward")
    return ny.tz_convert("UTC").hour.to_numpy()


def sembol_kur(ad: str, sinif: str, bilgi: dict, alt: pd.DataFrame, ust: pd.DataFrame, isinma: int) -> Sembol:
    alt = alt.sort_values("time").drop_duplicates("time").reset_index(drop=True)
    d = indikator.ekle(alt)
    t = alt["time"].to_numpy(np.int64)
    ut = backtest._ust_trend_dizisi(t, ust, 3600, 14400)
    yon, skor = backtest.sinyaller(d, ut, isinma)
    f = {"t": t, "o": d["open"].to_numpy(float), "h": d["high"].to_numpy(float), "l": d["low"].to_numpy(float),
         "c": d["close"].to_numpy(float), "sp": d["spread"].to_numpy(float) * bilgi["point"],
         "atr": d["atr"].to_numpy(float), "ema20": d["ema20"].to_numpy(float), "ema50": d["ema50"].to_numpy(float),
         "ema200": d["ema200"].to_numpy(float), "rsi": d["rsi"].to_numpy(float), "adx": d["adx"].to_numpy(float),
         "ut": ut, "yon0": yon, "skor0": skor, "utc_saat": _utc_saat(t)}
    with np.errstate(invalid="ignore", divide="ignore"):
        f["z"] = (f["c"] - f["ema20"]) / f["atr"]
    f.update(_gunluk(t, f["o"], f["h"], f["l"], f["c"]))
    ardisik = np.zeros(len(t), bool)
    ardisik[:-1] = (t[1:] - t[:-1]) == 3600
    f["ardisik"] = ardisik
    cumartesi = (((t // 86400) + 3) % 7) == 5
    return Sembol(ad, sinif, bilgi, f, isinma, bool(cumartesi.mean() > 0.01))


def yukle(cfg, klasor="veri", sadece=None, siniflar=None) -> list[Sembol]:
    klasor = yol(klasor)
    meta = json.loads((klasor / "semboller.json").read_text(encoding="utf-8"))
    isinma = int((cfg.get("backtest") or {}).get("isinma_bar", cfg["tarama_bar"]))
    out = []
    for s in meta["semboller"]:
        if (sadece and s["ad"] not in sadece) or (siniflar and s["sinif"] not in siniflar):
            continue
        out.append(sembol_kur(s["ad"], s["sinif"], s["bilgi"], pd.read_csv(klasor / s["dosyalar"]["H1"]),
                              pd.read_csv(klasor / s["dosyalar"]["H4"]), isinma))
    return out


def canonik(sem: Sembol) -> backtest.SembolVerisi:
    """Aynı veriyi botun backtest motorunun biçimine çevirir (eşlik testleri ve son doğrulama için)."""
    f = sem.f
    return backtest.SembolVerisi(ad=sem.ad, sinif=sem.sinif, bilgi=sem.bilgi, t=f["t"], o=f["o"], h=f["h"], l=f["l"],
                                 c=f["c"], sp_pt=f["sp"] / sem.bilgi["point"], atr=f["atr"], yon=f["yon0"],
                                 skor=f["skor0"], ardisik=f["ardisik"], hafta_sonu=sem.hafta_sonu, periyot=3600,
                                 isinma=sem.isinma)


# ------------------------------------------------------------------ simülasyon çekirdeği
@njit(cache=True)
def _sim(o, h, l, c, sp, atr, idx, yon, son, sl_atr, tp_r, be_r, tr_bas, tr_atr, max_mum, taban, point):
    n = len(idx)
    R = np.full(n, np.nan)
    K = np.full(n, -1, np.int64)
    KOD = np.full(n, -1, np.int8)
    BEK = np.full(n, -1, np.int64)
    M = np.zeros(n)
    G = np.zeros(n)
    for a in range(n):
        i = idx[a]
        j = i + 1
        if j > son:
            continue
        al = yon[a] > 0
        spj = sp[j]
        m = max(sl_atr * atr[i], taban + spj)
        if not (m > 0.0) or spj > 0.25 * m:
            continue
        giris = o[j] + spj if al else o[j]
        sl = giris - m if al else giris + m
        tp = giris + tp_r * m if al else giris - tp_r * m
        kod = SL
        kc = -1
        be_k = -1
        cikis = 0.0
        for k in range(j, son + 1):
            spk = sp[k]
            if al:
                if k > j and o[k] <= sl:
                    cikis = o[k]; kc = k; break
                if l[k] <= sl:
                    cikis = sl; kc = k; break
                if k > j and o[k] >= tp:
                    cikis = o[k]; kc = k; kod = TP; break
                if h[k] >= tp:
                    cikis = tp; kc = k; kod = TP; break
            else:
                ao = o[k] + spk
                if k > j and ao >= sl:
                    cikis = ao; kc = k; break
                if h[k] + spk >= sl:
                    cikis = sl; kc = k; break
                if k > j and ao <= tp:
                    cikis = ao; kc = k; kod = TP; break
                if l[k] + spk <= tp:
                    cikis = tp; kc = k; kod = TP; break
            if max_mum > 0 and k - j + 1 >= max_mum:            # zaman stopu: mum kapanışında piyasadan çık
                cikis = c[k] if al else c[k] + spk
                kc = k; kod = ZAMAN; break
            kar = (c[k] - giris) if al else (giris - (c[k] + spk))
            if kar >= tr_bas * m:
                yeni = c[k] - tr_atr * atr[k] if al else c[k] + spk + tr_atr * atr[k]
                aday = TRAIL
            elif kar >= be_r * m:
                yeni = giris + spk + 2 * point if al else giris - spk - 2 * point
                aday = BE
            else:
                continue
            if (al and yeni > sl) or ((not al) and yeni < sl):
                sl = yeni
                kod = aday
                if be_k < 0 and ((sl - giris) if al else (giris - sl)) >= 0:
                    be_k = k
        if kc < 0:
            kc = son
            kod = SON
            cikis = c[son] if al else c[son] + sp[son]
        R[a] = ((cikis - giris) if al else (giris - cikis)) / m
        K[a] = kc
        KOD[a] = kod
        BEK[a] = be_k
        M[a] = m
        G[a] = giris
    return R, K, KOD, BEK, M, G


@njit(cache=True)
def _geceler(giris_t, cikis_t, uc_kat, hafta_sonu):
    out = np.zeros(len(giris_t), np.int64)
    for a in range(len(giris_t)):
        toplam = 0
        for g in range(giris_t[a] // 86400 + 1, cikis_t[a] // 86400 + 1):
            biten = (g - 1 + 4) % 7
            if hafta_sonu:
                toplam += 1
            elif biten != 6 and biten != 0:
                toplam += 3 if biten == uc_kat else 1
        out[a] = toplam
    return out


@njit(cache=True)
def _cakismasiz(idx, k):
    """Sembol başına aynı anda tek pozisyon: çıkış mumunun kapanışında yeni sinyal alınabilir."""
    sec = np.zeros(len(idx), np.bool_)
    i_min = -1
    for a in range(len(idx)):
        if k[a] < 0 or idx[a] < i_min:
            continue
        sec[a] = True
        i_min = k[a]
    return sec


def uygun_mumlar(sem: Sembol, bas: int, bit: int, veri_bit: int, max_spread_atr: float, mal: Maliyet) -> tuple:
    """Canlıda taranabilecek sinyal mumları: ısınma sonrası, sonraki mum ardışık, giriş [bas, bit) içinde,
    giriş anındaki spread ATR'ye göre makul. Dönüş: (maske, son kullanılabilir mum index'i)."""
    f = sem.f
    son = int(np.searchsorted(f["t"], veri_bit, side="left")) - 1
    n = sem.n
    j = np.minimum(np.arange(n) + 1, n - 1)
    with np.errstate(invalid="ignore", divide="ignore"):
        m = (f["ardisik"] & (np.arange(n) >= sem.isinma) & (np.arange(n) + 1 <= son)
             & (f["t"][j] >= bas) & (f["t"][j] < bit) & (f["atr"] > 0)
             & (f["sp"][j] * mal.spread_carpani / f["atr"] <= max_spread_atr))
    return m, son


def simule(sem: Sembol, idx: np.ndarray, yon: np.ndarray, cikis: Cikis, mal: Maliyet, son: int) -> pd.DataFrame:
    """Verilen sinyal mumlarının her biri için bağımsız işlem sonucu (çakışma ayıklanmaz)."""
    f, b = sem.f, sem.bilgi
    sp = f["sp"] * mal.spread_carpani
    taban = ((b.get("trade_stops_level") or 0) + 5) * b["point"]
    idx = np.ascontiguousarray(idx, np.int64)
    yon = np.ascontiguousarray(yon, np.int8)
    R, K, KOD, BEK, M, G = _sim(f["o"], f["h"], f["l"], f["c"], sp, f[cikis.atr], idx, yon, son, cikis.sl_atr, cikis.tp_r,
                                cikis.be_r, cikis.tr_bas, cikis.tr_atr, cikis.max_mum, taban, b["point"])
    ok = K >= 0
    idx, yon, R, K, KOD, BEK, M, G = idx[ok], yon[ok], R[ok], K[ok], KOD[ok], BEK[ok], M[ok], G[ok]
    giris_t, cikis_t = f["t"][idx + 1], f["t"][K]
    maliyet = ((b.get("komisyon_yuzde") or 0.0) * mal.komisyon_carpani + mal.komisyon_yuzde) / 100 * G / M
    if mal.swap and b.get("swap_mode") == 5:
        oran = np.where(yon > 0, b.get("swap_long") or 0.0, b.get("swap_short") or 0.0)
        gece = _geceler(giris_t, cikis_t, int(b.get("swap_rollover3days", 3)), sem.hafta_sonu)
        maliyet = maliyet - gece * oran * mal.swap_carpani / 100 / 360 * G / M
    be_T = np.where(BEK >= 0, f["t"][np.clip(BEK, 0, None)] + 3600, -1)       # stopun girişe çekildiği an
    return pd.DataFrame({"sembol": sem.ad, "sinif": sem.sinif, "i": idx, "k": K, "yon": yon, "giris_t": giris_t,
                         "cikis_t": cikis_t, "kod": KOD, "be_T": be_T, "R_brut": R, "R": R - maliyet,
                         "maliyet_R": maliyet, "risk_yuzde": 0.5})


def tek(sem: Sembol, maske: np.ndarray, yon: np.ndarray, cikis: Cikis, mal: Maliyet, son: int) -> pd.DataFrame:
    """Sembol tek başına, aynı anda tek pozisyon (bot/backtest.tek_simule ile aynı)."""
    idx = np.flatnonzero(maske)
    df = simule(sem, idx, yon[idx], cikis, mal, son)
    if df.empty:
        return df
    return df[_cakismasiz(df["i"].to_numpy(np.int64), df["k"].to_numpy(np.int64))].reset_index(drop=True)


def portfoy(adaylar: pd.DataFrame, skor: np.ndarray, cfg) -> pd.DataFrame:
    """Canlı portföy kuralları (bot/backtest.portfoy_simule ile aynı mantık). adaylar: bütün sembollerin
    aday işlemleri (simule çıktıları birleştirilmiş, index tekil), skor: her adayın skoru."""
    r, max_aday = cfg["risk"], cfg["tarayici"]["max_aday"]
    a = adaylar.assign(skor=np.asarray(skor), sira=pd.factorize(adaylar["sembol"])[0])
    a = a.sort_values(["giris_t", "skor", "sira"], ascending=[True, False, True], kind="stable")
    es = adaylar.drop_duplicates("sembol")
    dov = {s: haber.sembol_dovizleri(s, sn) for s, sn in zip(es["sembol"], es["sinif"])}
    acik, secilen, gunluk = [], [], {}      # acik: (sembol, kapanis_T, dovizler, be_T, cikis_gunu, sonuc_%, risk_%)
    for T, grup in a.groupby("giris_t", sort=True):
        kalan = []
        for p in acik:
            if p[1] <= T:
                gunluk[p[4]] = gunluk.get(p[4], 0.0) + p[5]
            else:
                kalan.append(p)
        acik = kalan
        for row in grup.head(max_aday).itertuples():
            if any(p[0] == row.sembol for p in acik) or len(acik) >= r["max_acik_pozisyon"]:
                continue
            d = dov[row.sembol]
            if any(sum(1 for p in acik if x in p[2]) >= r["max_ayni_doviz"] for x in d):
                continue
            acik_risk = sum(p[6] for p in acik if p[3] < 0 or p[3] > T)
            if gunluk.get(T // 86400, 0.0) - acik_risk - row.risk_yuzde < -r["gunluk_max_zarar_yuzde"] * (1 + 1e-9):
                continue
            acik.append((row.sembol, row.cikis_t + getattr(row, "periyot", 3600), d, row.be_T, row.cikis_t // 86400,
                         row.R * row.risk_yuzde, row.risk_yuzde))
            secilen.append(row.Index)
    return adaylar.loc[secilen]


# ------------------------------------------------------------------ istatistik
def ozet(df: pd.DataFrame) -> dict:
    if df is None or len(df) == 0:
        return {"islem": 0}
    d = df.sort_values(["cikis_t", "giris_t"], kind="stable")
    R = d["R"].to_numpy(float)
    yuzde = R * d["risk_yuzde"].to_numpy(float)
    kazanc, kayip = R[R > 0].sum(), -R[R < 0].sum()
    return {"islem": int(len(R)), "kazanma_%": round(float(100 * (R > 0).mean()), 1),
            "ort_R": round(float(R.mean()), 3), "toplam_R": round(float(R.sum()), 1),
            "kar_faktoru": round(float(kazanc / kayip), 2) if kayip > 0 else float("inf"),
            "max_dusus_R": round(backtest._dusus(R), 1), "getiri_%": round(float(yuzde.sum()), 1),
            "max_dusus_%": round(backtest._dusus(yuzde), 1)}


def yil_yil(df: pd.DataFrame) -> pd.DataFrame:
    y = pd.to_datetime(df["giris_t"], unit="s").dt.year
    return pd.DataFrame([{"yil": int(k), **ozet(g)} for k, g in df.groupby(y)])


def blok_guven_araligi(df: pd.DataFrame, n: int = 2000, tohum: int = 0) -> tuple:
    """Ortalama R için %95 güven aralığı; haftalık bloklarla (aynı haftadaki işlemler birbirine bağımlı)."""
    hafta = (df["giris_t"].to_numpy() // (7 * 86400)).astype(np.int64)
    kod, tekil = pd.factorize(hafta)
    top = np.bincount(kod, weights=df["R"].to_numpy(float))
    say = np.bincount(kod).astype(float)
    rng = np.random.default_rng(tohum)
    sec = rng.integers(0, len(tekil), (n, len(tekil)))
    ort = top[sec].sum(1) / say[sec].sum(1)
    return float(np.percentile(ort, 2.5)), float(np.percentile(ort, 97.5))


def permutasyon(strateji: pd.DataFrame, havuz: pd.DataFrame, n: int = 2000, tohum: int = 0) -> dict:
    """Strateji girişleri, AYNI sembol ve yılda AYNI yönde rastgele seçilen girişlerden iyi mi?
    havuz: aynı çıkış kurallarıyla, uygun BÜTÜN mumlardan (stratejinin yönlerinde) açılmış işlemler.
    p: rastgele seçimin ortalama R'sinin stratejininkine eşit ya da büyük olma oranı."""
    def anahtar(d):
        return d["sembol"].astype(str) + "|" + pd.to_datetime(d["giris_t"], unit="s").dt.year.astype(str) + "|" + d["yon"].astype(str)
    s_an, h_an = anahtar(strateji), anahtar(havuz)
    gruplar = {k: g["R"].to_numpy(float) for k, g in havuz.groupby(h_an)}
    say = s_an.value_counts()
    rng = np.random.default_rng(tohum)
    toplam, adet = np.zeros(n), 0
    for k, c in say.items():
        hv = gruplar.get(k)
        if hv is None or len(hv) == 0:
            continue
        toplam += hv[rng.integers(0, len(hv), (n, c))].sum(1)
        adet += c
    rastgele = toplam / max(adet, 1)
    gercek = float(strateji["R"].mean())
    return {"strateji_ort_R": round(gercek, 4), "rastgele_ort_R": round(float(rastgele.mean()), 4),
            "fark": round(gercek - float(rastgele.mean()), 4), "p": float((rastgele >= gercek).mean()),
            "rastgele_kazanma_%": round(float(100 * (havuz["R"] > 0).mean()), 1)}


def monte_carlo_dusus(df: pd.DataFrame, n: int = 3000, tohum: int = 0) -> dict:
    """İşlem sırasını haftalık bloklar halinde karıştırarak max düşüşün (%) dağılımı."""
    d = df.sort_values("cikis_t")
    yuzde = (d["R"] * d["risk_yuzde"]).to_numpy(float)
    hafta = (d["cikis_t"].to_numpy() // (7 * 86400)).astype(np.int64)
    bloklar = [yuzde[hafta == h] for h in np.unique(hafta)]
    rng = np.random.default_rng(tohum)
    dd = np.empty(n)
    for a in range(n):
        sira = rng.permutation(len(bloklar))
        dd[a] = backtest._dusus(np.concatenate([bloklar[s] for s in sira]))
    return {"gercek_%": round(backtest._dusus(yuzde), 1), "medyan_%": round(float(np.median(dd)), 1),
            "p95_%": round(float(np.percentile(dd, 95)), 1), "p99_%": round(float(np.percentile(dd, 99)), 1)}


def komsular(cikis: Cikis, oran: float = 0.2) -> list:
    """Her çıkış parametresini tek tek ±oran değiştirir (hassasiyet testi)."""
    out = []
    for alan in ("sl_atr", "tp_r", "be_r", "tr_bas", "tr_atr", "max_mum"):
        v = getattr(cikis, alan)
        if v in (0, 0.0) or v >= SONSUZ:
            continue
        for c in (1 - oran, 1 + oran):
            yeni = {alan: (int(round(v * c)) if alan == "max_mum" else v * c)}
            if alan == "sl_atr":                 # stop değişirken hedefin ATR cinsinden yeri sabit kalsın
                yeni["tp_r"] = cikis.tp_r / c
            out.append((f"{alan} x{c:.1f}", replace(cikis, **yeni)))
    return out
