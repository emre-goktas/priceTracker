# priceTracker — Sistem Durumu, Veritabanı Analizi ve Tespit Raporu

**Tarih:** 2026-08-10  
**Rapor Türü:** Sistem Tanılama & Hata Analizi (PostgreSQL, DuckDB, MinIO, Alert Engine, Telegram)

---

## 1. Genel Özet

Sistemde yapılan inceleme sonucunda:
1. **Telegram Alert Mekanizması:** Bozuk değildir; ancak mimari olarak **günler arası (Day-over-Day)** fiyat farkı tespitine göre kurgulanmıştır. Sistem ilk kez bugün (2026-08-10) çalışmaya başladığı için henüz karşılaştırılacak bir önceki günün verisi (`rn=2`) bulunmamaktadır.
2. **Aynı Gün İçi Taramalar (Intra-day):** Günde 4 kez (09:30, 12:30, 17:30, 21:30) çalışan taramalar, `fetch_date` sadece `DATE` formatında olduğu için Postgres'te yeni bir satır eklemek yerine mevcut günün satırını güncellemektedir (`ON CONFLICT (silver_id) DO UPDATE`). Bu nedenle gün içi taramalarda ikinci bir tarih kaydı oluşmamaktadır.
3. **Eveshop Entegrasyon Hatası:** Eveshop scraping başarıyla çalışıp MinIO'ya 7.414 ürün kaydedilmiş olmasına rağmen, `matching.normalize` adımındaki bir SQL sütun eşleme hatası (`Binder Error: ean_html not found`) nedeniyle Postgres `pricing.silver_products` tablosuna aktarılamamıştır.
4. **Telegram Botu:** API bağlantısı ve bot token'ı sorunsuz çalışmaktadır.

---

## 2. Veritabanı & Katman Durumu

### 2.1. PostgreSQL (`pricing.silver_products`)
Sistemde toplam **30.928 ürün** yer almaktadır:

| Site | Toplam Ürün | Fiyatı Olan | İndirimli / Kart Fiyatı Olan | Stokta Olan | Fiyat Aralığı (TL) | Ort. Fiyat (TL) |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **Gratis** | 13.214 | 13.214 | 13.214 | 9.469 | 5.00 – 24.999 | 520.39 |
| **Rossmann** | 8.956 | 8.956 | 6.427 | 8.598 | 16.00 – 16.900 | 511.03 |
| **Watsons** | 8.758 | 8.749 | 0 | 6.657 | 4.99 – 22.799 | 416.37 |
| **Eveshop** | **0** | **0** | **0** | **0** | - | - |
| **TOPLAM** | **30.928** | **30.919** | **19.641** | **24.724** | - | - |

- `pricing.alerted_drops`: **0 satır** (Henüz eşik üstü düşüş tespit edilmediği için boş).
- `core.discovered_urls`: **44.329 URL**.
- `core.sites`: **4 aktif site**.

### 2.2. MinIO (Ham Veri Gölü - `datalake`)
- `category/gratis/`: 414 ham JSON
- `category/rossmann/`: 467 ham JSON
- `category/watsons/`: 1088 ham JSON
- `category/eveshop/`: 91 ham JSON (Toplam 7.414 ürün)

### 2.3. DuckDB (`matching/pricebot.duckdb`)
- `raw_gratis`: 39.432 satır
- `raw_rossmann`: 27.526 satır
- `raw_watsons`: 29.849 satır
- `raw_eveshop`: 22.242 satır (Deduplicated `clean_eveshop`: 7.414 satır)

---

## 3. Tespit Edilen Problemler ve Kök Sebepleri

### Problem A: Neden Hiç Alert Gönderilmedi?
**Konum:** `content/alert_engine.py` (Satır 48-73)

```sql
WITH ranked AS (
    SELECT *,
        LEAST(sale_price_try, conditional_promo_price_try) AS effective_price,
        ROW_NUMBER() OVER (
            PARTITION BY site_code, source_product_id ORDER BY fetch_date DESC
        ) AS rn
    FROM pricing.silver_products
    WHERE is_in_stock IS TRUE AND sale_price_try IS NOT NULL
)
SELECT ...
FROM ranked l
JOIN ranked p
    ON p.site_code = l.site_code AND p.source_product_id = l.source_product_id AND p.rn = 2
...
```

**Kök Sebep:**
1. **İlk Gün Kısıtı:** Sorgu, aynı ürünün en güncel tarihi (`l.rn = 1`) ile bir önceki tarihini (`p.rn = 2`) JOIN etmektedir. Veritabanındaki tüm kayıtlar sadece bugüne (`2026-08-10`) ait olduğu için tüm ürünlerde `rn = 1`'dir; `rn = 2` olan tek bir satır dahi yoktur.
2. **Gün İçi Ezilme (Overwrite):** `silver_id = md5(site_code + source_product_id + fetch_date)` formülünde `fetch_date` saat bilgisi içermediğinden, gün içindeki 4 tarama Postgres'te aynı satırın üzerine yazmaktadır (`DO UPDATE`). Dolayısıyla gün içi fiyat değişimleri ikinci bir snapshot oluşturmamaktadır.

---

### Problem B: Eveshop Neden Postgres'e Aktarılamadı?
**Konum:** `matching/analysis/build_clean.py` & `matching/normalize.py`

**Hata Kaydı (`logs/price_pipeline.log`):**
```text
ERROR   | matching.normalize | eveshop: field_mapping SQL'i çalıştırılamadı, atlanıyor - 
Binder Error: Referenced column "ean_html" not found in FROM clause!
WARNING | matching.normalize | 1 site atlandı: ['eveshop']
```

**Kök Sebep:**
1. `configs/tr/eveshop.yaml` dosyasında `ean`, `conditional_promo_price_try` ve `stock_qty` alanları `ean_html`, `eve_price_html` ve `qty_html` sütunlarına referans vermektedir.
2. Bu sütunlar `build_clean.py` içinde `eveshop_html_supplement` tablosundan LEFT JOIN ile eklenmek üzere tasarlanmıştır.
3. Ancak `eveshop_html_supplement` tablosu DuckDB'de bulunmadığında, `build_clean.py` bu sütunları `clean_eveshop` tablosuna eklememekte (NULL fallback vermemekte) ve `normalize.py` çalışırken `clean_eveshop` tablosunda `ean_html` bulunamadığı için hata fırlatmaktadır.

---

## 4. Telegram Bot API Kontrolü

**Konum:** `content/publishers/telegram.py` & `.env`

- **Bot Adı:** Aksam Pazarı
- **Bot Kullanıcı Adı:** `@cheapcheapp_bot`
- **Bot ID:** `8740296380`
- **Hedef Chat ID:** `7673353516`
- **API Yanıtı:** `HTTP 200 - OK` (Bağlantı ve kimlik doğrulama başarılı)

---

## 5. Önerilen Çözüm ve Geliştirme Adımları

1. **Eveshop Fallback Düzeltmesi (`matching/analysis/build_clean.py`):**
   - Supplement tablosu (`eveshop_html_supplement`) henüz mevcut değilse, `clean_eveshop` tablosuna bu sütunların `NULL` olarak eklenmesi (`NULL::VARCHAR[] AS ean_html`, `NULL::BIGINT AS eve_price_html`, `NULL::BIGINT AS qty_html`) sağlanmalıdır. Böylece Eveshop'un mevcut 7.414 ürünü hemen Postgres'e aktarılabilir.

2. **Gün İçi Fiyat Takibi İsteniyorsa (İsteğe Bağlı):**
   - Fiyat geçmişi takibinde `fetch_date` (Date) yerine snapshot timestamp / run ID kullanılabilir veya `pricing.price_history` tablosu eklenerek gün içindeki saatlik değişimler de alert mekanizmasına dahil edilebilir.
   - Mevcut kurguda kalınacaksa, ilk alertler yarınki ilk tarama (2026-08-11 09:30) tamamlandığında bugünün baz fiyatlarıyla karşılaştırılarak otomatik gelecektir.
