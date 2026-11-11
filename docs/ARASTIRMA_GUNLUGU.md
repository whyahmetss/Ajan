# Araştırma Günlüğü

Bu dosya, strateji araştırmasında denenen HER şeyi (başarısızlar dahil) kaydeder. Amaç: kaç şey
denendiğini gizlememek ve "geçmişte işe yarayanı seçip gerekçe uydurma" hatasından kaçınmak.

## 0. Veri (halka açık kaynaklar, gerçek piyasa fiyatları)

| Sınıf | Kaynak | Dönem | Spread |
|---|---|---|---|
| Forex (14 parite) | FXCM açık mum arşivi, H1 alış+satış | 2015-01 → 2026-11 | **Gerçek** (mum açılışındaki alış-satış farkı; FXCM standart hesap, komisyonsuz) |
| Altın, gümüş, petrol, 5 endeks | HistData.com, M1 → H1 | 2015-01 → 2026-10 | **Model**: aktif saatlerde (07-20 UTC) sabit, dışında x2.5 |
| Kripto (8 coin) | Binance spot H1 | 2017/18 → 2026-10 | **Model**: fiyatın yüzdesi (BTC %0.03, ETH %0.05, diğerleri %0.15) |

Finansman (swap) modeli, yıllık ve her iki yönde masraf (muhafazakâr): forex %2, altın/gümüş %4,
petrol %8, endeks %5, kripto %20.

Sınırlar:
- Bu, kullanıcının broker'ının verisi DEĞİL. Spread, seans saatleri, swap broker'a göre değişir.
- Emtia/endeks/kripto için spread varsayımdır; maliyet stres testi (x1.5, x2) bu yüzden zorunlu.
- Zaman: tipik MT5 "New York kapanışı" sunucu saati (UTC+2 / ABD yaz saatinde UTC+3).

Yeniden üretmek için: `python -m arastirma.veri_topla`

## 1. Dönemler (sonuçlara bakmadan önce sabitlendi)

- **KİLİTLİ**: takvimin son %20'si. Sınır ilk çalıştırmada `kayitlar/kilit.json`'a yazılır.
  Strateji ve ayarlar donana kadar bu döneme BAKILMAZ; en sonda BİR kez test edilir.
- **Geliştirme**: kilitten önceki her şey. İlk %70'i "eğitim", son %30'u "TEST".
  Ayrıca yıl yıl sonuç tablosu (zaman içinde tutarlılık).

## 2. Kabul ölçütleri (sonuçlara bakmadan önce sabitlendi)

Bir strateji ancak şunların HEPSİNİ sağlarsa bota konur:

1. Geliştirme TEST bölümünde, maliyetlerden sonra: ortalama R ≥ +0.05 ve kâr faktörü ≥ 1.2.
2. Kazanma oranı ≥ %60 (kullanıcının hedefi), TEST bölümünde.
3. Yılların en az %70'inde pozitif (geliştirme dönemi, yıl yıl).
4. Rastgele girişten anlamlı derecede iyi: aynı stop/hedefle, aynı sembol ve yılda rastgele seçilen
   girişlere karşı permütasyon testi p < 0.05. (Sadece "yükselen piyasada alış" şansını ayıklar.)
5. Maliyet stresi: spread x1.5'te ortalama R hâlâ pozitif.
6. Komşu parametreler (±%20): hepsinde ortalama R pozitif (tek bir "sihirli" ayara bağlı değil).
7. KİLİTLİ dönem (tek sefer): ortalama R > 0, kâr faktörü > 1.0, kazanma oranı ≥ %55.

Hiçbir aday bunları sağlamazsa sonuç "kenar bulunamadı" olarak raporlanır.

Not: %60 kazanma oranı tek başına kârlılık değildir. Hedef stoptan küçükse sıfır kenarla bile
kazanma oranı yüksek çıkar (ör. hedef 1 ATR, stop 2 ATR → rastgele girişte ~%67). Bu yüzden asıl
ölçüt 1, 4 ve 7'dir.

## 3. Ön kayıtlı hipotezler (test edilmeden ÖNCE yazıldı)

Hepsinde giriş: sinyal mumunun kapanışından sonraki mumun açılışı (piyasa emri). Risk: işlem başı %0.5.
z = (kapanış − EMA20) / ATR14 (H1). "D1 yükseliş trendi" = dünkü günlük kapanış > 200 günlük ortalama.

| # | Ad | Sinyal | Çıkış | Gerekçe |
|---|---|---|---|---|
| H0 | Mevcut strateji | Trend/kırılım skoru ≥ 60 (zip'teki kurallar) | SL 1.5 ATR, TP 2R, 1R'de başa baş, 1.5R'den sonra takip | Karşılaştırma tabanı |
| H1 | Endekslerde trendde dip alımı | D1 yükseliş trendi + z ≤ −2 (sadece alış) | TP +1 ATR, SL −2 ATR, 24 mum zaman stopu | Hisse risk primi + satış baskısında likidite sağlama; literatürde kısa vadeli geri dönüş |
| H2 | Altın ve kriptoda trendde dip alımı | H1 ile aynı (sadece alış) | H1 ile aynı | Güçlü trend dönemlerinde geri çekilmelerin alınması |
| H3 | Forex'te trend yönünde geri çekilme | D1 trendi yukarı + z ≤ −2 → alış; aşağı + z ≥ +2 → satış | H1 ile aynı | H1'in iki yönlü hali |
| H4 | Forex Asya seansı ortalamaya dönüş | 21:00-07:00 UTC arası z ≤ −2 → alış, z ≥ +2 → satış (trend filtresi yok) | H1 ile aynı | Düşük oynaklık saatlerinde bant içi hareket |
| H5 | Mevcut sinyal + kısa hedef | H0'ın sinyali (skor ≥ 60) | TP +1 ATR, SL −2 ATR, 24 mum zaman stopu, başa baş/takip yok | H0 sinyalinde 1 ATR ufkunda yön kenarı var mı? |

| H6 | Endekslerde GÜNLÜK dip alımı | Dünkü günlük kapanış > 200 günlük ortalama ve günlük RSI(2) < 10; yeni günün ilk H1 mumu kapanınca alış | TP +1 günlük ATR, SL −2 günlük ATR, 120 mum (~5 gün) zaman stopu | Literatürde en iyi belgelenmiş kısa vadeli geri dönüş etkisi (RSI-2 tipi); günlük ölçekte maliyet R'ye göre çok küçük |
| H7 | Altın ve kriptoda GÜNLÜK dip alımı | H6 ile aynı | H6 ile aynı | H6'nın diğer "uzun vadede yükselen" varlıklara genellemesi |

H6 ve H7, H0-H5 çalıştırılmadan ÖNCE eklendi (hiçbir sonuç görülmeden). Gerekçe: H1 ölçeğinde stop
mesafesi küçük olduğu için spread + finansman işlem başına ~0.1R tutuyor; günlük ölçekte ~0.03R.

Varsayılan değerler (z eşiği 2.0, TP 1.0 ATR, SL 2.0 ATR, 24 mum) önceden seçildi; komşuları
(z: 1.6/2.4, TP: 0.8/1.2, SL: 1.6/2.4, süre: 19/29) sadece hassasiyet için kullanılır, "en iyisini
seçmek" için değil.

## 4. Sonuçlar

### 4.1 Veri temizliği (sonuçlardan önce)
- HistData `GRXEUR` (DAX) dosyası 2020-06-15 → 2023-12-01 arasında başka bir endeksin verisini içeriyor
  (seviye ve seans saatleri Euro Stoxx 50 ile uyumlu). Temiz iki bölüm `GER40` ve `GER40B`, aradaki
  bölüm `EU50B` olarak ayrı semboller yapıldı.
- HistData `ETXEUR` (Euro Stoxx 50) 2018-12-17'den sonra ~8900 seviyesinde başka bir endeks içeriyor: kesildi.
- `WTIUSD` (petrol) çıkarıldı: kaynakta 2024-2025 yok.
- FXCM mum verisindeki spread ham/ECN spread'dir (EURUSD medyan 0.2 pip). Gerçekçi olması için forex'e
  gidiş-dönüş %0.007 komisyon eklendi (100k'da ~7 USD).
- Emtia/endeks spread modeli sabit değer yerine max(taban, fiyatın yüzdesi) yapıldı (2015-2026'da
  fiyatlar birkaç katına çıktı).
- Son evren: 14 forex, 8 kripto, 2 emtia (altın, gümüş), 8 endeks serisi = 32 seri.
- Dönemler: eğitim 2015-03-03 → 2021-09-16, TEST → 2024-07-06, KİLİTLİ → 2026-11-07.

### 4.2 H0 — mevcut strateji: BAŞARISIZ
Eğitim: 48.893 işlem, kazanma %35.8, ort −0.074R, KF 0.88. TEST: 23.578 işlem, kazanma %35.3,
ort −0.090R (%95 GA [−0.117, −0.063]), KF 0.85. Her yıl (2015-2024) ve her sınıfta negatif.
Rastgele girişten farkı yok (p = 0.72 / 0.97). Maliyetsiz bile ort −0.015R: sinyalin kenarı yok.
Canlı portföy kurallarıyla TEST: ort −0.137R, yıllık ≈ −%116 (bileşiksiz). Walk-forward: hangi eşik
seçilirse seçilsin negatif.

### 4.3 H1-H7: HEPSİ BAŞARISIZ
| # | Eğitim ort R / kazanma | TEST ort R / kazanma | Rastgele girişe karşı p (TEST) |
|---|---|---|---|
| H1 | −0.033 / %64.6 | −0.054 / %63.2 | 0.88 |
| H2 | −0.040 / %63.9 | −0.053 / %62.7 | 0.54 |
| H3 | −0.052 / %64.6 | −0.047 / %64.9 | 0.60 |
| H4 | −0.062 / %63.6 | −0.091 / %62.0 | 0.99 |
| H5 | −0.052 / %64.0 | −0.064 / %63.2 | 1.00 |
| H6 | −0.096 / %58.1 (105 işlem) | −0.071 / %59.5 (42 işlem) | 0.62 |
| H7 | +0.013 / %58.4 | −0.033 / %52.1 | 0.72 |

Ders: %60+ kazanma oranı bu çıkış geometrisiyle RASTGELE girişte de çıkıyor (%63-65). Kazanma oranı
kenar değildir.

### 4.4 Olay incelemesi (keşif, sadece eğitim dönemi) — `python -m arastirma.olay`
Çıkış kuralından bağımsız, koşullu ileri getiriler (günlük ATR birimi, haftalık kümelenmiş t):
- Forex, endeks, emtia: |t| > 3 ve ekonomik olarak anlamlı hiçbir yapı yok. (Forex'te günün ilk mumundaki
  +0.02 ATR, gece yarısı spread genişlemesinin alış fiyatına etkisi; işlem yapılamaz.)
- **Kripto: güçlü momentum.** "Dünkü günlük kapanış 20 günün zirvesinde" → sonraki 120 mumda koşulsuzun
  +0.60 günlük ATR üstünde (t = 4.5). "Yükseliş trendinde H1 z ≥ +2" → +0.81 (t = 4.8).
  Mevcut skor ≥ 60 sinyali de kriptoda +0.29 (t = 3.4): yön bilgisi var ama dar stop/hedef bunu harcıyor.
- Uyarı: eğitim dönemi 2017 ve 2020-21 boğa piyasalarını içeriyor; etki rejime bağlı olabilir.

### 4.5 Kripto momentum ailesi (TEST'e bakılmadan ÖNCE tanımlandı)
Olay incelemesinden sonra, sadece eğitim verisine dayanarak tanımlandı. Hepsi sadece alış, sadece kripto.
| # | Sinyal | Çıkış |
|---|---|---|
| K1 | Günün ilk mumu kapanınca: dünkü günlük kapanış ≥ son 20 günün en yüksek kapanışı ve > SMA200 | TP +1 günlük ATR, SL −2 günlük ATR, 120 mum (H6/H7 ile aynı; yüksek kazanma oranı hedefli) |
| K2 | K1 ile aynı | Sadece SL −2 günlük ATR ve 240 mum (10 gün) zaman stopu; hedef yok (kazananı kesmeyen sürüm) |
| K3 | Yükseliş trendi (D1 > SMA200) ve H1 z ≥ +2 | K1 ile aynı |

Seçim kuralı: aile içinden en fazla BİR sürüm, eğitim sonuçlarına göre seçilir; TEST sadece doğrulama
içindir. Toplam denenen hipotez sayısı bu satırla birlikte 11'dir (H0-H7, K1-K3).

### 4.6 K ailesi — geliştirme dönemi sonuçları
| | Eğitim (sembol başına) | TEST (sembol başına) | Rastgele girişe karşı p (eğitim / TEST) | TEST, canlı portföy kuralları |
|---|---|---|---|---|
| K1 | 530 işlem, %69.6, +0.115R, KF 1.52 | 302 işlem, %61.3, +0.039R, KF 1.16 | 0.0005 / 0.17 | 198 işlem, %60.6, +0.053R, KF 1.23, yıllık %1.9, düşüş %3.6 |
| K2 | 203 işlem, %53.2, +0.889R, KF 3.59 | 141 işlem, %46.8, +0.249R, KF 1.58 | 0.0005 / 0.005 | 85 işlem, %45.9, +0.240R, KF 1.61, yıllık %3.6, düşüş %4.2 |
| K3 | 1147 işlem, %66.2, +0.082R | 629 işlem, %55.8, −0.031R | 0.002 / 0.93 | — (BAŞARISIZ) |

Sağlamlık yoklaması (`python -m arastirma.calisma.k_saglamlik`, sadece geliştirme dönemi):
- K2: kırılım penceresi 16/24 gün, SMA200 filtresiz, gün sınırı 3/7/12 saat kaydırılmış → TEST'te hepsi
  pozitif (ort +0.14 … +0.25R, KF 1.29-1.60). Ters sinyal (20 günün dibinde alış) negatif. Finansman x3
  (%60/yıl) ile TEST portföy: +0.144R, KF 1.33.
- K2'nin zayıf yanları: kâr birkaç büyük kazanca bağlı (TEST'te en iyi 5 işlem çıkınca +0.249 → +0.085R);
  XRP ve LTC'de TEST negatif; ayı yıllarında (2018, 2022) az işlem ve küçük kayıp; eğitimden TEST'e
  ortalama R 0.89'dan 0.25'e düşmüş (etki zayıflıyor olabilir).
- K1: gün sınırı 12 saat kayınca TEST negatife dönüyor (−0.018R). Kırılgan. Kazanma oranı %60+ ama
  kenarı küçük.
- Yıllar: K2 2019, 2020, 2021, 2023, 2024 pozitif; 2018 (4 işlem) ve 2022 (10 işlem) negatif.

Temel gözlem: momentumun kazancı sağ kuyrukta (büyük hareketler). Hedefi kısa tutup kazanma oranını
%60'a çıkarmak (K1) kenarın çoğunu siliyor. "Kazanma oranı ≥ %60" ile "pozitif beklenti" bu etki için
birbiriyle çekişiyor.

Ölçütlere göre durum: K2, ölçüt 2 (kazanma ≥ %60) dışında hepsini geliştirme döneminde sağlıyor.
K1, ölçüt 2'yi sağlıyor ama ölçüt 1 ve 4'ü sembol başına tabloda sağlamıyor.
Getiri beklentisi (işlem başı %0.5 risk, en fazla 2 kripto pozisyonu): K2 yılda ~%3-5, K1 ~%2. Küçük.

### 4.7 Kilitli dönem planı (KİLİT AÇILMADAN ÖNCE yazıldı)
- Kilitli dönem (2024-07-06 → 2026-11-07) TEK seferde açılır; K1 ve K2 birlikte, tanımları yukarıdaki
  gibi dondurulmuş olarak test edilir. Başka hiçbir sürüm kilitli dönemde denenmez.
- Evren: 8 kripto. Maliyetler: baz model. Hem sembol başına hem canlı portföy kurallarıyla raporlanır.
- Geçme ölçütü K2 (birincil, kanıtı güçlü olan): ortalama R > 0, kâr faktörü > 1.0 ve rastgele girişe
  karşı p < 0.10.
- Geçme ölçütü K1 (kazanma oranı hedefli): ortalama R > 0, kâr faktörü > 1.0 ve kazanma oranı ≥ %55.
- Karar kuralı: K2 geçerse bota K2 varsayılan olarak konur (kazanma oranının %60'ın altında olduğu
  açıkça belirtilerek). K1 de geçerse config'te seçenek olarak sunulur. Hiçbiri geçmezse bot varsayılan
  olarak işlem açmaz ve sonuç "doğrulanmış kenar yok" olarak raporlanır.

### 4.8 KİLİTLİ DÖNEM SONUCU (tek açılış, 2024-07-06 → 2026-11-07) — `kayitlar/kilit.json`

| | Sembol başına | Rastgele girişe karşı | Canlı portföy kuralları |
|---|---|---|---|
| K1 | 226 işlem, kazanma %60.2, ort +0.015R (%95 GA [−0.12, +0.12]), KF 1.06 | fark +0.038R, p = 0.157 | 132 işlem, %55.3, **−0.044R, KF 0.86, yıllık −%1.2**, düşüş %5.2 |
| K2 | 102 işlem, kazanma %45.1, ort +0.323R (%95 GA [−0.15, +0.87]), KF 1.78 | fark +0.274R, **p = 0.019** | 55 işlem, %47.3, **+0.209R, KF 1.50, yıllık +%2.5**, düşüş %4.0 (MC %95: %5.7) |

K2 yıl yıl: 2024 (Tem-Ara) +0.976R (38 işlem); 2025 −0.029R (46 işlem); 2026 −0.158R (18 işlem).
K2 coin coin: XRP +18.8R (toplam +32.9R'nin %57'si), DOGE +5.2R, ADA +5.0R, ETH +3.2R, BTC +2.7R,
BNB +0.7R, SOL −0.3R, LTC −2.3R.

**Karar (4.7'deki kurala göre):**
- **K1 REDDEDİLDİ.** Sembol başına tabloda sıfıra yakın (+0.015R, anlamsız), canlı portföy kurallarıyla
  zararda. "Kazanma oranı ≥ %60" hedefini sağlayan ama para kazandıran bir strateji BULUNAMADI.
- **K2 ölçütü geçti** (ort R > 0, KF > 1, p < 0.10) ve bota varsayılan strateji olarak konur.

**K2 hakkında dürüst çekinceler:**
1. Kazanma oranı ~%45-50. Kullanıcının %60 hedefini sağlamıyor.
2. Kilitli dönemdeki kârın tamamı 2024 sonundaki kripto rallisinden; 2025 ve 2026 hafif negatif.
   Strateji güçlü boğa dönemlerinde kazanıyor, diğer zamanlarda az az kaybediyor.
3. Kâr birkaç büyük işleme ve (kilitli dönemde) tek bir coine yoğunlaşmış. %95 güven aralığı sıfırı içeriyor.
4. Getiri küçük: işlem başı %0.5 risk ve en fazla 2 kripto pozisyonuyla yılda ~%2.5-5, düşüş ~%4-6.
5. Etki zamanla zayıflıyor olabilir (eğitim +0.89R → TEST +0.25R → kilitli +0.32R, ama son 22 ay ≤ 0).
6. Veri broker verisi değil; kripto CFD spread ve finansmanı modellenmiş. Pahalı broker'da kenar küçülür
   (finansman %60/yıl ile TEST portföy +0.144R).

**Toplam denenen hipotez: 11** (H0-H7, K1-K3). Ölçütlerin hepsini (kazanma oranı dahil) sağlayan: 0.
Kazanma oranı ölçütü dışında hepsini sağlayan: 1 (K2).
