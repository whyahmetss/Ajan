# Aşama 1 — Kurulum ve Kod İncelemesi

Kapsam: tüm dosyalar (1572 satır). **Bot kodunda henüz hiçbir değişiklik yapılmadı.**

## Kurulum

- `.venv` (Python 3.13.18) kuruldu. `requirements.txt`'teki her şey yüklendi, **`MetaTrader5` hariç**: paket sadece Windows için yayınlanıyor, bu ortam Linux. Testler Aşama 2'de sahte MT5 modülüyle çalışacak.
- pandas 3.1.1 ile backtest uçtan uca hatasız çalıştı (`pandas>=2.0` sabitlenmediği için Windows'ta da 3.x kurulacak).

## Yöntem

Her şüpheli nokta, geçici bir sahte MT5 ile çalıştırılarak denendi. "Deney" yazan bulgular çalıştırılarak doğrulandı.
Sentetik veri sadece mekanizmayı göstermek için kullanıldı. **Kârlılık hakkında hiçbir şey söylemez**, bu yüzden sentetik performans tablosu yok.

## Doğru çalışanlar (kısaca)

- **Sinyalde lookahead bulunmadı:** göstergeler nedensel, Donchian `shift(1)` ile önceki 20 mum, giriş bir sonraki mumun açılışında, H4 trendi `merge_asof` ile H4 mumunun **kapanış** zamanına göre eşleniyor. Canlıda oluşmakta olan mum atılıyor.
- SL/TP bid/ask açısından doğru: alışta giriş ask, SL bid ile tetiklenir, kayıp tam 1R. Lot `order_calc_profit` ile hesap para birimine çevriliyor, aşağı yuvarlanıyor; min lot riski aşıyorsa işlem reddediliyor (yukarı yuvarlanmıyor). `1e-9` toleransı doğru çalışıyor.
- Backtest kötümser kuralları doğru uygulanmış: aynı mumda SL ve TP görülürse SL sayılıyor, gap'te çıkış açılıştan, alışta spread girişte, satışta çıkışta.
- Takip eden stop kodda geri gitmiyor. Yargıç hata verirse RED (fail-closed). API anahtarları sadece `.env`'den okunuyor.
- Saat dilimi: haber blackout'u UTC ile (doğru); tarama zamanlayıcısı sunucu saatinin modülüyle çalışıyor, bu H1/H4/D1 mum sınırlarıyla doğru hizalanıyor.

---

## YÜKSEK

### Y1 — Günlük %2 zarar limiti delinebiliyor · `bot/risk.py:55-72`
Gün başı bakiye, günün **ilk adayı** geldiğinde kaydediliyor. Ondan önceki kayıplar (ör. dünden kalan pozisyonların stop olması) sayılmıyor.
**Deney:** aday yokken −%1.8 kayıp → ilk adayda "günlük sonuç = 0". Sonra −%1.9 daha → gün toplamı **−%3.7, limit tetiklenmedi.**
**Öneri:** gün başı değeri ana döngüde her turda yakala (gün değiştiğinde). Yeniden başlatmaya dayanıklı olması için gerçekleşen P/L'yi deal geçmişinden de doğrula.

### Y2 — Gerçek hesap kilidi sadece açılışta · `bot/mt5_baglanti.py:36`, `bot/emir.py`, `araclar.py:25,58`
Demo kontrolü sadece `baglan()` içinde. Bot çalışırken MT5 terminalinde başka (gerçek) bir hesaba geçilirse emirler o hesaba gider. `emir.py`'de config'den bağımsız bir kilit yok. `araclar.py semboller/backtest` kilidi bilerek atlıyor (`sadece_demo=False`). Sadece okuma yapıyor, ama kuralınız "gerçek hesapta hiçbir şey çalışmayacak".
**Öneri:** her `order_send` öncesinde (emir ve stop güncelleme) `account_info().trade_mode == DEMO` zorunlu olsun. Bu sabit kod olur, config ile kapatılamaz. Ayrıca her döngü turunda kontrol edilsin ve araclar.py de kilide uysun.

### Y3 — Fiyat kayması iptali fiilen çalışmıyor · `main.py:74,94`, `bot/emir.py:28-29`
emir.py "LLM'ler düşünürken fiyat kaydıysa iptal" diyor. Ama main.py emirden hemen önce planı yeni fiyatla tazeliyor. Bu yüzden kayma, LLM'lerin gördüğü plana göre değil, milisaniyeler önceki plana göre ölçülüyor.
**Deney:** LLM planı 1.1001, emir anında 1.1013 (**0.80R kayma**) → emir **açıldı**. Aynı kontrol LLM planıyla yapılsaydı iptal olurdu.
(Risk R cinsinden aynı kalıyor çünkü SL/TP kaydırılıyor. Sorun şu: açılan işlem, analiz edilen işlem değil.)
**Öneri:** LLM'e gösterilen planı sakla, kaymayı ona göre ölç.

### Y4 — Onay modunda "e" dendikten sonra kontrol yok, beklerken izleme duruyor · `main.py:89-96`
`input()` süresiz bekliyor. Bu sürede (dakikalar ya da saatler) DURDUR dosyası, günlük limit, haber blackout'u, pozisyon limitleri ve lot **yeniden kontrol edilmiyor**. Kayma kontrolü de tazelenmiş plana göre yapıldığı için (Y3) saatler sonra bile emir açılır. Bot tek iş parçacıklı olduğu için beklerken açık pozisyonların başa baş ve takip eden stopu da yönetilmiyor.
**Öneri:** onaydan sonra plan, filtre ve lot yeniden hesaplansın. Onaya zaman aşımı eklensin (ör. 120 sn, sonra RED).

### Y5 — Ayı ajanı hata verirse veto sessizce kalkıyor · `bot/ajanlar.py:77-80`
"Hata = NOTR" kuralı destek ajanları için güvenli, ama veto yetkili Ayı için tersine çalışıyor. Ayı'nın çağrısı başarısız olursa ya da bozuk JSON dönerse işlem 2 destek oyuyla geçer.
**Deney:** Ayı → "grok yanıt vermedi" → NOTR → `oy_sayimi = (True, '2 destek oyu')`.
**Öneri:** veto yetkili ajan hata verirse aday RED.

### Y6 — Backtest max düşüşü yanlış sırayla hesaplanıyor · `bot/backtest.py:114,153`
R listesi önce sembole, sonra zamana göre birleştiriliyor (önce tüm EURUSD işlemleri, sonra tüm GBPUSD işlemleri...). Düşüş zaman sırasıyla hesaplanmalı.
**Deney:** aynı 12 işlem, sembol sırasıyla DD = **6R**, zaman sırasıyla DD = **2R**.
**Öneri:** çıkış zamanına göre sırala, eşzamanlı pozisyonları hesaba kat.

### Y7 — min_skor eşikleri gerçekte simüle edilmiyor · `bot/backtest.py:128-151`
Tüm eşikler, en düşük eşikle (50) simüle edilen işlemlerden süzülüyor. "Sembol başına tek pozisyon" kuralı yüzünden 60 eşiğinin gerçek işlem dizisi farklı: 50'lik bir işlem açıkken gelen 60+ sinyaller atlanmış oluyor.
**Deney** (sentetik, tek sembol): 60 eşiği için süzülmüş liste **876**, gerçek simülasyon **996** işlem (%12 eksik); toplam R 355 ile 410. Yani canlıdaki ayar (60) aslında hiç test edilmemiş.
**Öneri:** her eşik ayrı simüle edilsin.

### Y8 — Eğitim/test ayrımı takvime göre değil · `bot/backtest.py:105,142`
Her sembol kendi mum sırasına göre %70/%30 bölünüyor. 20000 H1 mum forex'te ≈ 3.19 yıl (test = son 11.5 ay), kriptoda ≈ 2.28 yıl (test = son 8.2 ay). Kriptonun "eğitim" dönemi forex'in "test" dönemiyle yaklaşık 3 ay çakışıyor, ortak piyasa rejimi bilgisi teste sızıyor. Ayrıca 20000 mum kripto için 3 yıl şartını karşılamıyor. BENIOKU'daki "eşikleri TEST satırlarına bakarak seç" akışı da test setini eğitim gibi kullanıyor.
**Öneri:** ortak takvim tarihleriyle bölme. Aşama 3'teki kilitli son %20 de takvim bazlı olsun ve en sonda **bir kez** bakılsın.

---

## ORTA

- **O1 — Canlı izleme ile backtest farklı kurallar uyguluyor** · `bot/izleme.py:29-36` / `bot/backtest.py:59-63,70-74`
  Canlıda başa baş ve trailing 20 sn'de bir **anlık** fiyatla tetikleniyor (mum içi tepe dahil); backtest'te mum **kapanışıyla**. Canlıda başa baş çok daha sık tetiklenir, trailing daha sıkı olur. Yani backtest canlıyı temsil etmiyor. Ayrıca stop her 1 point'lik iyileşmede değiştirilebiliyor; broker'ın "çok sık istek" kısıtlamasına takılma riski var. Etkisi Aşama 3'te M1 verisiyle ölçülecek.
- **O2 — Backtest maliyetleri eksik ve muhtemelen iyimser.** Komisyon ve swap yok. MT5 mum `spread` alanı (mum içi min/son değer, broker'a bağlı) giriş anındaki gerçek spread'den küçük olabilir; ayrıca giriş mumunun kendi spread'i kullanılıyor. Aşama 3'te tick verisiyle doğrulanacak.
- **O3 — Backtest portföy kurallarını içermiyor:** max 4 pozisyon, aynı döviz max 2, tur başına max 3 aday (skor sırasıyla), günlük limit, haber filtresi, min lot uygunluğu, "spread > stop'un %25'i" kuralı. Sonuçlar canlıda alınamayacak işlemleri de içeriyor. Aşama 3'te kronolojik portföy simülasyonu önerilir.
- **O4 — Seans boşlukları** · `bot/mt5_baglanti.py:88`
  (a) `mumlar()` son mumu koşulsuz atıyor. Yeni mum henüz başlamadıysa (piyasa kapalı ya da ilk tick gelmedi) kapanmış son mum atılır ve bir önceki mum **tekrar** analiz edilir. Aynı aday için LLM'e iki kez para ödenebilir; piyasa kapanışından sonraki ilk taramada aday LLM'e sorulur, emir ise "market closed" ile reddedilir.
  (b) Backtest, Cuma'nın son mumundaki sinyale Pazartesi açılışında (gap) giriyor. Canlı bunu yapamaz.
- **O5 — MT5 bağlantısı koparsa** · `bot/risk.py:40`, `main.py:164-166`
  `positions_get()` hata verip None döndüğünde "pozisyon yok" sayılıyor, max pozisyon ve döviz limitleri fail-open oluyor (deneyle doğrulandı). Yeniden bağlanma başarısız olursa `SystemExit` fırlıyor ve bot tamamen kapanıyor, izleme de duruyor. `terminal_info()` kontrolü broker bağlantısına (`.connected`) bakmıyor.
- **O6 — Haber filtresi fail-open** · `bot/haber.py:55-57`
  Takvim alınamazsa filtre sessizce devre dışı kalıyor. Ağ hatasında geçen haftanın önbelleği de kullanılabiliyor. Öneri: güncel takvim yoksa yeni işlem açılmasın (config ile seçilebilir).
- **O7 — Lot adımı yuvarlaması** · `bot/risk.py:133-134`
  Ondalık hane sayısı log10'dan hesaplanıyor; adım 0.25 için tek hane çıkıyor, 0.75 lot 0.8'e yuvarlanıyor.
  **Deney:** izin verilen risk 50.00, gerçek risk **52.00**; 115'e karşı **117**. Broker muhtemelen "geçersiz hacim" der, ama risk kuralını bozuyor.
- **O8 — Fiyatlar `digits`'e yuvarlanıyor, `trade_tick_size`'a değil** · `bot/risk.py:33-34`, `bot/emir.py:34-35`, `bot/izleme.py`
  Tick size'ı 0.25/0.5/1 olan endekslerde "Invalid stops" reddi alınır.
- **O9 — Sembol sınıflandırması kırılgan** · `config.yaml:26`, `bot/mt5_baglanti.py:42-57`
  "major/minor/cross" kelimeleri "Indices\Major", "Crypto\Majors" gibi yolları forex'e düşürür (forex ilk sırada kontrol ediliyor). Yol eşleşmezse 6 harfle başlayan her isim (ör. "NVIDIA") forex sayılır. Bunun sonucunda döviz ve haber eşleşmesi yanlış olur. Aşama 3'te `araclar.py semboller` çıktısına göre düzeltilecek.
- **O10 — Hafta sonu kripto taraması durabilir** · `main.py:149-158`
  Zamanlayıcı sunucu saatini sadece listedeki ilk 25 sembolün tick'inden alıyor. Bunlar forex ise hafta sonu saat ilerlemez ve kripto hiç taranmaz. Backtest ise hafta sonu kripto işlemlerini içeriyor.
- **O11 — Karar kayıtları Aşama 5 analizine yetmiyor** · `main.py`, `bot/kayit.py`
  RED kayıtlarında plan (giriş/SL/TP/ATR), mum zamanı ve UTC zaman yok. Bu yüzden "LLM'in reddettiği işlem sonra stop mu oldu, hedefe mi gitti?" ölçülemez.

## DÜŞÜK

- **D1** — Canlı tarayıcı EMA200'ü 300 mumla hesaplıyor, backtest ~20000 mumla; canlıda ilk mumun ağırlığı ~%5 kalıyor. **Deney** (sentetik, 3×2000 mum): adayların %0-3'ü farklı, H4 trendi mumların %1-4'ünde farklı. Öneri: `tarama_bar: 1000`.
- **D2** — RSI hiç düşüş yoksa 100 yerine 50 veriyor (`indikator.py:14-15`, deneyle doğrulandı). Pratikte sadece serinin başında olur.
- **D3** — `gerekceler` metin olarak gelirse harflere bölünüyor: `['T','r','e','n']` (`ajanlar.py:76`, deneyle doğrulandı).
- **D4** — `order_send` sadece DONE'ı başarı sayıyor. Kısmi dolum (IOC) olursa pozisyon açık olduğu halde "HATA" yazılır (`emir.py:49`).
- **D5** — `deviation` tüm sembollerde sabit 20 point (JPY'de 2 pip, BTC'de 0.20$).
- **D6** — CLOSEONLY/LONGONLY/SHORTONLY semboller elenmiyor (`mt5_baglanti.py:71`).
- **D7** — LLM'e giden mum saatleri broker saatinde ama etiketsiz. Ayı ajanından "düşük likidite saatleri"ni değerlendirmesi isteniyor; saatleri 2-3 saat kaydırarak yorumlayabilir. Öneri: UTC gönder.
- **D8** — Günlük limitin gün sınırı bilgisayarın yerel gece yarısı, broker'ınki değil. Belgelenmeli.
- **D9** — `DURDUR` ve `gun_basi.json` yolları çalışma klasörüne göre çözülüyor (`risk.py:17,52`). `--config` ile başka bir klasörden çalıştırılırsa acil durdurma dosyası görülmez.
- **D10** — LLM kalıcı hatalarda da (401, yanlış model adı) 3 kez deniyor. Hata metinleri `kararlar.jsonl`'a yazılıyor ve SDK mesajları maskelenmiş anahtar parçası içerebilir. Öneri: loglara anahtar maskeleme filtresi.
- **D11 — DOĞRULANMADI:** grok-4 reasoning modeli. Düşünme tokenları `max_token: 900`'ü tüketirse boş cevap gelir; Grok yargıcı her şeye RED der ve bot bunu sadece uyarı olarak loglar. Aşama 5'te ölçülecek.
- **D12** — Backtest başa başta (+spread) kapanan işlemleri "kazanç" sayıyor, kazanma % şişiyor. Raporlarda ayrı gösterilecek.
- **D13** — Backtest 220. mumdan başlıyor; EMA200 bu noktada henüz oturmamış.
- **D14** — Backtest yavaş: 1 sembol × 20000 mum ≈ 3.4 sn. Walk-forward ve duyarlılık testleri için hızlandırma gerekecek. Sonuçlar orijinal fonksiyonla birebir karşılaştırılarak test edilecek.
- **D15** — Freeze level kontrol edilmiyor. Her taramada tüm semboller Market Watch'a ekleniyor (performans).

---

## Tasarım soruları (hata değil, karar sizin)

- **S1** — Yargıç "YARIM" diyerek lotu yarıya indirebiliyor. LLM riski artıramıyor, sadece azaltabiliyor. Bunu "risk LLM'den bağımsız" kuralınıza uygun sayıyor musunuz, yoksa kaldırılsın mı?
- **S2** — Günlük limit sadece **yeni** işlemi durduruyor, açık pozisyonların riskini saymıyor. −%1.9'dayken 4 açık pozisyonla (her biri %0.5) gün −%3.9'a kadar gidebilir. "Açık risk dahil %2" olarak sıkılaştırılsın mı?
- **S3** — `max_ayni_doviz: 2`, ve kripto ile ABD endeksleri USD sayıldığı için pratikte aynı anda en fazla ~2 USD'li pozisyon açılabiliyor. Bu bilinçli mi?
- **S4** — O1 için iki yol var. (a) Canlı izleme mum kapanışında çalışır: backtest ile birebir uyum, basit. (b) Canlı aynı kalır, backtest M1 verisiyle mum içi simüle edilir. Benim önerim (a); Aşama 3'te iki varyantı da M1 ile ölçerim.

## Bu aşamada değişen dosyalar

- `.gitignore`: `.venv/` ve `kayitlar/` eklendi. Önceki commit'te yanlışlıkla `logs/` yazmıştım; botun klasörü `kayitlar/` ve `bot.log` hesap numarasını içeriyor.
- `docs/ASAMA1_INCELEME.md` (bu dosya).
- Bot kodunda değişiklik **yok**.

## Açık riskler ve emin olmadığım noktalar

- MT5 mum `spread` alanının tam anlamı (O2): broker verisiyle doğrulanacak.
- Grok `max_token` sorunu (D11): gerçek API ile doğrulanacak.
- Sentetik deneyler mekanizmayı gösterir; gerçek verideki büyüklük farklı olabilir (özellikle O1 ve D1).

## Aşama 3 ve 5 için engel

Bu oturum bir Linux bulut container'ında çalışıyor. MT5 terminali burada çalışmaz ve bilgisayarınızdaki MT5'e erişimim yok (paylaşılmış klasör de görünmüyor). Seçenekler:

1. Claude masaüstü uygulamasında Remote Control ile bot klasörünü bu oturuma açmak (Ayarlar → Claude Code → Remote Control). Bu özellik herkese açık olmayabilir.
2. Bir veri dışa aktarma komutu (`python araclar.py veri_indir`) yazarım. Windows'ta çalıştırırsınız (demo hesapta, sadece okuma), çıkan CSV dosyalarını buraya yüklersiniz, backtest'i burada yaparım. Aşama 5'teki canlı LLM denemesi ise sizin makinenizde çalışır.
3. Claude Code'u doğrudan Windows'ta çalıştırmak.
