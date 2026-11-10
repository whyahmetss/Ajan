# AJAN BOT — MT5 için Çok Ajanlı Trading Botu

```
Tarayıcı (kod, ücretsiz)  →  tüm forex / endeks / kripto / emtia sembolleri, en iyi 3 aday
        ↓
Filtre ajanı (kod)        →  spread, haber, günlük zarar, pozisyon/döviz limitleri, DURDUR dosyası
        ↓
4 LLM ajanı (paralel)     →  Boğa (Claude) · Ayı (Grok, veto hakkı) · Makro (Grok) · Risk (Claude)
        ↓
Oy sayımı (kod)           →  en az 2 destek, Ayı vetosu yok
        ↓
Yargıç                    →  Claude VE Grok ayrı ayrı; ikisi de ONAY derse geçer
        ↓  (isteğe bağlı) TradingAgents derin analiz
Risk / lot (kod)          →  bakiyenin %0.5'i risk, SL = 1.5 ATR, TP = 2R
        ↓
Emir (mod: sinyal / onay / otomatik)
        ↓
İzleme                    →  1R'de stop girişe, 1.5R'den sonra ATR ile takip eden stop
```

**Altın kural:** LLM'ler sadece "evet / hayır" der. Lot, stop ve risk limitleri koddadır; hiçbir yapay zekâ bunları değiştiremez.

---

## Kurulum (Windows, bir kez)

1. **Python 3.11 veya 3.12** kur: https://www.python.org/downloads/ — kurulumda **"Add Python to PATH"** kutusunu işaretle.
2. **MT5'i** aç, demo hesabınla giriş yap.
   `Araçlar → Seçenekler → Uzman Danışmanlar → "Algoritmik işlemlere izin ver"` işaretle.
   Üstteki **Algo Trading** butonu yeşil olmalı.
3. Bu klasörde komut satırını aç (klasörün adres çubuğuna `cmd` yazıp Enter) ve:
   ```
   pip install -r requirements.txt
   ```
4. `.env.example` dosyasını kopyala, adını `.env` yap, API anahtarlarını yaz:
   - Claude: https://console.anthropic.com
   - Grok: https://console.x.ai
5. Kontroller:
   ```
   python araclar.py semboller     (broker sembolleri doğru sınıflanıyor mu?)
   python araclar.py modeller      (model isimleri doğru mu? config.yaml > llm kısmına yaz)
   ```

## Sırayla yapılacaklar (atlama!)

| Adım | Komut | Geçme şartı |
|---|---|---|
| 1. Backtest | `python araclar.py backtest` | **TEST** satırında kar_faktoru ≥ 1.2 ve ort_R ≥ 0.05 |
| 2. Sinyal modu, 1-2 hafta | config: `mod: sinyal` → `python main.py` | Kararlar mantıklı mı? `kayitlar/kararlar.csv` |
| 3. Onay modu, demo, 1 ay | `mod: onay` | Demo sonuçları backtest'e yakın mı? |
| 4. Otomatik, demo, 2-3 ay | `mod: otomatik` | Tutarlı kâr, max düşüş kabul edilebilir |
| 5. Gerçek hesap | `sadece_demo: false`, risk %0.25 ile başla | — |

Tek tarama ile hızlı deneme: `python main.py --bir-kez`

## Acil durum

- **Yeni işlem açmayı durdur:** klasöre `DURDUR` adında boş bir dosya koy. Silince devam eder.
- **Botu kapat:** Ctrl+C. Açık pozisyonların SL/TP'si broker'da kalır, korumasız kalmazlar.
- Günlük zarar %2'ye ulaşınca bot o gün kendiliğinden yeni işlem açmaz.

## Maliyet

Her aday için 6 LLM çağrısı yapılır (4 ajan + 2 yargıç). Ajanlar ucuz modelde, yargıç güçlü modelde çalışır.
H1'de saatte en fazla 3 aday olur; kodda filtreler önce çalıştığı için çoğu aday LLM'e hiç gitmez.
TradingAgents açıksa aday başına onlarca çağrı ve birkaç dakika eklenir.

## Dosyalar

```
main.py              ana döngü
araclar.py           semboller / modeller / backtest
config.yaml          TÜM ayarlar
bot/tarayici.py      strateji ve skorlama (backtest de AYNI fonksiyonu kullanır)
bot/ajanlar.py       4 LLM ajanı ve promptları
bot/karar.py         oy sayımı ve yargıç
bot/risk.py          filtre ajanı, lot hesabı, günlük limit
bot/emir.py          MT5 emirleri (emir atan tek yer)
bot/izleme.py        başa baş ve takip eden stop
bot/haber.py         ekonomik takvim
bot/derin_analiz.py  TradingAgents bağlantısı (isteğe bağlı)
bot/backtest.py      geçmiş test
kayitlar/            log, kararlar.csv, kararlar.jsonl
```

> Bu yazılım yatırım tavsiyesi değildir. Kaldıraçlı ürünlerde sermayenin tamamı kaybedilebilir.
