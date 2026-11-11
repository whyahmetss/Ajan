# AJAN BOT — MT5 için Çok Ajanlı Trading Botu

```
Tarayıcı (kod, ücretsiz)  →  tüm forex / endeks / kripto / emtia sembolleri, az önce kapanan mum, en iyi 3 aday
        ↓
Filtre ajanı (kod)        →  spread, haber, günlük zarar (açık stoplar dahil), pozisyon/döviz limitleri, DURDUR
        ↓
4 LLM ajanı (paralel)     →  Boğa (Claude) · Ayı (Grok, veto hakkı) · Makro (Grok) · Risk (Claude)
        ↓
Oy sayımı (kod)           →  en az 2 destek, Ayı vetosu yok (Ayı cevap veremezse de RED)
        ↓
Yargıç                    →  Claude VE Grok ayrı ayrı; ikisi de ONAY derse geçer
        ↓  (isteğe bağlı) TradingAgents derin analiz
Son kontrol (kod)         →  fiyat analizden 0.3R'den fazla kaçtıysa iptal; filtre ve lot baştan
        ↓
Risk / lot (kod)          →  bakiyenin %0.5'i risk, SL = 1.5 ATR, TP = 2R
        ↓
Emir (mod: sinyal / onay / otomatik)   — her emirden önce hesabın DEMO olduğu kontrol edilir
        ↓
İzleme (her mum kapanışında) →  1R'de stop girişe, 1.5R'den sonra ATR ile takip eden stop
```

**Altın kural:** LLM'ler sadece "evet / hayır" der. Lot, stop ve risk limitleri koddadır; hiçbir yapay zekâ
bunları değiştiremez (yargıç lotu ne artırabilir ne azaltabilir).

**Gerçek hesap kilidi:** Bot sadece DEMO hesapta çalışır. `sadece_demo: true` olmak zorundadır; ayrıca kodda
her emirden ve stop değişikliğinden hemen önce hesap türü kontrol edilir ve bu kontrol config ile kapatılamaz.
Bot çalışırken MT5'te gerçek hesaba geçilirse bot kendini durdurur. `araclar.py` komutları da gerçek hesapta
çalışmaz.

---

## Kurulum (Windows, bir kez)

1. **Python 3.11 veya 3.12** kur: https://www.python.org/downloads/ — kurulumda **"Add Python to PATH"** kutusunu işaretle.
2. **MT5'i** aç, demo hesabınla giriş yap.
   `Araçlar → Seçenekler → Uzman Danışmanlar → "Algoritmik işlemlere izin ver"` işaretle.
   Üstteki **Algo Trading** butonu yeşil olmalı.
   Backtest için: `Araçlar → Seçenekler → Grafikler → Grafikteki maks. çubuk` değerini yükselt (ör. 500000).
3. Bu klasörde komut satırını aç (klasörün adres çubuğuna `cmd` yazıp Enter) ve:
   ```
   pip install -r requirements.txt
   ```
4. `.env.example` dosyasını kopyala, adını `.env` yap, API anahtarlarını yaz:
   - Claude: https://console.anthropic.com
   - Grok: https://console.x.ai
   Anahtarlar sadece `.env`'de durur; loglara ve kayıt dosyalarına yazılmadan önce maskelenir.
5. Kontroller:
   ```
   python araclar.py semboller     (broker sembolleri doğru sınıflanıyor mu?)
   python araclar.py modeller      (model isimleri doğru mu? config.yaml > llm kısmına yaz)
   ```

## Testler (MT5 ve API anahtarı gerekmez, para harcamaz)

```
pip install -r requirements-dev.txt
python -m pytest
```
Testler sahte bir MetaTrader5 modülü ve sahte LLM cevaplarıyla çalışır (Linux'ta da).

## Sırayla yapılacaklar (atlama!)

| Adım | Komut | Geçme şartı |
|---|---|---|
| 1. Backtest | `python araclar.py backtest` | **TEST** satırında kar_faktoru ≥ 1.2 ve ort_R ≥ 0.05 |
| 2. Kilitli dönem | strateji ayarları BİTİNCE, bir kez: `python araclar.py backtest --kilidi-ac` | TEST'e yakın sonuç |
| 3. Sinyal modu, 1-2 hafta | `python main.py --mod sinyal` | Kararlar mantıklı mı? `kayitlar/kararlar.csv` |
| 4. Onay modu, demo, 1 ay | `mod: onay` | Demo sonuçları backtest'e yakın mı? |
| 5. Otomatik, demo, 2-3 ay | `mod: otomatik` | Tutarlı kâr, max düşüş kabul edilebilir |

Bu kurulum gerçek hesapta çalışmaz (yukarıdaki kilit).

Tek tarama ile hızlı deneme (emir atmaz): `python main.py --bir-kez --mod sinyal`

### Backtest hakkında

- Veri bir kez indirilip dosyadan test edilebilir (MT5 açık olmadan, başka bilgisayarda):
  ```
  python araclar.py veri_indir --yil 4                 (veri/ klasörüne CSV; içinde hesap bilgisi yok)
  python araclar.py veri_indir --yil 4 --tick-gun 5    (+ mum spread'ini gerçek tick spread'iyle karşılaştır)
  python araclar.py backtest --veri veri
  ```
- Bölümler takvime göredir: son %20 **KİLİTLİ** (sınırı ilk çalıştırmada `kayitlar/kilit.json`'a yazılır),
  kalan dönemin ilk %70'i eğitim, son %30'u test. Ayarları eğitimde seç, TEST'e bak, kilitli döneme en sonda
  bir kez bak (ikinci kez bakmak için `--zorla` gerekir ve kayda geçer).
- Varsayılan mod canlı portföy kurallarını uygular (max pozisyon, aynı döviz, tur başına 3 aday, günlük limit,
  min lot). `--tek` ile sembol başına bağımsız test.
- Maliyetler: spread, `backtest.komisyon_lot`, swap (sembolün bugünkü oranlarıyla). Stres testi için
  `backtest.spread_carpani: 1.5 / 2.0`.
- Dahil olmayanlar: LLM katmanı, haber filtresi (geçmiş takvim yok), marjin kontrolü.

## Acil durum

- **Yeni işlem açmayı durdur:** proje klasörüne `DURDUR` adında boş bir dosya koy. Silince devam eder.
- **Botu kapat:** Ctrl+C. Açık pozisyonların SL/TP'si broker'da kalır, korumasız kalmazlar.
- **Günlük zarar limiti:** broker sunucu gününde, kapanan işlemler + açık pozisyonların stop riski + yeni işlemin
  riski %2'yi aşacaksa bot o gün yeni işlem açmaz. Deal geçmişinden hesaplanır (bot yeniden başlatılsa da doğru).
- **MT5 bağlantısı koparsa:** bot yeni işlem açmaz, bekleyerek yeniden bağlanmayı dener.
- **Haber takvimi alınamazsa:** yeni işlem açılmaz (`haber.takvim_yoksa_islem_yok`).

## Maliyet

Her aday için 6 LLM çağrısı yapılır (4 ajan + 2 yargıç). Ajanlar ucuz modelde, yargıç güçlü modelde çalışır.
Filtreler önce çalıştığı için reddedilen adaylar LLM'e hiç gitmez; aynı mum için LLM'e ikinci kez gidilmez.
Her kararın kaydında o adayın token kullanımı ve tahmini maliyeti (`llm_maliyet_usd`) bulunur
(fiyatlar `config.yaml > llm.fiyat_usd_mtok`).
Claude 5.5 modellerinde düşünme varsayılan olarak açıktır; düşünme derinliği `llm.claude_efor` ile ayarlanır.
TradingAgents açıksa aday başına onlarca çağrı ve birkaç dakika eklenir.

## Dosyalar

```
main.py              ana döngü
araclar.py           semboller / modeller / veri_indir / backtest
config.yaml          TÜM ayarlar
bot/strateji.py      skor, üst trend, stop kuralı, lot yuvarlama (canlı ve backtest AYNI fonksiyonları kullanır)
bot/tarayici.py      tarama (az önce kapanan mum, açık piyasalar)
bot/ajanlar.py       4 LLM ajanı ve promptları
bot/karar.py         oy sayımı ve yargıç
bot/risk.py          filtre ajanı, lot hesabı, günlük limit
bot/emir.py          MT5 emirleri (emir atan tek yer, demo kilidi burada)
bot/izleme.py        başa baş ve takip eden stop (mum kapanışında)
bot/mt5_baglanti.py  bağlantı, demo kilidi, sunucu saati, kapanmış mumlar
bot/haber.py         ekonomik takvim
bot/llm.py           Claude / Grok çağrıları, hata yönetimi, token takibi
bot/kayit.py         log ve karar kayıtları (anahtar maskeleme)
bot/derin_analiz.py  TradingAgents bağlantısı (isteğe bağlı)
bot/backtest.py      geçmiş test (portföy simülasyonu, maliyetler, kilitli dönem)
tests/               otomatik testler (sahte MT5, sahte LLM)
docs/                inceleme raporları
kayitlar/            log, kararlar.csv, kararlar.jsonl, kilit.json
```

> Bu yazılım yatırım tavsiyesi değildir. Kaldıraçlı ürünlerde sermayenin tamamı kaybedilebilir.
