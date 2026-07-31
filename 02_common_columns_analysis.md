# 🔗 Tablolar Arası Ortak Kolon ve Semantik Saha Eşleşme Analizi

**Proje:** PriceTracker (TR Kozmetik Fiyat Takip Sistemi)  
**Tarih:** 31 Temmuz 2026  
**Veritabanı:** `matching/pricebot.duckdb`  
**Kapsam:** `clean_gratis`, `clean_rossmann`, `clean_watsons`, `clean_eveshop`

---

## 🔍 1. Birebir (Literal) Ortak Kolonlar

Veritabanı seviyesinde `DESCRIBE` sorgusu ile analiz edildiğinde, **4 tablonun 4'ünde de tam olarak aynı isimle bulunan sadece 4 adet sistem metadata kolonu** mevcuttur:

| Kolon Adı | Veri Tipi | Doluluk Oranı | Açıklama |
| :--- | :--- | :--- | :--- |
| **`_source_object`** | `VARCHAR` | %100 | MinIO nesne deposundaki kaynak ham verinin dosya yolu (Örn: `category/gratis/2026-07-31/...json`). |
| **`_fetch_date`** | `DATE` | %100 | Verinin web kazıma (scraping/API) ile toplandığı tarih (Örn: `2026-07-31`). |
| **`_category_folder`** | `VARCHAR` | %100 | Keşif katmanında kullanılan normalize kategori dizin adı (Örn: `dudak_makyaji`, `cilt_bakim`). |
| **`_row_id`** | `BIGINT` | %100 | DuckDB tablo bazlı sıralı benzersiz satır kimlik numarası (Primary Key adayı). |

### 🛑 Neden Birebir Kolon İsmi Sayısı Az?
Her e-ticaret platformu farklı bir altyapı yazılımı (Gratis özel JSON API, Rossmann Magento/Elasticsearch, Watsons SAP Hybris, Eve Shop Shopify) kullandığı için, Bronz/Clean katmanında ham API yanıtlarının alan adları korunmuştur. Bu durum Silver katmanında bir **Kanonik Şema (Canonical Schema)** oluşturulmasını zorunlu kılmaktadır.

---

## 🧬 2. Semantik (Kavramsal) Ortak Alan Haritası

Tabloların kolon isimleri farklı olsa da temsil ettikleri iş mantığı (retail domain) %100 aynıdır. Aşağıdaki tablo, 4 e-ticaret kaynağındaki semantik karşılıkları ve Silver katmanına nasıl dönüştürüleceklerini göstermektedir:

| Kanonik Alan (Canonical Field) | Silver Veri Tipi | Gratis (`clean_gratis`) | Rossmann (`clean_rossmann`) | Watsons (`clean_watsons`) | Eve Shop (`clean_eveshop`) |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **`site_code`** | `VARCHAR` | `'gratis'` (Sabit) | `'rossmann'` (Sabit) | `'watsons'` (Sabit) | `'eveshop'` (Sabit) |
| **`source_product_id`** | `VARCHAR` | `id` | `_source.id` | `code` | `product_id` |
| **`source_sku`** | `VARCHAR` | `id` | `_source.sku` | `defaultSku` | `variant.sku` |
| **`barcode` (EAN/GTIN)** | `VARCHAR` | `attributes.eanUpc` | `_source.barcode` | `ean` *(Virgüllü listeler ayıklanmalı)* | `variant.barcode` |
| **`product_name`** | `VARCHAR` | `attributes.displayName` | `_source.name` | `name` | `title` |
| **`brand_name`** | `VARCHAR` | `attributes.brand` | `_source.brand` | `masterBrand.name` *(Büyük harf)* | Başlıktan/Varyanttan çıkarılacak |
| **`list_price_try`** | `DECIMAL(10,2)` | `prices.normalPrice / 100.0` | `_source.price` | `price.value` *(savePrice varsa eklenir)* | `price / 100.0` |
| **`sale_price_try`** | `DECIMAL(10,2)` | `prices.discountedPrice / 100.0` | `COALESCE(_source.special_price, _source.price)` | `price.value` | `COALESCE(eve_price, price) / 100.0` |
| **`currency`** | `VARCHAR` | `prices.currency` (`'TRY'`) | `'TRY'` | `price.currencyIso` (`'TRY'`) | Metinden çıkarılır (`'TRY'`) |
| **`is_in_stock`** | `BOOLEAN` | `stockStatus != 'NONE'` | `_source.is_in_stock == 1` | `inStockFlag == true` | `available == true` |
| **`stock_qty`** | `INTEGER` | `NULL` | `_source.qty` | `stock.stockLevel` | `qty` |
| **`category_path`** | `VARCHAR` | `_category_folder` | `_source.breadcrumb` | `categoryNameHierarchy` | `collections` / `_category_folder` |
| **`product_url`** | `VARCHAR` | URL template + `id` | `_source.url_key` | `url` | `product_url` |
| **`image_url`** | `VARCHAR` | Statik URL yapısı | `_source.image` | `roundelCategoryBadge.image.url` | `variant.featured_image` |
| **`fetch_date`** | `DATE` | `_fetch_date` | `_fetch_date` | `_fetch_date` | `_fetch_date` |

---

## ⚠️ 3. Eşleştirmede Dikkate Alınması Gereken Kritik Farklılıklar

### 1. Fiyat Ölçeği Sapması (Price Scale Difference)
- **Gratis & Eve Shop**: Fiyatları **Kuruş** cinsinden tamsayı olarak tutar. (Örn: 150 TL -> `15000`).
- **Rossmann & Watsons**: Fiyatları **TL** cinsinden ondalıklı/tamsayı olarak tutar. (Örn: 150 TL -> `150.0` veya `150`).
- **Silver Çözümü**: Gratis ve Eve Shop fiyatları `100.0` değerine bölünerek standart `DECIMAL(10,2)` TL formatına getirilecektir.

### 2. Barkod (EAN) Yapısı ve Çoklu Barkodlar
- **Gratis, Rossmann, Eve Shop**: Tekil 13 haneli EAN string tutar.
- **Watsons**: Bazı varyantlı veya ambalaj yenilenmiş ürünlerde virgülle ayrılmış birden fazla EAN tutar (Örn: `'8691190069797,8691190069803'`).
- **Silver Çözümü**: Watsons EAN alanı `string_split(ean, ',')[1]` fonksiyonu ile ilk/ana EAN çekilecek, opsiyonel olarak ikincil EAN'ler dizi (array) alanında saklanacaktır.

### 3. Stok Durumu Temsili
- **Gratis**: Metinsel stok durumu (`'HIGH'`, `'MEDIUM'`, `'LOW'`, `'NONE'`).
- **Rossmann**: Sayısal flag (`1` / `0`) + Stok miktarı (`qty`).
- **Watsons & Eve Shop**: Mantıksal boolean (`True` / `False`) + Stok miktarı (`qty`).
- **Silver Çözümü**: Tüm kaynaklar `is_in_stock` (BOOLEAN) kolonuna eşlenecektir. Gratis için `stockStatus != 'NONE'` kuralı uygulanacaktır.

### 4. Marka Adı Biçimlendirmesi
- **Watsons**: Marka isimlerini tamamen büyük harfle tutar (Örn: `'MAYBELLINE'`, `'L’OREAL PARIS'`).
- **Gratis & Rossmann**: Title Case olarak tutar (Örn: `'Maybelline'`, `'L’Oreal Paris'`).
- **Eve Shop**: Ayrı bir marka kolonu yoktur; ürün başlığı (`title`) içinden ayıklanmalıdır.
- **Silver Çözümü**: Marka isimleri `INITCAP(LOWER(brand_name))` ile standartlaşacak, Eve Shop için regex bazlı marka çıkarma uygulanacaktır.

---

*Rapor Sonu.*
