# Araştırma Raporu 2 ve Test Planı — dış repolardan strateji / risk fikirleri

Tarih: 2026-11-11. Durum: **sadece rapor ve plan.** Bu belgede anlatılan hiçbir strateji henüz kodlanmadı,
test edilmedi ve bota eklenmedi. Onaydan sonra Bölüm 6'daki sırayla ilerlenecek.

Değişmeyenler: `sadece_demo: true` kilidi yerinde kalır, gerçek hesapta emir yetkisi eklenmez, risk kuralları
(işlem başı %0.5, günlük %2, lot hesabı) LLM'den bağımsız kalır.

İncelenen repolar (salt okunur incelendi, içlerinden hiçbir kod çalıştırılmadı ve projeye kopyalanmadı):

| Repo | İncelenen sürüm | Ne olduğu |
|---|---|---|
| Jakeschincariol/day-trader-skill | 3d0f4ef | ABD hisseleri için gün içi işlem yardımcı aracı (tarayıcı, ORB girişi, risk kapısı) |
| freqtrade/freqtrade-strategies | f3340ce | Freqtrade için 66 örnek kripto stratejisi |
| QuantConnect/Lean | ana dal, sığ kopya | Olay güdümlü backtest/canlı işlem motoru (C#), örnek alfa/risk/maliyet modelleri |

---

## 1. Repolardan ne çıktı

### 1.1 day-trader-skill
- **Kenar kanıtı yok.** Repo kendi belgesinde "gün içi işlem çoğu insana para kaybettirir" diyor; hiçbir
  backtest sonucu, işlem kaydı ya da maliyet sonrası performans yok. Kurallar bir fikir kaynağıdır, kanıt değil.
- ORB kuralı: seansın ilk 15 dakikası (üç 5 dk mum) aralık; tetik = aralığın üstünde **kapanan** ilk mum
  (fitil değil kapanış); isteğe bağlı hacim onayı (tetik mumunun hacmi, aralık mumlarının ortalamasının
  katı); 09:45'ten önce / 15:30'dan sonra giriş yok; 15:55'te düz. Stop: aralık ortası (varsayılan), aralık
  dibi, yüzde ya da ATR×K. Hedef 2R. Sadece alış.
- Günlük kurulumlar: 20 günlük kapanış kırılımı, boğa bayrağı, MA20 geri alma, RSI14<30.
- **İşe yarar risk fikirleri:**
  1. Günlük zarar bütçesinde bugün açılan bütün işlemlerin *tam azami zararını* saymak (Ajan zaten açık riski
     sayıyor — uyumlu).
  2. Girişi "plan fiyatı ile taze fiyatın kötü olanından" ölçmek (Ajan'da kayma referansı zaten böyle).
  3. Günlük azami işlem sayısı, giriş saat penceresi, aynı sembolde tekrar giriş engeli.
  4. Başa baş kazanma oranı = 1/(1+R) tablosu: hedef/stop oranına göre WR'nin tek başına anlamsız olduğunu
     gösterir (bizim H1–H7 bulgumuzla aynı ders).

### 1.2 freqtrade-strategies
- 66 stratejinin çoğu 1m/5m, sadece alış, kripto. Hyperopt çıktısı ROI tabloları, %−25…−35 stoplar ve 5 haneli
  parametreler aşırı uydurma işareti. Repoda hiçbir strateji için örneklem dışı sonuç yok.
- H1/H4'e uyanlar ve kuralları:

| Strateji | ZD | Kural | Aile |
|---|---|---|---|
| BbandRsi | 1h | RSI<30 ve kapanış < alt Bollinger; çıkış RSI>70 | ortalamaya dönüş |
| ADXMomentum | 1h | ADX>25, momentum>0, +DI>25, +DI>−DI | trend devamı |
| hlhb | 4h | RSI 50'yi keser, EMA5 EMA10'u keser, ADX>25 | trend devamı |
| futures/VolatilitySystem | 1h (3h'e yeniden örneklenmiş) | 3 saatlik kapanış değişimi > 2×ATR(14) → o yönde gir; ters sinyalde çevir; alış ve satış | oynaklık kırılımı |
| futures/TrendFollowingStrategy | — | fiyat trend çizgisinin üstünde ve OBV yükseliyor | trend + hacim |
| Supertrend, AdxSmas, AwesomeMacd | 1h | klasik gösterge kesişimleri | trend |
| HourBasedStrategy | 1h | günün saatine göre al/sat | mevsimsellik (aşırı uydurmaya çok açık) |

- **Hacim kullanımı:** neredeyse hep "pump'tan kaçın" filtresi (hacim < 20×ortalama) ya da "hacim > 0";
  kırılımı hacimle *onaylayan* bir örnek yok (OBV'li tek örnek hariç).
- **Gelecek verisi hatası dersi** (`lookahead_bias/` klasörü): DevilStra, GodStraNew, Zeus normalleştirmede
  bütün seri üzerinde `.min()/.max()` kullanıyor; wtc `MinMaxScaler.fit_transform` kullanıyor. Yani hata
  "tüm seriden hesaplanan istatistik". Ayrıca Strategy004 içindeki yorum da `volume.mean()`'in bütün veriyi
  kullandığını not ediyor. Bölüm 4.2'deki korumalar bu hatayı hedefliyor.

### 1.3 QuantConnect Lean
- Hazır strateji kaynağı değil, motor. Alfa modelleri bilerek basit: RSI 30/70 (35/65 histerezisli),
  MACD, EMA kesişimi, geçmiş getiri. Hiçbiri kenar iddiası taşımıyor. ORB örneği yok.
- **İşe yarar tasarım fikirleri:**
  1. Katman ayrımı: Alfa (sinyal) → Portföy → **Risk (bağımsız, son sözü söyler)** → Emir. Ajan'ın "risk LLM'den
     bağımsız" kuralıyla aynı felsefe; yeni stratejiler de sadece "aday" üretecek, risk katmanına dokunmayacak.
  2. `MaximumDrawdownPercentPortfolio(is_trailing)`: portföy düşüşü başlangıca (statik) ya da zirveye (takip
     eden) göre ölçülür ve aşılınca her şey kapatılır. Prop firma "toplam zarar" kuralının iki türünün bire
     bir karşılığı → simülasyonda ikisi de modellenecek (Bölüm 4.4).
  3. `TrailingStopRiskManagementModel`: pozisyon başına en iyi değerden % geri çekilme stopu → K2 için
     denenmemiş "takip eden çıkış" hipotezi (Bölüm 3, T2).
  4. `SpreadExecutionModel`: spread fiyatın belli bir yüzdesinden genişse emir gönderme. Ajan'da azami spread
     filtresi var; backtest'te de aynı filtre uygulanmalı (parite).
  5. Maliyet modelleri: `ConstantSlippageModel` (fiyatın sabit yüzdesi), `VolumeShareSlippageModel` (emir /
     mum hacmi)² × etki — ancak Lean'in kendisi Forex/CFD/kriptoda hacim bildirilmediği için bu modelin 0
     döndüğünü logluyor; yani bizim enstrümanlarda hacme dayalı kayma modeli kurulamaz, sabit/spread katı
     modeli kullanılacak. `FxcmFeeModel`: majörlerde taraf başı 1k lot için 0.04 USD (gidiş-dönüş ≈ %0.008),
     diğer paritelerde 0.06 USD (≈ %0.012). **Bizim varsayımımız %0.007 idi → hafif iyimser**; yeni testlerde
     %0.008 / %0.012 kullanılacak.
  6. Olay güdümlü yapı ve ısınma (warm-up): gösterge ancak yeterli geçmiş birikince "hazır" sayılır; günlük
     mum ancak gün kapanınca oluşur. Bizim "kapanmış mum + önceki günün D1 değeri" kuralımızın aynısı.

**Özet:** Üç repoda da doğrulanmış, maliyet sonrası kârlı bir strateji yok. Alınabilecek olan şey kural
şablonları (ORB, oynaklık kırılımı, BB+RSI), risk/maliyet modelleme fikirleri ve gelecek verisi hatası
örnekleridir. Kenar olup olmadığını kendi verimizde ölçmek zorundayız.

---

## 2. Dört strateji ailesinin değerlendirmesi ve mevcut Ajan ile karşılaştırma

Mevcut durum (`docs/ARASTIRMA_GUNLUGU.md`): 11 hipotez denendi (H0–H7, K1–K3). Bota konan tek strateji
**K2 = `gunluk_kirilim`** (sadece kripto, sadece alış).

| | İşlem | WR | Ort R | PF | Yıllık | Azami düşüş |
|---|---|---|---|---|---|---|
| K2 eğitim (portföy) | 114 | %50.9 | +0.59 | 2.62 | — | — |
| K2 TEST (portföy) | 85 | %45.9 | +0.24 | 1.61 | ~%3.6 | %4.2 |
| K2 kilitli (tek açılış) | 55 | %47.3 | +0.21 | 1.50 | ~%2.5 | %4.0 |
| Eski zip stratejisi (trend_skor) TEST | — | — | −0.09 | 0.85 | negatif | — |

K2'nin bilinen zayıflıkları: getiri küçük, WR %60 hedefinin altında, kâr birkaç büyük işleme bağlı, 55 işlemlik
kilitli örneklem küçük (p = 0.019), sadece kripto.

| Aile | Daha önce denenen | Sonuç | Bu turda yeni olan | Dürüst ön beklenti |
|---|---|---|---|---|
| **ORB** | Hiç | — | Seans açılışı aralık kırılımı (M15); forex/endeks/altın | Belirsiz. Gün içi olduğu için stop dar → spread/komisyon R cinsinden pahalı. En büyük risk maliyet. |
| **Kırılım + hacim onayı** | K1/K2 (hacimsiz) | K2 geçti, K1 kaldı | K2 sinyaline hacim şartı; ORB'ye hacim şartı | Hacim sadece kriptoda gerçek (aşağıya bak). İşlem sayısını azaltacağı kesin, kaliteyi artıracağı belirsiz. |
| **Trend devamı** | H0 (skor), H3 (forex geri çekilme), H5, K3 (H1 z≥2) | Hepsi kaldı | Oynaklık kırılımı (VolatilitySystem tarzı, H4), K2'ye takip eden stop | Düşük–orta. Olay incelemesinde forex/endeks/emtiada yön bilgisi bulunmadı; kriptoda var. |
| **Ortalamaya dönüş** | H1, H2, H6, H7 (trendde dip alımı), H4 (Asya seansı) | Hepsi kaldı (WR %58–65 ama rastgele giriş de %63–65) | Yatay rejim filtreli (ADX düşük) Bollinger dönüşü, çapraz paritelerde, H4 | Düşük. Önceki 5 denemenin hiçbiri rastgele girişi geçemedi. |

**Hacim verisi kısıtı (önemli):** elimizdeki veride gerçek hacim sadece Binance'te var (şu an işlem *sayısı*
saklanıyor; işlem *hacmi* için yeniden indirme gerekir). FXCM verisinde hacim yok (sabit 60 yazılmış),
HistData'da sadece M1 mum sayısı var. MT5'in forex/CFD'de verdiği de tick hacmidir ve brokerdan brokera
değişir. Bu yüzden "hacim onayı" hipotezi **yalnızca kriptoda** dürüstçe test edilebilir; forex/endekste
tick hacmiyle bulunan bir sonuç başka brokerda tekrarlanmayabilir ve bu turda test edilmeyecek.

**%60 WR hedefi hakkında:** önceki bulgu geçerli. WR, hedef/stop oranının bir fonksiyonu; kenar ölçütü
değil. Bu turda da asıl ölçütler beklenti (ort R), PF ve rastgele girişe karşı test olacak; WR raporlanacak
ama tek başına kabul sebebi olmayacak.

---

## 3. Ön kayıtlı adaylar (test edilmeden ÖNCE yazıldı)

Kurallar bu belgeyle donduruldu. Toplam 6 yeni hipotez; önceki 11 ile birlikte 17. Her aday sonuçtan
bağımsız raporlanacak. Parametreler kaynak repolardaki varsayılanlardan ya da yuvarlak değerlerden alındı;
hiçbiri veriye bakılarak seçilmedi.

| # | Aile | Evren | ZD | Giriş | Çıkış |
|---|---|---|---|---|---|
| **O1** | ORB | Londra açılışı (08:00 Londra): EURUSD, GBPUSD, EURGBP, EURJPY, GBPJPY, GER40, UK100, EU50. New York açılışı (09:30 NY): US500, US100, XAUUSD | M15 | Aralık = açılıştan sonraki ilk 2 M15 mumu (30 dk). Açılıştan sonraki 3 saat içinde aralığın dışında **kapanan** ilk M15 mumunun kapanışında, kırılım yönünde (alış ve satış). Sembol başına günde 1 işlem. | Stop: aralığın karşı kenarı. Hedef yok. Seans sonunda kapat (Londra grubu 16:30 Londra, NY grubu 15:55 NY). |
| **O2** | ORB | O1 ile aynı | M15 | O1 ile aynı | Stop aynı; hedef +1R; seans sonunda kapat. (Yüksek WR sürümü.) |
| **V1** | Kırılım + hacim | Kripto (8 coin, Binance gerçek hacim) | H1 (günlük sinyal) | K2 sinyali **ve** kırılım gününün hacmi ≥ önceki 20 günün ortalamasının 1.5 katı | K2 ile aynı (SL 2 günlük ATR, 240 mum) |
| **T1** | Trend devamı (oynaklık kırılımı) | Bütün sınıflar; sınıf sınıf ayrı raporlanır | H4 | Son kapanan H4 mumunun kapanış değişimi, bir önceki mumdaki 2×ATR(14, H4)'ten büyükse o yönde gir (alış ve satış) | SL 2×ATR(H4); ters sinyalde kapat; 60 mum (10 gün) zaman stopu |
| **T2** | Trend devamı (çıkış iyileştirme) | Kripto | H1 | K2 sinyali | Başlangıç SL 2 günlük ATR; sonra girişten beri en yüksek günlük kapanış − 3 günlük ATR takip eden stop (sadece gün kapanışında güncellenir); zaman stopu yok |
| **M1** | Ortalamaya dönüş | Forex çaprazları: EURGBP, AUDNZD, EURAUD, AUDJPY, CADJPY + USDCHF, USDCAD | H4 | ADX(14)<20 **ve** kapanış Bollinger(20, 2.0) bandının dışında → banda ters yönde gir | Hedef: SMA20 (her mumda güncellenir); SL 2×ATR(H4); 30 mum zaman stopu |

Kıyas tabanları: **K2 (mevcut)** ve aynı çıkış geometrisiyle **rastgele giriş**. V1 ve T2 doğrudan K2 ile
karşılaştırılır (aynı sinyal ailesi); "iyileşme" = eğitim + doğrulamada K2'den daha iyi ort R **ve** daha
kötü olmayan azami düşüş.

Bilerek dışarıda bırakılanlar: saat-bazlı strateji (HourBasedStrategy — çoklu test riski çok yüksek),
hyperopt ile bulunmuş ROI tabloları, forex/endekste tick hacmi onayı, kriptoda ORB (seans açılışı yok).

---

## 4. Test planı

### 4.1 Modül yapısı — mevcut sistemi bozmadan
```
arastirma/stratejiler/
    __init__.py        ortak arayüz: sinyal(sembol_verisi, prm) -> (yön dizisi, stop mesafesi dizisi, çıkış tanımı)
    orb.py             O1, O2
    hacim_kirilim.py   V1
    oynaklik.py        T1
    takip_cikis.py     T2
    bant_donus.py      M1
arastirma/prop.py      prop firma kural simülasyonu (4.4)
arastirma/deney2.py    bu planın koşucusu (dönemler, tablolar, eleme)
tests/test_stratejiler.py, tests/test_prop.py
```
- `bot/` altındaki hiçbir dosya değişmeyecek; `config.yaml` değişmeyecek; mevcut 220 test aynen geçmeli.
- Stratejiler mevcut `arastirma/motor.py` simülatörünü kullanacak (aynı maliyet ve dolum kuralları → K2 ile
  elma-elma kıyas). Motora eklenecekler: kayma parametresi, M15 veri desteği, seans sonu çıkışı, H1
  bazında değerlenmiş (mark-to-market) özvarlık eğrisi.
- Bir strateji ancak bütün ölçütleri geçer ve siz onay verirseniz `bot/strateji.py`'ye taşınır (ayrı bir iş).

### 4.2 Gelecek verisine bakmayı önleme
1. Sinyal sadece **kapanmış** mumdan hesaplanır; giriş bir sonraki mumun açılışından (O1/O2'de tetik mumunun
   kapanışı = sonraki mumun açılışı) yapılır.
2. Günlük değerler (ATR, zirve, hacim ortalaması) **önceki günün** kapanışına kadar olan veriden gelir.
3. Bütün seriden hesaplanan istatistik yasak (min/max/ortalama/ölçekleme sadece geriye bakan pencereyle) —
   freqtrade `lookahead_bias` örneklerindeki hata.
4. **Otomatik kesme testi** (her modül için birim testi): sinyal dizisi tam veriyle ve verinin t anında
   kesilmiş haliyle hesaplanır; t'ye kadarki değerler bit bit aynı olmalı. Rastgele 50 kesme noktası.
5. **Gecikme testi**: sinyali 1 mum geciktirince sonuç dramatik iyileşiyorsa ya da 1 mum öne alınca
   (bilerek hata) sonuç aynı kalıyorsa sızıntı şüphesi → incelenir.
6. Seans saatleri yaz/kış saati dahil yerel saatten (Londra, New York) hesaplanır; sabit UTC ofseti kullanılmaz.
7. Aynı mumda stop ve hedef görülürse stop sayılır (mevcut kural); boşlukla stop aşılırsa açılıştan dolar.

### 4.3 Maliyetler: spread, komisyon, kayma, finansman
| Kalem | Temel senaryo | Stres |
|---|---|---|
| Spread | Veri kaynağındaki gerçek/modellenmiş spread (mevcut) | ×1.5 ve ×2 |
| Komisyon | Forex majör %0.008, diğer %0.012 gidiş-dönüş (Lean FXCM modeli); kripto/endeks mevcut değerler | ×2 |
| Kayma (yeni) | Piyasa girişinde ve stop çıkışında, aleyhte 0.25 × o mumun spread'i | 0.5× ve 1× spread; stop çıkışında ayrıca 0.05 × ATR |
| Finansman/swap | Mevcut model | ×3 |
| Spread filtresi | Botla aynı azami spread kuralı backtest'te de uygulanır | — |

ORB için ek kontrol: stop mesafesi / (spread + komisyon) oranı raporlanır. Maliyet, ortalama stop mesafesinin
%10'undan fazlaysa bu ayrıca belirtilir (gün içi stratejilerin tipik ölüm sebebi).

### 4.4 Prop firma zarar limitleri simülasyonu
H1 bazında değerlenmiş özvarlık eğrisi üzerinde, portföy kurallarıyla (işlem başı %0.5, günlük %2 dahil):

| Kural | Değerler |
|---|---|
| Günlük zarar limiti | %5 ve %4; gün başı bakiyesine göre, **açık pozisyonların yüzen zararı dahil** (gün içi en düşük özvarlık) |
| Toplam zarar limiti | %10 statik (başlangıca göre) ve %10 / %6 takip eden (zirveye göre) |
| Kâr hedefi | 1. aşama %8 ve %10, 2. aşama %5 |
| Süre | Süresiz ve 30/60 günlük sürümler; asgari 4 işlem günü |

Yöntem: her hafta başından bir "değerlendirme" başlatılır (yüzlerce örtüşen başlangıç). Raporlanan:
geçme oranı, limit ihlaliyle başarısız olma oranı, sonuçlanmayanların oranı, hedefe medyan süre, en kötü
gün, limite en yakın mesafe. Ayrıca işlem sırası karıştırılarak (haftalık blok) Monte Carlo ile %95 aralık.
Gün içi mum içi hareket bilinmediği için günlük limit kontrolü mumun en kötü fiyatıyla (alışta dip, satışta
tepe + spread) yapılır — kötümser taraf.

Önceden belirtmek gerekir: K2 düzeyinde bir getiriyle (yılda ~%3) %8–10 hedefe ulaşmak yıllar sürer; işlem
başı risk %0.5 kuralı sabit kaldığı sürece prop değerlendirmesinde "ihlal" olasılığı düşük ama "hedefe
ulaşamama" olasılığı yüksek çıkması beklenir. Sonuç neyse o raporlanacak; riski artırarak hedefe yetiştirme
denenmeyecek.

### 4.5 Veri ayrımı: eğitim / doğrulama / hiç dokunulmamış test
| Bölüm | Tarih | Kullanım | Dürüstlük notu |
|---|---|---|---|
| Eğitim | 2015-03-03 → 2021-09-16 | Kural hatalarını ayıklama, V1/T2'nin K2 ile kıyası, O1/O2 arasından seçim | Serbestçe bakılır |
| Doğrulama | 2021-09-16 → 2024-07-06 | Eğitimde seçilen sürümün onayı; ayar yapılmaz | Bu dönem önceki 11 hipotezde "TEST" olarak kullanıldı; artık bakir değil, o yüzden adı doğrulama |
| **Dokunulmamış test** | 2024-07-06 → 2026-11-07 | En sonda, **tek sefer**, sadece doğrulamayı geçen adaylar | Sınıfa göre durum aşağıda |
| İleriye dönük | 2026-11-07 sonrası | Demo/sinyal modu kayıtları (`araclar.py rapor`) | Gerçekten görülmemiş tek veri |

Dokunulmamış test döneminin gerçek durumu:
- **Forex, endeks, emtia:** bu dönemde bugüne kadar hiçbir stratejinin sonucuna bakılmadı → O1, O2, T1
  (kripto dışı), M1 için gerçekten bakir.
- **Kripto:** bu dönem K1/K2 için bir kez açıldı. V1 ve T2 için bakir **değil**. Bu yüzden V1/T2 için
  geçmiş veride "dokunulmamış test" iddia edilmeyecek; kabul için ek olarak en az 3 ay ileriye dönük demo
  kaydı şart koşulacak ve kilitli dönem sonucu "ikinci bakış" etiketiyle raporlanacak.
- M15 verisi (ORB için) yeniden indirilecek (HistData M1, FXCM m1). İndirme sırasında kilit sınırından
  sonraki veriler ayrı dosyaya yazılacak ve son adıma kadar hiçbir tabloya girmeyecek.
- Kilit açılışı `kayitlar/kilit.json`'a ikinci kayıt olarak yazılır (tarih, hangi adaylar, donmuş kural özeti).

Ek doğrulamalar (eğitim + doğrulama üzerinde): yıl yıl tablo, yürüyen pencere (walk-forward), ±%20 komşu
parametreler, rastgele girişe karşı permütasyon (aynı sembol/yıl/yön dağılımı, aynı çıkış), haftalık blok
bootstrap güven aralığı, Monte Carlo azami düşüş %95 aralığı, sınıf ve sembol kırılımı.

### 4.6 Karşılaştırma tablosu (her aday × her dönem, portföy kurallarıyla)
İşlem sayısı · WR · ortalama R (beklenti) · PF · azami düşüş (% ve R) · yıllık getiri · rastgele girişe karşı
p · maliyet stresi sonrası ort R · prop geçme/ihlal oranı. K2 ve rastgele giriş aynı tabloda taban olarak yer alır.

### 4.7 Eleme ölçütleri (sonuçlara bakmadan önce sabitlendi)
Bir aday aşağıdakilerin **hepsini** sağlamazsa elenir ve "elendi" olarak raporlanır:

1. Doğrulamada maliyet + temel kayma sonrası ort R ≥ +0.05 ve PF ≥ 1.2.
2. İşlem sayısı: eğitimde ≥ 200, doğrulamada ≥ 80 (V1/T2 için ≥ 100 / ≥ 40; daha azı "yetersiz örneklem" → elenir).
3. Rastgele girişe karşı permütasyon p < 0.05/6 ≈ 0.008 (6 yeni hipotez için Bonferroni düzeltmesi), doğrulamada.
4. Spread ×1.5 + kayma 0.5× spread senaryosunda ort R > 0.
5. ±%20 komşu parametrelerin hepsinde ort R > 0.
6. Geliştirme döneminde yılların ≥ %70'i pozitif.
7. V1 ve T2 için ek: K2'ye göre ort R'de iyileşme, haftalık blok bootstrap ile farkın %90 aralığı sıfırın
   üstünde. (Sadece "biraz daha iyi görünüyor" yetmez.)
8. Dokunulmamış test (tek sefer): ort R > 0 ve PF > 1.0.
9. Prop simülasyonunda limit ihlali oranı ≤ %5 (statik %10 / günlük %5 kurallarıyla).

WR ≥ %60 ayrıca raporlanır. Ölçüt 1–9'u geçen adaylar arasında WR ≥ %60 olan varsa tercih sebebidir; ama
ölçütleri geçmeyen bir aday WR'si yüksek diye kabul edilmez.

Hiçbir aday geçmezse sonuç "iyileşme yok, K2 ile devam" olarak raporlanır. Altı adaydan birinin şans eseri
iyi görünme olasılığı yüksek olduğu için eşikler bilerek sıkıdır.

---

## 5. Açık riskler ve sınırlamalar
- **Beklenti düşük tutulmalı.** Önceki 11 hipotezden 1'i geçti. Forex/endeks/emtiada olay incelemesi yön
  bilgisi bulamadı; bu turdaki adayların çoğu yine bu sınıflarda. En olası sonuç: adayların çoğu elenir.
- Kripto için bakir geçmiş veri kalmadı; V1/T2 ancak ileriye dönük kayıtla kesinleşir.
- ORB'nin M15 verisi gerektirmesi: botun tarama döngüsü H1'e göre kurulu. O1/O2 geçerse bota taşımak
  (M15 tarama, seans sonu kapatma) ayrı ve daha büyük bir iş; testten önce yapılmayacak.
- Veri brokerınızın verisi değil (bu ortamdan MT5'e erişim yok). Endeks spreadleri modellenmiş. Gün içi
  stratejiler spread varsayımına en duyarlı olanlar.
- Kayma modeli varsayım; gerçek kayma ancak demo kayıtlarından ölçülür.
- Prop simülasyonu belirli bir firmanın kural metnini değil, yaygın kural tiplerini modelliyor. Hedef
  firma belli olursa kuralları birebir girilmeli.
- Komisyon varsayımı önceki testlerde hafif iyimserdi (%0.007 ↔ %0.008–0.012). Önceki forex sonuçları zaten
  negatifti; sonucu değiştirmez ama not edildi.

## 6. Onay sonrası adımlar
1. Motor eklemeleri (kayma, M15, seans çıkışı, saatlik özvarlık) + birim testleri; mevcut K2 sonuçlarının
   kayma = 0 ile birebir aynı çıktığının kontrolü.
2. M15 verisinin indirilmesi; kilit sonrası verinin ayrılması.
3. Altı modül + kesme (lookahead) testleri.
4. Eğitim → doğrulama → sağlamlık → prop simülasyonu; eleme tablosu. **Burada durup rapor.**
5. Kalan aday varsa: onayınızla dokunulmamış test (tek sefer) → rapor.
6. Geçen olursa: bota taşıma önerisi (ayrı onay), önce sinyal/demo modunda ileriye dönük izleme.

---

## 7. SONUÇLAR — eğitim + doğrulama (2026-11-12)

Kod, sonuçlara bakılmadan önce donduruldu ve commit edildi (`06aa589`); ardından tek koşu yapıldı:
`python -m arastirma.deney2 O1 O2 V1 T1 T2 M1`. Tam çıktı: `docs/arastirma2_gelistirme_ciktisi.txt`.
Dokunulmamış test dönemi **açılmadı** (kilit kaydında hâlâ tek açılış var).

Ön kontroller: yeni simülatör kayma = 0 iken K2'de eski motorla birebir aynı sonucu verdi (eğitim 203 işlem
+0.889R, doğrulama 141 işlem +0.249R; portföy 114 / 85 işlem). 246 testin tamamı geçiyor; altı modülün hepsi
kesme (gelecek verisi) testinden geçti.

### 7.1 Karşılaştırma (portföy kurallarıyla, maliyet + temel kayma dahil)
| Aday | Bölüm | İşlem | WR | Ort R | PF | Azami düşüş | Yıllık | p (rastgele) | Prop: geçti / ihlal |
|---|---|---|---|---|---|---|---|---|---|
| **K2 (mevcut)** | eğitim | 114 | %50.9 | +0.588 | 2.60 | %3.5 | %5.1 | 0.0008 | |
| | doğrulama | 85 | %45.9 | +0.235 | 1.59 | %4.3 | %3.6 | 0.005 | %92.6 / %0 |
| O1 (ORB) | eğitim | 5818 | %36.8 | −0.008 | 0.99 | %81.6 | −%3.7 | 0.001 | |
| | doğrulama | 2506 | %36.3 | −0.022 | 0.96 | %54.6 | −%9.7 | 0.002 | %36.3 / %62.1 |
| O2 (ORB + 1R hedef) | eğitim | 7488 | %48.6 | −0.057 | 0.88 | %223 | −%32.9 | 0.000 | |
| | doğrulama | 3390 | %49.6 | −0.046 | 0.90 | %87.8 | −%27.7 | 0.000 | %13.5 / %84.6 |
| V1 (K2 + hacim) | eğitim | 93 | %52.7 | +1.321 | 4.36 | %3.2 | %9.4 | 0.000 | |
| | doğrulama | 67 | %40.3 | +0.246 | 1.58 | %3.9 | %2.9 | 0.005 | %89.5 / %0 |
| T1 (oynaklık kırılımı) | eğitim | 1015 | %30.4 | −0.013 | 0.98 | %22.4 | −%1.0 | 0.0004 | |
| | doğrulama | 495 | %29.1 | −0.068 | 0.90 | %45.1 | −%6.0 | 0.10 | %30.5 / %61.1 |
| T2 (K2 + takip eden stop) | eğitim | 50 | %50.0 | +1.442 | 4.70 | %1.8 | %5.5 | 0.029 | |
| | doğrulama | 35 | %31.4 | +1.010 | 2.77 | %5.3 | %6.3 | 0.023 | %91.8 / %0 |
| M1 (bant dönüşü) | eğitim | 1399 | %57.8 | −0.093 | 0.78 | %71.6 | −%9.9 | 0.99 | |
| | doğrulama | 629 | %62.0 | −0.012 | 0.97 | %10.6 | −%1.4 | 0.002 | %1.0 / %62.9 |

K2 satırındaki küçük fark (önceki +0.240R → +0.235R) yeni eklenen temel kaymadan geliyor. Prop sütunu:
"günlük %5 / toplam %10 statik / hedef %10 / süresiz" kuralı, 488 haftalık başlangıç.

### 7.2 Eleme — altı adayın altısı da ELENDİ
| Aday | Kaldığı ölçütler | Kısa açıklama |
|---|---|---|
| O1 | 1, 4, 5, 6, 9 | Sinyal rastgele girişten anlamlı derecede iyi (fark +0.08R) ama maliyetten sonra beklenti sıfırın altında. Kaymasız bile +0.003R. Sadece endekslerde doğrulama pozitif (+0.059R, PF 1.12), eğitimde endeksler de negatif (−0.034R). |
| O2 | 1, 4, 5, 6, 9 | 1R hedef WR'yi %49'a çıkarıyor ama beklentiyi daha da düşürüyor (−0.05R). |
| V1 | 7 | Kendi başına bütün ölçütleri geçiyor, ama K2'den iyi olduğu gösterilemedi: doğrulamada fark +0.061R, %90 aralığı [−0.090, +0.216]. Eğitimdeki büyük fark (+0.23R) doğrulamada kayboluyor; portföyde yıllık getiri K2'den düşük (%2.9 ↔ %3.6) çünkü işlem sayısı azalıyor. |
| T1 | 1, 3, 4, 5, 6, 9 | Eğitimde sadece kripto pozitif (+0.47R); forex ve endeks negatif. Doğrulamada toplam negatif, rastgele girişten ayırt edilemiyor (p = 0.10). |
| T2 | 3, 7 | En ilginç sonuç: doğrulama portföyünde +1.01R, PF 2.77, yıllık %6.3. Ama 35 işlem; rastgele girişe karşı p = 0.023 (eşik 0.008), K2 farkının %90 aralığı [−0.31, +0.96] sıfırı içeriyor. WR %31'e düşüyor, azami düşüş artıyor (sembol başına %11.2 ↔ K2 %5.8), kâr SOL/BNB/BTC'deki birkaç büyük işleme bağlı, XRP 17 işlemde 1 kazanç. Finansman ×3'te beklenti yarıya iniyor (uzun tutma). Kanıt yetersiz → elendi. |
| M1 | 1, 4, 5, 6, 9 | WR %58–62 ama beklenti ≤ 0: tipik "yüksek WR, kenar yok". Eğitimde rastgele girişten bile kötü (p = 0.99). |

Hiçbir aday ölçütleri geçmediği için **dokunulmamış test açılmayacak** ve bota hiçbir şey taşınmayacak.
Sonuç, önceden yazıldığı gibi: **"iyileşme yok, K2 ile devam."**

### 7.3 Prop firma simülasyonu — K2 (mevcut strateji)
| Kural | Geçti | İhlal (günlük / toplam) | Süre doldu | Geçenlerde medyan süre |
|---|---|---|---|---|
| günlük 5 / toplam 10 statik / hedef 10 / süresiz | %92.6 | 0 / 0 | — | 698 gün |
| hedef 8 | %93.2 | 0 / 0 | — | 402 gün |
| hedef 5 (2. aşama) | %95.3 | 0 / 0 | — | 389 gün |
| hedef 10 / 30 gün | %0 | 0 / 0 | %97.5 | — |
| hedef 10 / 60 gün | %0 | 0 / 0 | %97.5 | — |
| günlük 5 / toplam 10 takip eden / hedef 10 | %92.6 | 0 / 0 | — | 698 gün |
| günlük 4 / toplam 6 takip eden / hedef 8 | %57.0 | 0 / %36.3 | — | 402 gün |

En kötü gün −%1.16; zirveden en büyük düşüş (yüzen dahil) −%7.3. Yorum: işlem başı %0.5 riskle K2 standart
limitleri ihlal etmiyor ama hedefe ulaşması **1–2 yıl** sürüyor; süre sınırlı değerlendirmelerde (30/60 gün)
hiç geçemiyor; dar takip eden limitte (%6) üçte bir ihlal ediyor. Bu haliyle prop değerlendirmesi için
uygun bir araç değil. "Geçti" oranları geliştirme dönemine (2018–2024 kripto boğa dönemleri dahil) aittir.

### 7.4 Bu turdan çıkan dersler
- Kırılım sinyallerinde (ORB, oynaklık) yön bilgisi **var** (rastgele girişten istatistiksel olarak iyi), ama
  forex/endeks/altında büyüklüğü işlem maliyetini karşılamıyor. Sorun sinyal değil, kenarın maliyetten küçük olması.
- Çalışan tek şey hâlâ kripto günlük momentum. V1 ve T2 aynı kenarın farklı dilimleri; ikisi de K2'den
  kanıtlanabilir biçimde iyi değil.
- T2 (kazananı zaman stopu yerine takip eden stopla taşımak) umut verici ama küçük örneklemle kabul edilemez.
  Kripto için bakir geçmiş veri de kalmadığından bunu doğrulamanın tek dürüst yolu ileriye dönük kayıttır:
  bot sinyal/demo modunda K2 ile çalışırken aynı sinyallerin T2 çıkışı sanal olarak izlenebilir (bota strateji
  eklemeden, sadece raporlama tarafında). Bu ayrı bir iş ve onayınıza bağlı.

### 7.5 Sınırlamalar
- Çoklu test: bu turla birlikte 17 hipotez denendi. Bonferroni eşiği bu yüzden kullanıldı.
- Spread ×1.5 senaryolarında işlem sayısı düşüyor, çünkü botun azami spread filtresi (ATR'nin %12'si) daha
  çok sinyali eliyor; bu senaryolardaki ort R "kalan işlemlerin" ortalamasıdır.
- ORB verisi: forex için gerçek spread (FXCM m1), endeks/altın için modellenmiş spread. Broker verisi değil.
- Prop simülasyonu toplamsal (bileşik değil) ve yaygın kural tiplerini modelliyor; belirli bir firmanın
  metni değil.

### 7.6 Sonrası: gölge çıkış izlemesi (2026-11-12, onaylandı)
T2 bota strateji olarak EKLENMEDİ. Sadece `python araclar.py rapor` çıktısına bir izleme bölümü eklendi:
her `gunluk_kirilim` adayı için aynı girişin T2 çıkışıyla (girişten beri en yüksek günlük kapanış − 3 günlük
ATR, zaman stopu yok) sanal sonucu hesaplanır ve mevcut çıkışla yan yana gösterilir. Kurallar araştırma
modülüyle aynıdır (`tests/test_rapor.py` doğrular). Emir, risk ve strateji kodu değişmedi.
Karar kuralı (şimdiden): en az 40 sonuçlanmış adayda gölge çıkışın ortalama R'si mevcut çıkıştan yüksekse ve
haftalık blok bootstrap %90 aralığı sıfırın üstündeyse T2 yeniden değerlendirilir; aksi halde bırakılır.

---

## 8. K2'nin hiç bakılmamış coinlerde doğrulanması — ÖN KAYIT (2026-11-12, sonuçlardan ÖNCE yazıldı)

**Neden:** Bot, broker'ın "kripto" olarak listelediği BÜTÜN sembolleri tarar; ama K2 yalnızca 8 coinde test
edildi. Brokerda başka coinler varsa bot onlarda da işlem açacak. Ayrıca kripto için bakir geçmiş veri
kalmadığını söylemiştik; bu, yalnızca o 8 coin için doğru. Hiç bakılmamış coinler, K2 için gerçek bir
örneklem dışı testtir (kural ve parametreler donmuş durumda; hiçbir ayar yapılmayacak).

**Evren (sonuç görülmeden sabitlendi):** MT5 brokerlarında CFD olarak sık görülen 12 coin, Binance spot USDT:
BCH, LINK, DOT, AVAX, MATIC, TRX, XLM, EOS, ETC, UNI, ATOM, NEAR. Hayatta kalma yanlılığını azaltmak için
sonradan değer kaybeden / listeden çıkanlar (EOS, MATIC) bilerek dahil edildi. Hiçbir coin sonuçtan sonra
çıkarılmayacak.

**Maliyet:** spread fiyatın %0.30'u (ilk 8 coindeki %0.15'in iki katı; küçük coin CFD'leri daha pahalı),
finansman yıllık %20, temel kayma 0.25 × spread. Stres: spread ×2.

**Kural:** K2 aynen (`gunluk_kirilim`: 20 gün zirve, > SMA200, SL 2 günlük ATR, 240 mum). Tek koşu.

**Dönemler:** (a) kilit öncesi (→ 2024-07-06), (b) kilit sonrası (2024-07-06 → 2026-11). Bu coinlerde iki
dönem de bakirdir. Not: coinler birbirine yüksek korelasyonlu olduğu için bu, ilk 8 coinden tam bağımsız bir
kanıt değildir; "aynı kural başka enstrümanlarda da çalışıyor mu" sorusunu yanıtlar.

**Ölçütler (hepsi gerekir → "K2 genellenebiliyor"):**
1. İki dönemde de sembol başına ort R > 0 ve PF > 1.2.
2. Bütün dönemde rastgele girişe karşı p < 0.05.
3. Spread ×2'de bütün dönem ort R > 0.
4. 12 coinin en az 7'sinde bütün dönem ort R > 0.

**Sonuca göre eylem (şimdiden):**
- Geçerse: bot olduğu gibi kalır (zaten bütün kriptoları tarıyor); 8 + 12 coinlik portföy sonucu raporlanır.
- Kalırsa: bot varsayılan olarak yalnızca test edilmiş 8 coinle sınırlandırılır (`config.yaml`'a izin listesi);
  bu bir risk azaltma değişikliğidir, strateji değişikliği değil.

### 8.1 SONUÇ (tek koşu, 2026-11-12) — `python -m arastirma.calisma.k2_genis`, çıktı: `docs/k2_genis_ciktisi.txt`
Sembol başına, 12 yeni coin, maliyet (spread %0.30, finansman %20/yıl) + temel kayma dahil:

| Dönem | İşlem | WR | Ort R | PF | p (rastgele) | Ort R %95 aralığı |
|---|---|---|---|---|---|---|
| Kilit öncesi (→ 2024-07) | 172 | %37.8 | +0.117 | 1.23 | 0.13 | [−0.18, +0.43] |
| Kilit sonrası (2024-07 → 2026-11) | 45 | %42.2 | +0.593 | 2.19 | 0.06 | [−0.32, +1.77] |
| Bütün dönem | 217 | %38.7 | +0.216 | 1.42 | 0.036 | [−0.08, +0.57] |

| Ölçüt | Sonuç |
|---|---|
| 1. İki dönemde ort R > 0 ve PF > 1.2 | GEÇTİ (kilit öncesi sınırda: PF 1.23) |
| 2. Bütün dönem p < 0.05 | GEÇTİ (0.036) |
| 3. Spread ×2'de ort R > 0 | GEÇTİ (+0.122R; ama sadece 28 işlem — aşağıya bak) |
| 4. En az 7/12 coinde ort R > 0 | GEÇTİ (8/12; negatifler: ATOM, DOT, EOS, TRX) |

**Karar (ön kayda göre): K2 genellenebiliyor; bot olduğu gibi kalır.** Ama sonuç güçlü değil ve şöyle okunmalı:
- Dört ölçütün üçü sınırda geçti. %95 güven aralığı sıfırı içeriyor. Etki ilk 8 coindekinden küçük
  (portföyde bütün dönem: ilk 8 coin +0.39R, 12 yeni coin +0.26R).
- WR %39: hedefin çok altında. Kâr yine az sayıda büyük işlemden geliyor (XLM, ETC, NEAR, AVAX).
- Yıllara göre tutarsız: 2019, 2020, 2022, 2025 negatif; 2021, 2023, 2024, 2026 pozitif. Boğa yıllarına bağlı.
- Spread ×2 (%0.60) senaryosunda 217 işlemin 189'u botun spread filtresine (spread ≤ H1 ATR'nin %12'si)
  takılıyor. Yani broker'ınızda küçük coinlerin spread'i genişse bot onlarda zaten çok az işlem açar.
- 20 coin birlikte, en fazla 2 kripto pozisyonu kuralıyla: bütün dönem yıllık %6.0 (ilk 8 coin tek başına
  %5.8), azami düşüş %9.1 (↔ %4.3). **Evreni genişletmek getiriyi artırmıyor, düşüşü artırıyor**; çünkü
  pozisyon sayısı sınırlı ve yeni coinlerin kalitesi daha düşük.

Bunun anlamı: K2'nin dayandığı etki (kripto günlük momentum) yalnızca seçtiğimiz 8 coine özgü bir tesadüf
gibi görünmüyor; ama küçük, oynak ve piyasa rejimine bağlı. Getiri beklentisi değişmedi: yılda birkaç puan.

---

## 9. Üçüncü tur — ÖN KAYIT (2026-11-12, sonuçlardan ÖNCE yazıldı)

Amaç: getiriyi artırabilecek, K2'nin kanıtlanmış etkisine (günlük momentum) dayanan üç aday + bir risk ayarı.
Toplam hipotez sayısı bu turla 20 olur. Kurallar donduruldu; parametreler K2'den aynen alındı (yeni ayar yok).

| # | Fikir | Kural | Evren |
|---|---|---|---|
| **S1** | K2'nin satış tarafı (ayı piyasasında da işlem) | Günün ilk mumu kapanınca: dünkü günlük kapanış ≤ son 20 günün en düşük kapanışı **ve** < SMA200 → SATIŞ. SL 2 günlük ATR, 240 mum, hedef yok | Kripto (ilk 8 coin) |
| **B1** | Piyasa rejimi filtresi | K2 sinyali **ve** BTC'nin dünkü günlük kapanışı > BTC SMA200 | Kripto (ilk 8 coin) |
| **X1** | K2'nin başka sınıflarda aynısı | K2 kuralı aynen (sadece alış) | Endeksler + altın + gümüş |

Geliştirme: eğitim + doğrulama (→ 2024-07-06), önceki turlarla aynı maliyet/kayma ve ölçütler (Bölüm 4.7;
Bonferroni eşiği 0.05/3 ≈ 0.017). B1 için ek ölçüt 7 (K2'den iyi, %90 aralığı sıfırın üstünde).

Örneklem dışı kontrol (geliştirmeyi geçen adaylar için, tek sefer):
- S1, B1: Bölüm 8'deki 12 coin (bu coinlerde yalnızca K2 alış sonucu görüldü; S1/B1 için bakir) — bütün dönem.
  Ayrıca ilk 8 coinin kilitli dönemi "ikinci bakış" etiketiyle.
- X1: endeks/altın kilitli dönemi (2024-07 → 2026-11) — bu sınıflarda hiçbir sonuç görülmedi, gerçekten bakir.

Risk ayarı (hipotez değil, ödünleşim tablosu): aynı anda en fazla kripto pozisyonu 2 → 3 → 4. Getiri ve düşüş
birlikte raporlanır; karar kullanıcıya aittir, varsayılan değişmez.

Geçen aday olursa bile bota taşıma ayrı onaya bağlıdır.
