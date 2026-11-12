# AJAN BOT — MT5 için Çok Ajanlı Trading Botu

> **Önce bunu oku.** Bu bot bir "para basma makinesi" değildir ve öyle bir şey yoktur.
> 2015-2026 gerçek piyasa verisiyle 11 strateji fikri test edildi (`docs/ARASTIRMA_GUNLUGU.md`):
> - Bu projenin ilk stratejisi (`trend_skor`) **her yıl zarar etti**.
> - Kazanma oranı %60+ olan 7 fikrin **hepsi zarar etti** (o kazanma oranı rastgele girişte de çıkıyor).
> - Sadece biri, **kripto günlük kırılım momentumu**, hiç görülmemiş son dönemde de kâr etti.
>   Varsayılan strateji budur. Kazanma oranı **~%45-50**, getirisi **küçük** (aşağıda).
>
> Bot sadece DEMO hesapta çalışır. Aşağıdaki sonuçlar geçmişe aittir, geleceği garanti etmez.

## Strateji: `gunluk_kirilim` (kripto günlük kırılım momentumu)

- **Sinyal:** broker sunucu gününün ilk H1 mumu kapandığında, dünkü günlük kapanış hem 200 günlük
  ortalamanın üstünde hem de son 20 günün en yüksek kapanışı ise **alış**. Sadece kripto, sadece alış.
- **Çıkış:** stop = giriş − 2 × günlük ATR. Hedef **yok**. 240 H1 mumu (10 gün) sonra pozisyon piyasadan
  kapatılır. Başa baş / takip eden stop yok.
- **Risk:** işlem başı bakiyenin %0.5'i; aynı anda en fazla 2 kripto pozisyonu; günlük en fazla %2 zarar.

### Geçmiş sonuçlar (gerçek fiyatlar, modellenmiş CFD maliyeti, işlem başı %0.5 risk)

| Dönem | İşlem | Kazanma | Ort. R | Kâr faktörü | Yıllık getiri | En büyük düşüş |
|---|---|---|---|---|---|---|
| Eğitim (2018-03 → 2021-09, iki büyük boğa dönemi içerir) | 114 | %50.9 | +0.59 | 2.62 | ~%9.5 | %3.5 |
| TEST (2021-09 → 2024-07) | 85 | %45.9 | +0.24 | 1.61 | ~%3.6 | %4.2 |
| **KİLİTLİ (2024-07 → 2026-11), bir kez bakıldı** | 55 | %47.3 | +0.21 | 1.50 | **~%2.5** | %4.0 |

Canlı portföy kurallarıyla, büyük hesap varsayımıyla. Rastgele girişe karşı kilitli dönemde p = 0.019.

**Bilmen gerekenler:**
1. Kazanma oranı %50'nin altında. İşlemlerin yarısından fazlası stop olur; kâr az sayıda büyük hareketten gelir.
2. Kilitli dönemdeki kârın tamamı 2024 sonundaki ralliden. 2025 ve 2026'da hafif zararda. Strateji güçlü
   boğa dönemlerinde kazanır, diğer zamanlarda az az kaybeder. Aylarca kâr etmeyebilir.
3. Getiri küçük: bu risk ayarlarıyla yılda ~%2.5-5. Riski artırmak getiriyi de düşüşü de aynı oranda büyütür.
4. Küçük hesapta bazı işlemler açılamaz: 10.000 USD hesapta BTC'nin 0.01 lotu bile %0.5 riskten büyük
   olabilir (bot bunu "Min lot bile riskten fazla" diye reddeder). ~25.000 USD ve üstü demo hesap önerilir.
5. Sinyal günde bir kez, sunucu saatiyle gece 01:00 civarında değerlendirilir. **Bot o saatte çalışmıyorsa
   o günün işlemi kaçar.** Bot 7/24 açık bir bilgisayarda çalışmalıdır.
6. Araştırma verisi senin broker'ının verisi değil (Binance fiyatları + varsayılan CFD spread/finansman).
   Broker'ının kripto spread'i ve gecelik finansmanı yüksekse kenar küçülür.

## Akış

```
Tarayıcı (kod, ücretsiz)  →  aktif stratejinin sinyali; az önce kapanan mum; açık piyasalar
        ↓
Filtre ajanı (kod)        →  spread, haber, günlük zarar (açık stoplar dahil), pozisyon/döviz limitleri, DURDUR
        ↓
4 LLM ajanı + yargıç      →  mod: gozlem (kaydeder, ENGELLEMEZ) | veto (reddederse işlem yok) | kapali
        ↓
Son kontrol (kod)         →  fiyat analizden 0.3R'den fazla kaçtıysa iptal; filtre ve lot baştan
        ↓
Risk / lot (kod)          →  bakiyenin %0.5'i risk; lot aşağı yuvarlanır; LLM lotu değiştiremez
        ↓
Emir (mod: sinyal / onay / otomatik)   — her emirden önce hesabın DEMO olduğu kontrol edilir
        ↓
İzleme (her mum kapanışında) →  zaman stopu (gunluk_kirilim) ya da başa baş + takip eden stop (trend_skor)
```

**LLM katmanı neden "gözlem" modunda?** Doğrulanan şey kural tabanlı stratejidir; LLM'lerin katkısı
ölçülmedi. Ayrıca bu stratejinin kazancı büyük hareketlerden gelir ve "aşırı uzamış hareket" diyen bir
veto tam da bunları silebilir. Gözlem modunda ajanlar ve yargıçlar oy verir, hepsi kaydedilir, ama işlemi
engellemez. Yeterli kayıt birikince `python araclar.py rapor` ile katkıları ölçülür; değer katıyorlarsa
`llm.mod: veto` yapılır. API anahtarı kullanmak istemiyorsan `llm.mod: kapali`.

**Gerçek hesap kilidi:** Bot sadece DEMO hesapta çalışır. `sadece_demo: true` olmak zorundadır; ayrıca kodda
her emirden, stop değişikliğinden ve pozisyon kapatmadan hemen önce hesap türü kontrol edilir ve bu kontrol
config ile kapatılamaz. Bot çalışırken MT5'te gerçek hesaba geçilirse bot kendini durdurur.

---

## Kurulum (Windows, bir kez)

1. **Python 3.11 veya 3.12** kur: https://www.python.org/downloads/ — kurulumda **"Add Python to PATH"** kutusunu işaretle.
2. **MT5'i** aç, demo hesabınla giriş yap.
   `Araçlar → Seçenekler → Uzman Danışmanlar → "Algoritmik işlemlere izin ver"` işaretle.
   Üstteki **Algo Trading** butonu yeşil olmalı.
   `Araçlar → Seçenekler → Grafikler → Grafikteki maks. çubuk` değerini yükselt (ör. 500000).
3. Bu klasörde komut satırını aç (klasörün adres çubuğuna `cmd` yazıp Enter) ve:
   ```
   pip install -r requirements.txt
   ```
4. (LLM kullanacaksan) `.env.example` dosyasını kopyala, adını `.env` yap, API anahtarlarını yaz.
   Anahtarlar sadece `.env`'de durur; loglara ve kayıt dosyalarına yazılmadan önce maskelenir.
5. Kontrol:
   ```
   python araclar.py semboller     (kripto sembolleri "kripto" olarak sınıflanıyor mu?)
   python araclar.py modeller      (LLM kullanacaksan: model isimleri doğru mu?)
   ```

## Sırayla yapılacaklar

| Adım | Komut | Ne için |
|---|---|---|
| 1. Tek tarama | `python main.py --bir-kez --mod sinyal` | Bot bağlanıyor, sembolleri görüyor mu? (emir atmaz) |
| 2. Sinyal modu, 4+ hafta | `python main.py --mod sinyal` | Sinyaller kaydedilir, emir atılmaz |
| 3. Rapor | `python araclar.py rapor` | Sinyallerin sanal sonucu; ajanların isabeti; LLM değer katıyor mu? |
| 4. Otomatik, demo, 6+ ay | config: `mod: otomatik` | Demo sonuçları geçmiş sonuçlara benziyor mu? |

Strateji yılda ~30-40 işlem açar; bir-iki haftada sonuç görmeyi bekleme. Gerçek hesap bu kurulumda kilitlidir.

Rapor ayrıca bir **gölge çıkış izlemesi** gösterir: aynı girişler, 10 günlük zaman stopu yerine günlük kapanışa
göre takip eden stopla (3 günlük ATR) kapatılsaydı ne olurdu? Bot bu çıkışı kullanmaz; geçmiş veride umut
verici ama kanıtı yetersiz bulunan bir fikrin (T2) ileriye dönük kaydıdır. Ayrıntı: `docs/ARASTIRMA_RAPORU_2.md`.

İkinci araştırma turunda (ORB, hacim onayı, oynaklık kırılımı, takip eden çıkış, bant dönüşü) altı adayın
altısı da önceden sabitlenen ölçütlerde elendi; strateji değişmedi. Yeniden üretmek için:
`python -m arastirma.veri_m15` ve `python -m arastirma.deney2 O1 O2 V1 T1 T2 M1`.

### İsteğe bağlı: kendi broker verinle backtest

```
python araclar.py veri_indir --yil 4 --tick-gun 5    (veri/ klasörüne CSV; içinde hesap bilgisi yok)
python araclar.py backtest --veri veri               (canlı portföy kuralları)
python araclar.py backtest --veri veri --tek         (sembol başına)
```
Bu, broker'ının spread ve verisiyle bir **maliyet kontrolüdür**; yeni bir "görülmemiş dönem" kanıtı değildir
(aynı dönem araştırmada zaten kullanıldı). `config.yaml > backtest.komisyon_lot` değerini broker'ına göre doldur.

## Testler (MT5 ve API anahtarı gerekmez, para harcamaz)

```
pip install -r requirements-dev.txt
python -m pytest
```
Sahte bir MetaTrader5 modülü ve sahte LLM cevaplarıyla çalışır (Linux'ta da). Canlı tarayıcı ile backtest'in
aynı sinyali ve aynı stopu ürettiği, zaman stopunun aynı mumda çalıştığı testlerle doğrulanır.

## Araştırmayı yeniden üretmek

```
pip install -r requirements-arastirma.txt
python -m arastirma.veri_topla            (halka açık kaynaklardan gerçek veri: FXCM, HistData, Binance)
python -m arastirma.deney H0              (ilk strateji: gerçek veride sonuç)
python -m arastirma.deney K2              (seçilen strateji: geliştirme dönemi raporu)
python -m arastirma.olay                  (olay incelemesi)
```
Kilitli dönem bir kez açıldı; kaydı `docs/kilit_kaydi.json`, çıktısı `docs/kilitli_donem_ciktisi.txt`.

## Acil durum

- **Yeni işlem açmayı durdur:** proje klasörüne `DURDUR` adında boş bir dosya koy. Silince devam eder.
- **Botu kapat:** Ctrl+C. Açık pozisyonların stopu broker'da kalır. **Dikkat:** `gunluk_kirilim` pozisyonlarının
  hedefi yoktur ve 10 günlük zaman stopunu bot uygular; bot kapalıyken pozisyon sadece stopla korunur.
- **Günlük zarar limiti:** broker sunucu gününde, kapanan işlemler + açık pozisyonların stop riski + yeni işlemin
  riski %2'yi aşacaksa bot o gün yeni işlem açmaz.
- **MT5 bağlantısı koparsa:** bot yeni işlem açmaz, bekleyerek yeniden bağlanmayı dener.
- **Haber takvimi alınamazsa:** yeni işlem açılmaz (`haber.takvim_yoksa_islem_yok`).

## Maliyet (LLM)

Gözlem ya da veto modunda aday başına 6 LLM çağrısı (4 ajan + 2 yargıç). Strateji yılda ~30-40 aday
ürettiği için toplam maliyet çok düşüktür. Her kaydın içinde o adayın token kullanımı ve tahmini maliyeti
(`llm_maliyet_usd`) vardır; `python araclar.py rapor` toplamı gösterir.

## Dosyalar

```
main.py              ana döngü
araclar.py           semboller / modeller / veri_indir / backtest / rapor
config.yaml          TÜM ayarlar
bot/strateji.py      strateji kuralları (canlı ve backtest AYNI fonksiyonları kullanır)
bot/tarayici.py      tarama (az önce kapanan mum, açık piyasalar)
bot/risk.py          filtre ajanı, plan, lot hesabı, günlük limit
bot/emir.py          MT5 emirleri (emir atan tek yer, demo kilidi burada)
bot/izleme.py        zaman stopu / başa baş / takip eden stop (mum kapanışında)
bot/ajanlar.py       4 LLM ajanı ve promptları
bot/karar.py         oy sayımı ve yargıç
bot/llm.py           Claude / Grok çağrıları, hata yönetimi, token takibi
bot/rapor.py         karar kayıtlarından rapor
bot/backtest.py      geçmiş test (portföy simülasyonu, maliyetler, kilitli dönem)
bot/mt5_baglanti.py  bağlantı, demo kilidi, sunucu saati, kapanmış mumlar
bot/haber.py         ekonomik takvim
bot/kayit.py         log ve karar kayıtları (anahtar maskeleme)
bot/derin_analiz.py  TradingAgents bağlantısı (isteğe bağlı, sadece veto modunda)
arastirma/           araştırma kodu (veri toplama, hipotez testleri); bot bunu kullanmaz
tests/               otomatik testler (sahte MT5, sahte LLM)
docs/                inceleme raporu, araştırma günlüğü, kilitli dönem kaydı
kayitlar/            log, kararlar.csv, kararlar.jsonl, raporlar
```

> Bu yazılım yatırım tavsiyesi değildir. Kaldıraçlı ürünlerde sermayenin tamamı kaybedilebilir.
