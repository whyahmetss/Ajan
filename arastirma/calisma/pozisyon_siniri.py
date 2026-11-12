"""Risk ayarı ödünleşimi (hipotez değil): K2, aynı anda en fazla N kripto pozisyonu. Bölüm 9.
Kullanım:  python -m arastirma.calisma.pozisyon_siniri"""
import copy

from arastirma import deney2, motor, prop
from arastirma.stratejiler import ADAYLAR
from bot import backtest


def main():
    cfg = deney2.cfg_yukle()
    dn = deney2.donemler(cfg)
    mod = ADAYLAR["K2"][1]
    seriler = [s for s in deney2.yukle("H1", cfg) if s.sinif == "kripto"]
    gel = backtest.Kesim("gel", dn["egitim"].bas, dn["dogrulama"].bit, dn["dogrulama"].veri_bit)
    satir = []
    for n in (2, 3, 4):
        c = copy.deepcopy(cfg)
        c["risk"]["max_ayni_doviz"] = n
        c["risk"]["max_acik_pozisyon"] = max(4, n)
        for ad, k in (("egitim", dn["egitim"]), ("dogrulama", dn["dogrulama"]), ("kilitli (daha önce görüldü)", dn["KILITLI"])):
            d = deney2.islemler(mod, seriler, mod.PRM, k, c, portfoy=True)
            o = motor.ozet(d)
            mc = motor.monte_carlo_dusus(d)
            satir.append({"max_kripto": n, "donem": ad, "islem": o["islem"], "WR_%": o["kazanma_%"], "ort_R": o["ort_R"],
                          "PF": o["kar_faktoru"], "yillik_%": deney2._yillik(o, k), "max_dusus_%": o["max_dusus_%"],
                          "MC_dusus_p95_%": mc["p95_%"]})
        pf = deney2.islemler(mod, seriler, mod.PRM, gel, c, portfoy=True)
        ps = prop.simule(pf, {s.ad: s for s in seriler}, gel.bas, gel.bit)
        print(f"\nmax_kripto = {n} | prop (eğitim + doğrulama):", ps.attrs)
        print(ps.iloc[[0, 1, 6]].to_string(index=False))
    print()
    print(deney2.tablo(satir))


if __name__ == "__main__":
    main()
