# 🏗️ Silver Layer Katmanı - Adım Adım Normalizasyon ve Temizlik Planı

**Proje:** PriceTracker (TR Kozmetik Fiyat Takip Sistemi)  
**Tarih:** 31 Temmuz 2026  
**Hedef Veritabanı / Tablo:** `matching/pricebot.duckdb` ➔ `silver_products`  
**Kaynak Tablolar:** `clean_gratis`, `clean_rossmann`, `clean_watsons`, `clean_eveshop`

---

## 🎯 1. Mimari Hedef ve Silver Katmanının Rolü

Silver katmanı, heterojen e-ticaret sitelerinden toplanan ham/clean verileri **tek bir kanonik veri modeline (Canonical Data Model)** dönüştüren ELT (Extract-Load-Transform) katmanıdır.

Bu katmanın temel amaçları:
1. **Fiyat Birliğini Sağlamak:** Kuruş ve TL cinsinden gelen fiyatları standart `DECIMAL(10,2)` TL formatına getirmek.
2. **Barkod (EAN/GTIN) Temizliği:** EAN eşleştirmesine (EAN Match) hazır, doğrulanmış 8-14 haneli sayısal barkodlar üretmek.
3. **Marka Standardizasyonu:** Büyük/küçük harf ve imla farklarını giderip ortak marka isimleri oluşturmak.
4. **Metin & Hacim Ayıklama:** Ürün isimlerinden HTML kalıntılarını temizlemek; `150 ml`, `50 gr`, `2 adet` gibi birim ve miktar bilgilerini regex ile ayırmak.
5. **Stok Mantığını Bire İndirmek:** Metin veya sayısal stok verilerini standart `BOOLEAN` (`True`/`False`) stok bayrağına dönüştürmek.

---

## 📐 2. Kanonik Tablo Şeması (`silver_products`)

Silver katmanında oluşturulacak hedef `silver_products` DuckDB tablosunun DDL tasarımı:

```sql
CREATE TABLE IF NOT EXISTS silver_products (
    -- Sistem & İzsürülebilirlik
    silver_id           VARCHAR PRIMARY KEY,   -- hash(site_code || '_' || source_product_id || '_' || fetch_date)
    site_code           VARCHAR NOT NULL,      -- 'gratis', 'rossmann', 'watsons', 'eveshop'
    source_product_id   VARCHAR NOT NULL,      -- Kaynak sitedeki orijinal ürün ID
    source_sku          VARCHAR,               -- Kaynak SKU
    fetch_date          DATE NOT NULL,         -- Veri çekim tarihi
    
    -- Temel Ürün Kimlikleri
    raw_barcode         VARCHAR,               -- Kaynaktan gelen ham barkod
    clean_barcode       VARCHAR,               -- Temizlenmiş, doğrulanmış EAN-13 (EAN Match girdisi)
    is_valid_barcode    BOOLEAN,               -- Barkod EAN standartlarına uygun mu?
    
    -- Ürün İsim & Marka
    raw_product_name    VARCHAR NOT NULL,      -- Orijinal başlık
    clean_product_name  VARCHAR NOT NULL,      -- Temizlenmiş başlık (HTML, fazla boşluk süzülmüş)
    raw_brand_name      VARCHAR,               -- Orijinal marka
    clean_brand_name    VARCHAR,               -- Standardize edilmiş marka adı (Title Case)
    
    -- Hacim & Birim Ayıklama
    extracted_amount    DOUBLE,                -- Çıkarılan sayısal miktar (Örn: 150.0)
    extracted_unit      VARCHAR,               -- Çıkarılan birim (Örn: 'ml', 'g', 'adet')
    
    -- Fiyat Bilgileri (TL Cinsinden)
    list_price_try      DECIMAL(10, 2),        -- Liste / Normal fiyat
    sale_price_try      DECIMAL(10, 2) NOT NULL, -- Güncel indirimli satış fiyatı
    discount_rate       DOUBLE,                -- İndirim yüzdesi (%)
    currency            VARCHAR DEFAULT 'TRY', -- Para birimi
    
    -- Stok & Envanter
    is_in_stock         BOOLEAN NOT NULL,      -- Stok mevcudiyeti (True/False)
    stock_qty           INTEGER,               -- Varsa sayısal stok adedi
    
    -- Kategori & Bağlantılar
    category_path       VARCHAR,               -- Kategori hiyerarşi dizgisi
    category_level_1    VARCHAR,               -- Ana kategori (Örn: 'Makyaj')
    category_level_2    VARCHAR,               -- Alt kategori (Örn: 'Dudak')
    product_url         VARCHAR,               -- Ürün web adresi
    image_url           VARCHAR,               -- Ürün görsel bağlantısı
    
    -- Yükleme Zamanı
    created_at          TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

---

## 🛠️ 3. Adım Adım Normalizasyon ve Temizlik Kuralları

### Adım 1: Fiyat ve Para Birimi Normalizasyonu (Price Scale & Currency)

Her kaynak sitenin fiyat birimi ve alanı farklıdır:

1. **Gratis (`clean_gratis`)**:
   - `sale_price_try` = `prices.discountedPrice / 100.0`
   - `list_price_try` = `prices.normalPrice / 100.0`
   - `discount_rate` = `prices.discountRate`
2. **Eve Shop (`clean_eveshop`)**:
   - `sale_price_try` = `CASE WHEN eve_price > 0 THEN eve_price / 100.0 ELSE price / 100.0 END`
   - `list_price_try` = `price / 100.0`
   - `discount_rate` = `ROUND((1.0 - (sale_price_try / NULLIF(list_price_try, 0))) * 100, 2)`
3. **Rossmann (`clean_rossmann`)**:
   - `sale_price_try` = `COALESCE(_source.special_price, _source.price)` (TL formatında)
   - `list_price_try` = `_source.price` (TL formatında)
   - `discount_rate` = `_source.discp`
4. **Watsons (`clean_watsons`)**:
   - `sale_price_try` = `price.value` (TL formatında)
   - `list_price_try` = `price.value` *(İndirim öncesi fiyat label'dan ayrıştırılacaktır)*
   - `discount_rate` = `0.0`

> 🛡️ **Fiyat Kalite Filtresi (Outlier Rule):** `sale_price_try <= 0.0` VEYA `sale_price_try > 100000.0` olan mantıksız kayıtlar Silver katmanında `is_valid_price = FALSE` olarak işaretlenecektir.

---

### Adım 2: EAN / Barkod Validasyonu ve Temizliği (GTIN Standard)

Barkod, fuzzy matching öncesi en yüksek öncelikli ürün eşleştirme anahtarıdır.

1. **Watsons Virgüllü Barkod Ayıklama:**
   - Watsons tablosundaki virgüllü EAN'ler için ilk barkod alınır: `string_split(ean, ',')[1]`.
2. **Karakter Temizliği:**
   - Boşluklar, tireler ve basılmayan karakterler silinir: `REGEXP_REPLACE(barcode, '[^0-9]', '', 'g')`.
3. **Format Doğrulama (GTIN Checks):**
   - Barkod uzunluğu 8, 12, 13 veya 14 haneli olmalı ve sadece rakamlardan oluşmalıdır: `REGEXP_MATCHES(clean_barcode, '^[0-9]{8,14}$')`.
   - Doğrulamadan geçmeyen veya boş gelen barkodlar için `is_valid_barcode = FALSE` yapılır ve `clean_barcode = NULL` atanır.

---

### Adım 3: Marka (Brand Name) Standardizasyonu

1. **Format Birliği (Title Case Conversion):**
   - Watsons markaları tamamen büyük harflidir (`PASTEL` ➔ `Pastel`, `MAYBELLINE` ➔ `Maybelline`).
   - `clean_brand_name = INITCAP(LOWER(TRIM(raw_brand_name)))` dönüşümü uygulanır.
2. **Eve Shop Marka Çıkarma (Brand Extraction):**
   - Eve Shop'ta ayrı marka kolonu olmadığından, bilinen marka havuzu (Gratis, Rossmann, Watsons markaları) kullanılarak ürün başlığının (`title`) başındaki marka ismi regex ile tespit edilip ayrıştırılacaktır.
3. **Marka Alias Tablosu (Master Brand Alignment):**
   - Yazım farkı olan markalar tekilleştirilir:
     - `'L’Oreal Paris'`, `'Loreal'`, `'L’Oreal'` ➔ `'L'Oreal'`
     - `'Nivea Men'`, `'Nivea'` ➔ `'Nivea'`

---

### Adım 4: Ürün İsim & Hacim/Birim Çıkarma (Product Title & Volume Extraction)

Ürün başlığındaki gürültüler temizlenip hacim bilgisi ayrıştırılır:

1. **HTML & Karakter Temizliği:**
   - `&amp;` ➔ `&`, `&quot;` ➔ `"`, `<br>` ➔ ` `, `\xa0` ➔ ` ` değişimleri yapılır.
   - Baş ve sondaki boşluklar silinir, ardışık boşluklar teke indirilir.
2. **Hacim ve Birim Ayıklama Regex Kuralları:**
   - **Sıvı Hacim (ML/L):** `(?i)\b(\d+(?:[.,]\d+)?)\s*(ml|l|cl)\b`
     - Örn: `"150 ml"` ➔ `extracted_amount = 150.0`, `extracted_unit = 'ml'`
   - **Ağırlık (GR/KG):** `(?i)\b(\d+(?:[.,]\d+)?)\s*(gr|g|kg)\b`
     - Örn: `"50 gr"` ➔ `extracted_amount = 50.0`, `extracted_unit = 'g'`
   - **Adet/Parça:** `(?i)\b(\d+)\s*(adet|pk|li|lü|lu|parça)\b`
     - Örn: `"2 Adet"` ➔ `extracted_amount = 2.0`, `extracted_unit = 'adet'`

---

### Adım 5: Stok Durumu Standartlaştırma (Stock Normalization)

Tüm kaynakların stok bilgileri tek bir `is_in_stock` (BOOLEAN) kolonuna dönüştürülür:

- **Gratis**: `CASE WHEN stockStatus IN ('HIGH', 'MEDIUM', 'LOW') THEN TRUE ELSE FALSE END`
- **Rossmann**: `CASE WHEN _source.is_in_stock = 1 THEN TRUE ELSE FALSE END`
- **Watsons**: `inStockFlag` (`TRUE`/`FALSE`)
- **Eve Shop**: `available` (`TRUE`/`FALSE`)

---

### Adım 6: Kategori Hiyerarşisi Standartlaştırma

Kategori metinleri ayrıştırılarak 3 seviyeli kategori ağacı üretilir:

- **Watsons**: `categoryNameHierarchy` dizesindeki `/` karakterine göre ayrıştırılır. (Örn: `'Makyaj/Dudak/Ruj'` ➔ `L1: Makyaj`, `L2: Dudak`, `L3: Ruj`).
- **Rossmann**: `_source.breadcrumb` dizesindeki `>` karakterine göre ayrıştırılır.
- **Gratis & Eve Shop**: `_category_folder` dizesinden ana kategori türetilir.

---

## 🔄 4. Silver Katmanı DML (DuckDB SQL Dönüşüm Şablonu)

Aşağıda **Gratis** tablosundan `silver_products` tablosuna aktarım yapacak örnek SQL dönüşüm mantığı yer almaktadır:

```sql
INSERT INTO silver_products (
    silver_id,
    site_code,
    source_product_id,
    source_sku,
    fetch_date,
    raw_barcode,
    clean_barcode,
    is_valid_barcode,
    raw_product_name,
    clean_product_name,
    raw_brand_name,
    clean_brand_name,
    list_price_try,
    sale_price_try,
    discount_rate,
    is_in_stock,
    category_path,
    product_url
)
SELECT
    md5('gratis_' || id || '_' || CAST(_fetch_date AS VARCHAR)) AS silver_id,
    'gratis' AS site_code,
    id AS source_product_id,
    id AS source_sku,
    _fetch_date AS fetch_date,
    "attributes.eanUpc" AS raw_barcode,
    CASE 
        WHEN REGEXP_MATCHES(TRIM("attributes.eanUpc"), '^[0-9]{8,14}$') THEN TRIM("attributes.eanUpc")
        ELSE NULL 
    END AS clean_barcode,
    CASE 
        WHEN REGEXP_MATCHES(TRIM("attributes.eanUpc"), '^[0-9]{8,14}$') THEN TRUE 
        ELSE FALSE 
    END AS is_valid_barcode,
    "attributes.displayName" AS raw_product_name,
    TRIM(REGEXP_REPLACE("attributes.displayName", '\s+', ' ', 'g')) AS clean_product_name,
    "attributes.brand" AS raw_brand_name,
    INITCAP(LOWER(TRIM("attributes.brand"))) AS clean_brand_name,
    ROUND("prices.normalPrice" / 100.0, 2) AS list_price_try,
    ROUND("prices.discountedPrice" / 100.0, 2) AS sale_price_try,
    "prices.discountRate" AS discount_rate,
    CASE WHEN stockStatus IN ('HIGH', 'MEDIUM', 'LOW') THEN TRUE ELSE FALSE END AS is_in_stock,
    _category_folder AS category_path,
    'https://www.gratis.com/p/' || id AS product_url
FROM clean_gratis;
```

---

## 🧪 5. Veri Kalite Kontrolleri (Sanity Checks & Quality Gates)

Silver katmanı dönüşümü tamamlandıktan sonra çalıştırılacak doğrulama sorguları:

1. **Tekillik Kontrolü (Uniqueness Check):**
   `SELECT site_code, source_product_id, fetch_date, COUNT(*) FROM silver_products GROUP BY 1,2,3 HAVING COUNT(*) > 1;` (0 satır dönmeli).
2. **Barkod Geçerlilik Oranı:**
   `SELECT site_code, COUNT(*) as total, COUNT(clean_barcode) as valid_ean_count, ROUND(COUNT(clean_barcode)*100.0/COUNT(*), 2) as ean_success_rate FROM silver_products GROUP BY site_code;` (%98+ hedeflenir).
3. **Fiyat Sıfır / Negatif Kontrolü:**
   `SELECT COUNT(*) FROM silver_products WHERE sale_price_try <= 0;` (0 olmalı).

---

*Plan Sonu.*
