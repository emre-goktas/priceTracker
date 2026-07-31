# 📊 Silver Layer Katmanı - Clean Tabloları Kolon Detay Analiz Raporu

**Proje:** PriceTracker (TR Kozmetik Fiyat Takip Sistemi)  
**Tarih:** 31 Temmuz 2026  
**Veritabanı:** `matching/pricebot.duckdb`  
**Hedef Katman:** Silver Katmanı (Kanonik Haritalama & Normalizasyon)

---

## 📑 1. Genel Bakış

Bu doküman, e-ticaret sitelerinden (`Gratis`, `Rossmann`, `Watsons`, `Eve Shop`) çekilen ham verilerin temizlenip DuckDB veritabanında saklandığı `clean_*` tablolarının detaylı kolon analizini içermektedir.

DuckDB içerisindeki mevcut tablolar ve kayıt sayıları:
- **`clean_gratis`**: 10.402 satır | 170 kolon
- **`clean_rossmann`**: 8.648 satır | 73 kolon
- **`clean_watsons`**: 8.763 satır | 84 kolon
- **`clean_eveshop`**: 7.246 satır | 45 kolon

---

## 💄 2. Gratis (`clean_gratis`) Kolon Detay Analizi

Gratis API'si zengin nesne yapısına (nested JSON) sahiptir. Tabloda 170 kolon bulunmakta olup ana gruplar aşağıdadır:

### A. Sistem & Keşif Metadata Kolonları
- **`id`** (`VARCHAR`, %100 Dolu): Gratis sistemindeki benzersiz ürün ID'si (Örn: `'10209536'`).
- **`_fetch_date`** (`DATE`, %100 Dolu): Verinin çekildiği tarih.
- **`_category_folder`** (`VARCHAR`, %100 Dolu): Keşif katmanında kullanılan slugified kategori klasör adı (Örn: `'dudak_makyaji'`).
- **`_row_id`** (`BIGINT`, %100 Dolu): DuckDB satır sıralama ID'si.
- **`_source_object`** (`VARCHAR`, %100 Dolu): MinIO nesne dosya adı / yol bilgisi.

### B. Fiyat Kolonları (`prices.*`)
> ⚠️ **KRİTİK UYARI:** Gratis fiyat verilerini **KURUŞ** cinsinden tam sayı olarak saklamaktadır. TL değerine ulaşmak için `100.0`'e bölünmelidir.
- **`prices.normalPrice`** (`BIGINT`, %100 Dolu): Ürünün liste (indirimsiz) fiyatı kuruş cinsinden (Örn: `54800` = 548,00 TL).
- **`prices.discountedPrice`** (`BIGINT`, %100 Dolu): Ürünün indirimli satış fiyatı kuruş cinsinden (Örn: `54800` = 548,00 TL).
- **`prices.promotionPrice`** (`BIGINT`, %100 Dolu): Özel kampanya / kartlı alışveriş fiyatı (Örn: `41800` = 418,00 TL).
- **`prices.currency`** (`VARCHAR`, %100 Dolu): Para birimi (`'TRY'`).
- **`prices.normalPriceLabel`** (`VARCHAR`, %100 Dolu): Formatlanmış metin liste fiyatı (Örn: `'548,00 TL'`).
- **`prices.discountedPriceLabel`** (`VARCHAR`, %100 Dolu): Formatlanmış metin indirimli fiyat (Örn: `'548,00 TL'`).
- **`prices.discountRate`** (`BIGINT`, %100 Dolu): Uygulanan indirim yüzdesi (Örn: `0`, `25`, `50`).
- **`prices.discountRateLabel`** (`VARCHAR`, %100 Dolu): Formatlanmış metin indirim oranı (Örn: `'%0'`, `'%25'`).
- **`prices.discountedText`** (`VARCHAR`, %100 Dolu): Kampanya metni açıklaması (Örn: `'Gratis Kart ile'`).
- **`prices.promotionLabel`** (`VARCHAR`, %100 Dolu): Sepet promosyon etiketi (Örn: `'250 TL ve Üzeri Alışverişe'`).

### C. Ürün & Marka Öznitelikleri (`attributes.*`)
- **`attributes.brand`** (`VARCHAR`, %100 Dolu): Ürünün markası (Örn: `'Love Generation'`, `'Golden Rose'`).
- **`attributes.displayName`** (`VARCHAR`, %100 Dolu): Ürünün tam adı/başlığı (Örn: `'Love Generation Jel Dudak Kalemi Lip Pleasure 05 Smart'`).
- **`attributes.description`** (`VARCHAR`, %100 Dolu): Ürün açıklama metni.
- **`attributes.eanUpc`** (`VARCHAR`, %100 Dolu): Ürünün EAN/UPC barkod numarası (Örn: `'4602006353027'`).
- **`attributes.brandId`** (`VARCHAR`, %100 Dolu): Gratis sistemindeki marka ID'si (Örn: `'60431'`).
- **`attributes.colorName`** (`VARCHAR`, %35.7 Dolu): Renk bilgisi (Örn: `'Şeffaf'`, `'Lila'`).
- **`attributes.size`** (`VARCHAR`, %5.2 Dolu): Beden/Ebat bilgisi.
- **`attributes.volume`** (`VARCHAR`, %12.4 Dolu): Hacim bilgisi (Örn: `'15 ml'`, `'200 ml'`).
- **`attributes.orderLimit`** (`BIGINT`, %100 Dolu): Sepet başına maksimum sipariş adedi.
- **Dinamik Kategori Özellikleri (`attributes.*`)**: `attributes.sekil`, `attributes.finish`, `attributes.cinsiyet`, `attributes.ebat`, `attributes.materyal`, `attributes.fircaTipi` vb. (130+ spresifik kategori filtresi).

### D. Stok & Durum Kolonları
- **`stockStatus`** (`VARCHAR`, %100 Dolu): Stok mevcudiyet durumu. Dağılım: `'HIGH'` (%41.3), `'NONE'` (%27.2 - Stokta Yok), `'LOW'` (%17.8), `'MEDIUM'` (%13.7).

---

## 🌿 3. Rossmann (`clean_rossmann`) Kolon Detay Analizi

Rossmann Elasticsearch / Magento API altyapısını kullanmaktadır. Tabloda 73 kolon mevcuttur:

### A. Sistem & Keşif Metadata Kolonları
- **`_index`** (`VARCHAR`, %100 Dolu): Arama indeksi adı.
- **`_id`** (`VARCHAR`, %100 Dolu): Rossmann entite ID'si (Örn: `'28932'`).
- **`_fetch_date`** (`DATE`, %100 Dolu): Veri çekim tarihi.
- **`_category_folder`** (`VARCHAR`, %100 Dolu): Kategori klasör adı.
- **`_row_id`** (`BIGINT`, %100 Dolu): Tablo satır ID'si.
- **`_source_object`** (`VARCHAR`, %100 Dolu): MinIO obje yolu.

### B. Ürün Kimlik & Barkod Kolonları (`_source.*`)
- **`_source.id`** (`BIGINT`, %100 Dolu): Sayısal ürün ID'si.
- **`_source.sku`** (`VARCHAR`, %100 Dolu): Rossmann SKU kodu (Örn: `'SR21100208'`).
- **`_source.barcode`** (`VARCHAR`, %100 Dolu): EAN-13 Barkodu (Örn: `'8800331320257'`).
- **`_source.url_key`** (`VARCHAR`, %100 Dolu): Ürün web URL uzantısı.

### C. Ürün İsim & Marka Kolonları
- **`_source.name`** (`VARCHAR`, %100 Dolu): Ürün tam adı (Örn: `'Hince Raw Glow Gel Tint R005 Hibiscus, 1 Adet'`).
- **`_source.brand`** (`VARCHAR`, %100 Dolu): Marka adı (Örn: `'Hince'`, `'Otacı'`, `'Nivea Men'`).
- **`_source.branding`** (`VARCHAR`, %99.9 Dolu): Alt marka/seri adı (Örn: `'Vaseline'`, `'Nivea Men'`).

### D. Fiyat Kolonları
> ℹ️ **FİYAT ÖLÇEĞİ:** Rossmann fiyatları **TL** bazındadır (Ondalık/Integer).
- **`_source.price`** (`BIGINT`, %100 Dolu): Liste fiyatı (Örn: `949` = 949,00 TL).
- **`_source.special_price`** (`DOUBLE`, %100 Dolu): Kampanyalı/İndirimli satış fiyatı (Örn: `949.0` TL).
- **`_source.discp`** (`BIGINT`, %100 Dolu): İndirim yüzdesi.
- **`_source.crm_price`**, **`_source.cmp_100_price`**, **`_source.ross_60_price`**: Özel üyelik ve kulüp fiyatları.

### E. Stok & Lojistik Kolonları
- **`_source.is_in_stock`** (`BIGINT`, %100 Dolu): Stok mevcudiyeti (`1` = Stokta var, `0` = Stokta yok).
- **`_source.qty`** (`BIGINT`, %100 Dolu): Depo stok adedi (Örn: `28`, `0`).
- **`_source.max_sale_qty`** (`BIGINT`, %100 Dolu): Maksimum satın alma sınırı.

### F. Kategori & Filtre Kolonları
- **`_source.breadcrumb`** (`VARCHAR`, %100 Dolu): Kategori ağacı yolu (Örn: `'Makyaj > Dudak > Ruj'`).
- **`_source.master_category_id`** (`VARCHAR`, %100 Dolu): Ana kategori kimliği.
- **`_source.size`**, **`_source.height`**, **`_source.origin`**: Fiziksel boyut, menşei ülke.

---

## 🟦 4. Watsons (`clean_watsons`) Kolon Detay Analizi

Watsons SAP Hybris e-ticaret altyapısını kullanmaktadır. Tabloda 84 kolon mevcuttur:

### A. Sistem & Keşif Metadata Kolonları
- **`_fetch_date`**, **`_category_folder`**, **`_row_id`**, **`_source_object`**: Standart boru hattı metadataları.

### B. Ürün Kimlik & Barkod Kolonları
- **`code`** (`VARCHAR`, %100 Dolu): Watsons ürün kodu (Örn: `'BP_1409863'`).
- **`defaultSku`** (`VARCHAR`, %99.9 Dolu): Watsons stok birimi kodu (Örn: `'1512875'`).
- **`ean`** (`VARCHAR`, %99.9 Dolu): Barkod (EAN) bilgisi.  
  > ⚠️ **ÖNEMLİ BULGU:** Watsons varyantlı veya ambalajı güncellenmiş ürünlerde EAN alanında virgülle ayrılmış çoklu barkod saklamaktadır (Örn: `'8691190069797,8691190069803'`).

### C. Ürün İsim & Marka Kolonları
- **`name`** (`VARCHAR`, %100 Dolu): Ürün başlığı (Örn: `'Max Factor Elixir Dudak Kalemi No: 25 Brown N Bold'`).
- **`masterBrand.name`** (`VARCHAR`, %100 Dolu): Marka adı (TÜMÜ BÜYÜK HARF formatında, örn: `'MAX FACTOR'`, `'PASTEL'`, `'RIMMEL LONDON'`).
- **`rangeName`** (`VARCHAR`): Ürün serisi/koleksiyonu adı.

### D. Fiyat Kolonları
> ℹ️ **FİYAT ÖLÇEĞİ:** Watsons fiyat verileri **TL** bazındadır (`DOUBLE`).
- **`price.value`** (`DOUBLE`, %100 Dolu): Güncel satış fiyatı (Örn: `437.9` = 437,90 TL).
- **`price.formattedValue`** (`VARCHAR`, %100 Dolu): Formatlanmış fiyat metni (Örn: `'437,90 ₺'`).
- **`price.currencyIso`** (`VARCHAR`, %100 Dolu): Para birimi (`'TRY'`).
- **`price.savePrice`** (`VARCHAR`, %100 Dolu): İndirim kazancı miktarı.

### E. Stok & Sipariş Kolonları
- **`inStockFlag`** (`BOOLEAN`, %100 Dolu): Stok durumu (`True` / `False`).
- **`stock.stockLevel`** (`BIGINT`, %100 Dolu): Stok adedi.
- **`stock.stockLevelStatus`** (`VARCHAR`, %100 Dolu): Stok durum kelimesi (`'inStock'`, `'outOfStock'`).
- **`maxOrderQuantity`**, **`minOrderQuantity`** (`BIGINT`): Sipariş sınırları.

### F. Kategori & Değerlendirme Kolonları
- **`categoryNameHierarchy`** (`VARCHAR`, %100 Dolu): Hiyerarşik kategori dizesi (Örn: `'Makyaj/Makyaj Aksesuarları/Cımbız/undefined'`).
- **`averageRating`** (`DOUBLE`, %100 Dolu): Müşteri puan ortalaması.
- **`numberOfReviews`** (`BIGINT`, %100 Dolu): Yorum sayısı.

---

## 🌺 5. Eve Shop (`clean_eveshop`) Kolon Detay Analizi

Eve Shop Shopify altyapısı kullanmaktadır. Tabloda 45 kolon bulunmaktadır:

### A. Sistem & Keşif Metadata Kolonları
- **`_fetch_date`**, **`_category_folder`**, **`_row_id`**, **`_source_object`**: Standart boru hattı metadataları.

### B. Ürün & Varyant Kimlik Kolonları
- **`product_id`** (`BIGINT`, %100 Dolu): Shopify ana ürün ID'si (Örn: `8644513759370`).
- **`variant_id`** / **`variant.id`** (`BIGINT`, %100 Dolu): Shopify varyant ID'si (Örn: `46596449697930`).
- **`variant.sku`** (`VARCHAR`): Stok kodu.
- **`variant.barcode`** (`VARCHAR`, %100 Dolu): EAN-13 barkod numarası (Örn: `'3574669909150'`).

### C. Ürün İsim & Başlık Kolonları
- **`title`** (`VARCHAR`, %100 Dolu): Ana ürün başlığı (Örn: `'Bebek Yağı 200ml'`).
- **`variant.title`** (`VARCHAR`, %100 Dolu): Varyant başlığı (Örn: `'Default Title'`, `'200 ml'`).
- **`variant.name`** (`VARCHAR`, %100 Dolu): Ürün + varyant tam adı.

### D. Fiyat Kolonları
> ⚠️ **KRİTİK UYARI:** Eve Shop fiyatları **KURUŞ** cinsinden saklamaktadır. TL karşılığı için `100.0`'e bölünmelidir.
- **`price`** (`BIGINT`, %100 Dolu): Liste / Normal fiyat kuruş cinsinden (Örn: `23000` = 230,00 TL).
- **`eve_price`** (`BIGINT`, %100 Dolu): Eve Shop özel satış fiyatı kuruş cinsinden (Örn: `13900` = 139,00 TL).
- **`compare_at_price`** (`BIGINT`, %100 Dolu): Çizili eski fiyat.
- **`price_with_currency`** (`VARCHAR`, %100 Dolu): Formatlanmış metin fiyatı (Örn: `'230.00 TL'`).

### E. Stok & Envanter Kolonları
- **`available`** (`BOOLEAN`, %100 Dolu): Stok mevcudiyeti (`True` / `False`).
- **`qty`** (`BIGINT`, %100 Dolu): Depo stok adedi (Örn: `38`, `0`).
- **`inventory_management`** (`VARCHAR`): Envanter yönetim sistemi (`'shopify'`).

### F. Kategori & Bağlantı Kolonları
- **`collections`** (`VARCHAR`, %100 Dolu): Ürünün bağlı olduğu Shopify koleksiyon ID dizisi.
- **`product_url`** (`VARCHAR`, %100 Dolu): Ürün web adresi URL uzantısı.

---

## 📌 Özet Sonuç

| Tablo Adı | Toplam Satır | Toplam Kolon | Barkod Alanı | Fiyat Birimi | Stok Durum Alanı |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **`clean_gratis`** | 10.402 | 170 | `attributes.eanUpc` | **KURUŞ** (Int) | `stockStatus != 'NONE'` |
| **`clean_rossmann`** | 8.648 | 73 | `_source.barcode` | **TL** (Int/Float) | `_source.is_in_stock == 1` |
| **`clean_watsons`** | 8.763 | 84 | `ean` (Virgüllü listeler var) | **TL** (Float) | `inStockFlag == true` |
| **`clean_eveshop`** | 7.246 | 45 | `variant.barcode` | **KURUŞ** (Int) | `available == true` |

*Rapor Sonu.*
