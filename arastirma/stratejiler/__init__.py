"""Ön kayıtlı aday stratejiler (docs/ARASTIRMA_RAPORU_2.md, Bölüm 3). Her biri ayrı modül; bot bunları KULLANMAZ.

Her modülün arayüzü:
    ZD          : zaman dilimi ("M15", "H1", "H4")
    PRM         : ön kayıtlı parametreler (dict)
    KOMSU       : ±%20 hassasiyet testinde değiştirilecek parametreler {ad: [değerler]}
    evren(seri) : bu sembol stratejinin evreninde mi
    sinyal(seri, prm) -> Emirler   : bütün aday işlemler (uygunluk maskesi sonradan uygulanır)
    havuz(seri, prm)  -> Emirler   : rastgele giriş havuzu (aynı çıkış kuralları, sinyalsiz girişler)
"""
from . import bant_donus, hacim_kirilim, orb, oynaklik, takip_cikis, tur3

ADAYLAR = {
    "K2": ("Mevcut strateji (kıyas tabanı): kripto günlük kırılım, SL 2 günlük ATR, 240 mum", hacim_kirilim.K2),
    "O1": ("ORB: 30 dk aralık kırılımı, stop karşı kenar, hedef yok, seans sonunda çık", orb.O1),
    "O2": ("ORB: O1 + hedef 1R", orb.O2),
    "V1": ("K2 + kırılım günü hacmi >= 1.5 x önceki 20 gün ortalaması", hacim_kirilim.V1),
    "T1": ("Oynaklık kırılımı (H4): kapanış değişimi > 2 ATR, SL 2 ATR, ters sinyal / 60 mum", oynaklik.T1),
    "T2": ("K2 sinyali + günlük kapanıştan 3 günlük ATR takip eden stop, zaman stopu yok", takip_cikis.T2),
    "S1": ("K2'nin satış tarafı: 20 gün dibi ve < SMA200 -> satış, SL 2 günlük ATR, 240 mum", tur3.S1),
    "B1": ("K2 + BTC rejim filtresi (BTC > SMA200)", tur3.B1),
    "X1": ("K2 kuralı endeks / altın / gümüşte", tur3.X1),
    "M1": ("Ortalamaya dönüş (H4): ADX<20 ve Bollinger(20,2) dışı kapanış; hedef SMA20, SL 2 ATR, 30 mum", bant_donus.M1),
}
