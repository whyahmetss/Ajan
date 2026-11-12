"""Prop firma kural simülasyonu (docs/ARASTIRMA_RAPORU_2.md, Bölüm 4.4).

Girdi: portföy kurallarıyla seçilmiş işlemler + fiyat serileri. Mum bazında değerlenmiş özvarlık eğrisi kurulur:
  - eq_dip : o mumdaki EN KÖTÜ özvarlık (açık pozisyonlar mumun en kötü fiyatıyla değerlenir -> kötümser)
  - eq_son : mum kapanışındaki özvarlık
  - bakiye : kapanmış işlemlerin toplamı
Hepsi başlangıç bakiyesinin yüzdesi cinsindendir (toplamsal; işlem başı risk sabit % kabul edilir).

Değerlendirme: verilen her başlangıç anından itibaren (o andan SONRA açılan işlemlerle) kurallar yürütülür.
Sonuç: "gecti", "gunluk" (günlük zarar limiti ihlali), "toplam" (toplam zarar limiti ihlali), "sure" (süre doldu),
"bitmedi" (veri bitti; ne hedef ne ihlal).
"""
from dataclasses import dataclass

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class Kural:
    ad: str
    gunluk: float = 5.0            # günlük zarar limiti (%), gün başı bakiyesine göre, yüzen zarar dahil
    toplam: float = 10.0           # toplam zarar limiti (%)
    takip: bool = False            # True: zirve özvarlığa göre (takip eden); False: başlangıca göre (statik)
    hedef: float = 10.0            # kâr hedefi (%), kapanmış bakiye
    sure_gun: int = 0              # 0 = süresiz
    min_gun: int = 4               # asgari işlem günü


KURALLAR = [
    Kural("1. aşama: günlük 5 / toplam 10 statik / hedef 10 / süresiz"),
    Kural("1. aşama: hedef 8", hedef=8.0),
    Kural("2. aşama: hedef 5", hedef=5.0),
    Kural("hedef 10 / 30 gün", sure_gun=30),
    Kural("hedef 10 / 60 gün", sure_gun=60),
    Kural("günlük 5 / toplam 10 takip eden / hedef 10", takip=True),
    Kural("günlük 4 / toplam 6 takip eden / hedef 8", gunluk=4.0, toplam=6.0, takip=True, hedef=8.0),
]


def yollar(islemler: pd.DataFrame, seriler: dict) -> list:
    """Her işlem için mum mum yüzen sonuç (%): (mum zamanları, en kötü, kapanış, çıkış zamanı, sonuç %, giriş zamanı)."""
    out = []
    for r in islemler.itertuples():
        s = seriler[r.sembol]
        j, k = int(r.i) + 1, int(r.k)
        h, l, c, sp = s.h[j:k + 1], s.l[j:k + 1], s.c[j:k + 1], s.sp[j:k + 1]
        if r.yon > 0:
            kotu, kap = (l - r.G) / r.M, (c - r.G) / r.M
        else:
            kotu, kap = (r.G - h - sp) / r.M, (r.G - c - sp) / r.M
        kotu, kap = kotu * r.risk_yuzde, kap * r.risk_yuzde
        sonuc = r.R * r.risk_yuzde
        stop = r.kod in (0, 3)                                 # SL / TRAIL: mum içindeki dip stop seviyesinde kesildi
        kotu[-1] = sonuc if stop else min(kotu[-1], sonuc)
        kap[-1] = 0.0                                          # çıkış mumunun sonunda pozisyon kapalı (bakiyeye geçer)
        out.append((s.t[j:k + 1], kotu, kap, int(s.t[k]), float(sonuc), int(r.giris_t)))
    return out


def egri(yol_listesi: list, izgara: np.ndarray):
    """izgara (sıralı mum zamanları) üzerinde: eq_dip, eq_son, bakiye (mum sonu)."""
    n = len(izgara)
    dip, son, gercek = np.zeros(n), np.zeros(n), np.zeros(n)
    for t, kotu, kap, cikis_t, sonuc, _ in yol_listesi:
        ix = np.searchsorted(izgara, t)
        np.add.at(dip, ix, kotu)
        np.add.at(son, ix, kap)
        gercek[np.searchsorted(izgara, cikis_t)] += sonuc
    bakiye = np.cumsum(gercek)
    onceki = np.r_[0.0, bakiye[:-1]]
    return onceki + dip, bakiye + son, bakiye


def degerlendir(izgara, eq_dip, eq_son, bakiye, giris_gunleri, kural: Kural) -> tuple:
    """Tek değerlendirme (dizi başı = başlangıç). Dönüş: (sonuç, gün sayısı)."""
    if len(izgara) == 0:
        return "bitmedi", 0.0
    gun = izgara // 86400
    _, ilk, ters = np.unique(gun, return_index=True, return_inverse=True)
    onceki_bakiye = np.r_[0.0, bakiye[:-1]]
    gun_basi = onceki_bakiye[ilk][ters]
    ihlal_g = eq_dip < gun_basi - kural.gunluk * (1 + 1e-9)
    if kural.takip:
        zirve = np.maximum.accumulate(np.r_[0.0, eq_son[:-1]])
        ihlal_t = eq_dip < zirve - kural.toplam * (1 + 1e-9)
    else:
        ihlal_t = eq_dip < -kural.toplam * (1 + 1e-9)
    islem_gunu = np.searchsorted(giris_gunleri, gun, side="right")           # o güne kadarki işlem günü sayısı
    gecti = (bakiye >= kural.hedef) & (islem_gunu >= kural.min_gun)

    def ilk_dogru(m):
        ix = np.flatnonzero(m)
        return int(ix[0]) if len(ix) else len(m) + 1
    a, b, c = ilk_dogru(ihlal_g), ilk_dogru(ihlal_t), ilk_dogru(gecti)
    sure = len(izgara) + 1
    if kural.sure_gun:
        ix = np.flatnonzero(izgara - izgara[0] > kural.sure_gun * 86400)
        sure = int(ix[0]) if len(ix) else len(izgara) + 1
    en = min(a, b, c, sure)
    if en > len(izgara):
        return "bitmedi", (izgara[-1] - izgara[0]) / 86400
    gun_say = (izgara[en] - izgara[0]) / 86400
    if en == a or en == b:                                    # aynı mumda ihlal ve hedef: ihlal sayılır (kötümser)
        return ("gunluk" if a <= b else "toplam"), gun_say
    if en == sure and sure < c:
        return "sure", gun_say
    return "gecti", gun_say


def simule(islemler: pd.DataFrame, seriler: dict, bas: int, bit: int, kurallar=KURALLAR, adim_gun: int = 7) -> pd.DataFrame:
    """[bas, bit) aralığında her `adim_gun` günde bir değerlendirme başlatır; kural başına sonuç oranları."""
    yl = sorted(yollar(islemler, seriler), key=lambda y: y[5])
    if not yl:
        return pd.DataFrame()
    izgara = np.unique(np.concatenate([y[0] for y in yl]))
    satirlar = {k.ad: [] for k in kurallar}
    genel_dip, genel_son, genel_bak = egri(yl, izgara)
    for T in range(int(bas), int(bit), adim_gun * 86400):
        alt = [y for y in yl if y[5] >= T]
        g0 = int(np.searchsorted(izgara, T))
        iz = izgara[g0:]
        if not len(alt) or not len(iz):
            for k in kurallar:
                satirlar[k.ad].append(("bitmedi", (bit - T) / 86400))
            continue
        dip, son, bak = egri(alt, iz)
        gunler = np.unique(np.array([y[5] for y in alt]) // 86400)
        for k in kurallar:
            satirlar[k.ad].append(degerlendir(iz, dip, son, bak, gunler, k))
    out = []
    for k in kurallar:
        s = pd.DataFrame(satirlar[k.ad], columns=["sonuc", "gun"])
        n = len(s)
        gecen = s[s.sonuc == "gecti"]
        out.append({"kural": k.ad, "deneme": n, "gecti_%": round(100 * len(gecen) / n, 1),
                    "ihlal_gunluk_%": round(100 * (s.sonuc == "gunluk").mean(), 1),
                    "ihlal_toplam_%": round(100 * (s.sonuc == "toplam").mean(), 1),
                    "sure_doldu_%": round(100 * (s.sonuc == "sure").mean(), 1),
                    "bitmedi_%": round(100 * (s.sonuc == "bitmedi").mean(), 1),
                    "gecme_medyan_gun": round(float(gecen.gun.median()), 0) if len(gecen) else None})
    df = pd.DataFrame(out)
    gun = izgara // 86400
    _, ilk, ters = np.unique(gun, return_index=True, return_inverse=True)
    gun_basi = np.r_[0.0, genel_bak[:-1]][ilk][ters]
    df.attrs["en_kotu_gun_%"] = round(float((genel_dip - gun_basi).min()), 2)
    df.attrs["en_dusuk_ozvarlik_%"] = round(float(genel_dip.min()), 2)
    df.attrs["zirveden_en_buyuk_dusus_%"] = round(float((genel_dip - np.maximum.accumulate(np.r_[0.0, genel_son[:-1]])).min()), 2)
    return df


def monte_carlo(islemler: pd.DataFrame, hedef: float = 10.0, toplam: float = 10.0, n: int = 3000, tohum: int = 0) -> dict:
    """İşlem sonuçlarını haftalık bloklarla yeniden örnekleyerek (yerine koyarak) 'hedefe ihlalden önce ulaşma'
    olasılığı. Sadece kapanmış işlem bakiyesi (yüzen zarar yok) -> iyimser taraf; simule() ile birlikte okunmalı."""
    d = islemler.sort_values("cikis_t")
    yuzde = (d["R"] * d["risk_yuzde"]).to_numpy(float)
    hafta = (d["cikis_t"].to_numpy() // (7 * 86400)).astype(np.int64)
    bloklar = [yuzde[hafta == h] for h in np.unique(hafta)]
    if len(bloklar) < 5:
        return {}
    rng = np.random.default_rng(tohum)
    gecti = ihlal = 0
    adet = []
    for _ in range(n):
        yol = np.cumsum(np.concatenate([bloklar[s] for s in rng.integers(0, len(bloklar), 4 * len(bloklar))]))
        h, z = np.flatnonzero(yol >= hedef), np.flatnonzero(yol <= -toplam)
        ih, iz = (h[0] if len(h) else 10 ** 9), (z[0] if len(z) else 10 ** 9)
        if ih < iz:
            gecti += 1
            adet.append(ih + 1)
        elif iz < ih:
            ihlal += 1
    return {"hedef_%": hedef, "ufuk": "veri süresinin 4 katı", "gecti_%": round(100 * gecti / n, 1),
            "ihlal_%": round(100 * ihlal / n, 1), "sonucsuz_%": round(100 * (n - gecti - ihlal) / n, 1),
            "hedefe_medyan_islem": int(np.median(adet)) if adet else None}
