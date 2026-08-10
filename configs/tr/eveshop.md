# eveshop.yaml — Notlar

`configs/tr/eveshop.yaml`'daki tüm `#` yorum satırları buraya taşındı (2026-08-06). YAML artık sade — sadece config verisi. `field_mapping` altındaki `note:`/`confidence:` alanları YAML'da kaldı (gerçek veri, yorum değil).

## Genel — kapsam ve altyapı

Eveshop fetch-özel config. Kapsam: sitenin TAMAMI (kategori filtresi yok).
Altyapı: Shopify (Gratis/Watsons/Rossmann'dan farklı - JSON API yok, HTML kazıma gerekiyor, WAF/Cloudflare korumalı -> curl_cffi impersonate=chrome120 zorunlu, bkz. shared/http_client.py).

**ÖNEMLİ - product_fetcher.py bilerek KULLANILMIYOR** (CLAUDE.md'de tanımlı dosya boş kalıyor):
Kategori sayfası HTML'inde ürün kartı başına 2 ayrı gömülü JSON bloğu var:
1. `x-labels-data` attribute'u -> fiyat/kampanya (eve_price, price, product_id, variant_id)
2. `x-variants-data` class'lı div içindeki `<script type='application/json'>` -> barkod, sku, variant id (x-labels-data.variant_id ile birebir eşleşiyor, doğrulandı: eveshop_test.html üzerinde 18/18 kart)

Yani kategori taraması TEK BAŞINA fiyat+barkod+sku'nun tamamını veriyor. Ayrıca canlı bir ürün sayfası (/products/{handle}) çekilip doğrulandı: aynı yapı orada da var ama SADECE o ürünün kendi varyantı için - ekstra bilgi getirmiyor. 3277 ürünlük gerçek örneklemde (raw_json/eveshop, /products/{handle}.js çıktısı) TÜMÜ tam olarak 1 varyanta sahipti (0/3277 çok-varyantlı) - yani Eveshop'ta ton/renk farkı ayrı ürün/handle olarak modelleniyor, Shopify'ın klasik "tek üründe çoklu varyant" deseni yok. Bu yüzden "varyant tamamlama" için ayrı ürün HTML fetch'ine gerek kalmıyor -> ~14.735 ürüne tek tek istek atıp WAF riski büyütmenin veri kazancı yok. Bu koşullar değişirse (çok-varyantlı ürün keşfedilirse) product_fetcher.py burada devreye girer.

**NOT - DÜZELTME (2026-07-30, aynı gün içinde):** Burada önceden "/products/{handle}.js endpoint'inde (Kaynak C) barkod VE fiyat 0/3277, kullanılamaz" yazıyordu - bu, eski/statik bir dosya örneklemine (raw_json/eveshop/, tarihi belirsiz bir önceki scrape) dayanıyordu. Aynı gün içinde CANLI olarak yeniden test edildi: endpoint şeması değişmiş (product_type -> type gibi alan adı farkları da var) ve şu anda barkod DA, fiyat DA dolu geliyor (6 farklı kategoriden örnekle doğrulandı). Yani bu endpoint zaman içinde tutarsız/değişken davranmış olabilir - "kullanılamaz" iddiası yanlıştı, düzeltildi.

Yine de bu config Kaynak C'yi FİYAT/BARKOD için KULLANMIYOR (yukarıdaki gerekçe hâlâ geçerli: kategori taraması zaten tek istekte hepsini veriyor, ekstra ~7367 istekle WAF riski büyütmenin anlamı yok). Kaynak C'nin gerçek faydası başka bir alanda çıktı: `type` alanı temiz bir kategori hiyerarşisi veriyor ("Eve Genel Hiyerarşi > RENKLİ KOZMETİK > GÖZ ÜRÜNLERİ" gibi) - bu, configs/taxonomy/mappings/eveshop.yaml'ı inşa etmek için (Gratis'in attributes.categories'i gibi) örnekleme amacıyla kullanıldı. Sürekli veri toplama akışının (bu dosyanın konusu) parçası değil, bir defalık taksonomi keşfi içindi. (NOT: `configs/taxonomy/` dizini 2026-08-06'da kullanılmıyor olduğu için silindi — bu paragraf artık sadece tarihsel kayıt.)

## `archive`

DÜZELTME (2026-08-06): aktif kaynak artık products.json (gerçek JSON yanıt) - archive bloğu kaldırılırsa zaten varsayılan (json/application-json, bkz. core/storage.py) devreye girer, ama açıkça yazıldı ki main_categories_html_barkod_icin_gelecek görevi aktifleşince buraya geri dönüp html/text-html'e ÇEVİRMEK gerektiği unutulmasın.

ESKİ (HTML kazıma aktifken): `extension: html`, `content_type: text/html`.

## `rate_limit`

Sayfalar/kategoriler arası bekleme + retry — hardcode değil, buradan okunur (bkz. core/parsers/base_parser.py). Diğer sitelerle aynı temkinli varsayılanlar; Eveshop'a özel bir WAF-blok gözlemi henüz yok (küçük ölçekli testte hiç 403 alınmadı) ama Cloudflare arkasında olduğu bilindiği için ihtiyatlı başlanıyor.

**DENEY SONUCU (2026-08-05):** 9 kategoriye eşzamanlı istek (`max_concurrent_categories: 9`) denendi, core/fetchers/category_fetcher.py -> fetch_site_categories bunu destekliyor. SONUÇ: BAŞARISIZ - Cloudflare WAF saniyeler içinde 429 (Too Many Requests) ile bloke etti, her kategori 2-3 sayfada kesildi. Eveshop'un tam site taraması ~19dk (444 sayfa, tamamen sıralı, delay_seconds=1.5sn) HÂLÂ tek güvenli/doğrulanmış yöntem - bilerek DÜŞÜK eşzamanlılık (>1) denenmedi burada bırakılmadı, config anahtarı yine de MEVCUT (core/fetchers/category_fetcher.py'de) ama varsayılan olmadığı için hiçbir sitede aktif değil. Daha düşük bir değer (örn. 2-3) denenmeden bu satır tekrar eklenmemeli.

## `main_categories_html_barkod_icin_gelecek` / `main_categories` / `category_search_api`

DÜZELTME (2026-08-06): AKTİF kaynak artık HTML kategori kazıma DEĞİL, Shopify'ın standart toplu ürün feed'i (products.json). Gerekçe: HTML kategori taramasının Cloudflare WAF'a çok hassas olduğu 2 kez doğrulandı (2026-08-05 eşzamanlılık denemesi VE 2026-08-06 tam-sıralı üretim çalıştırması - ikisinde de 429). products.json canlı test edildi (2026-08-06): aynı kapsam için ~30 istek (444+ yerine, 250 ürün/sayfa), HİÇ blok almadı - muhtemelen Cloudflare kuralı /collections/* path'ine özel. TEK EKSİK: products.json barkodu HİÇ vermiyor (Shopify platform kısıtı, herkese açık feed'de yok) - sadece HTML kaynağında var. Kategori de kayıp DEĞİL: product_type alanı tam hiyerarşiyi veriyor (analytics_regex'in 'type' alanıyla aynı).

ESKİ main_categories (9 gerçek kategori, HTML kazıma) - AKTİF DEĞİL, silinmedi çünkü ileride "ara sıra HTML'e istek atıp barkod tazeleme" görevi için lazım olacak (henüz tasarlanmadı, kullanıcı kararıyla ertelendi). `main_categories_html_barkod_icin_gelecek` altında referans olarak duruyor, normal akışta OKUNMUYOR (kod sadece aktif `main_categories`'i okur).

products.json kategori kavramı BİLMİYOR (tek toplu feed, 9 ayrı koleksiyon değil) - MinIO'nun KESİN category/ şeması (bkz. CLAUDE.md) yine de değişmedi, yeni bir prefix/klasör seviyesi GEREKMEDİ: tek bir sözde-kategori olarak modellendi, mevcut `{category_id}_{category_name_slug}` kalıbına aynen uyuyor -> `category/eveshop/{tarih}/urunler_tum_urunler/{hash}.json`.

`pagination.zero_indexed: false` — Shopify page=1'den başlar (page=0 değil, canlı doğrulandı).

`page_size` YOK - `limit`'in üstü SESSİZCE 250'ye sabitleniyor (canlı test: `limit=5000000` bile 250 döndü). Toplam ~30 sayfa, ~7414 ürün (page 30 kısmi=164, page 60/70 boş - canlı doğrulandı 2026-08-06, sitemap çapraz kontrolündeki ~7367 aktif ürünle tutarlı).

`response`: `total_count_path`/`total_pages_path` YOK - Shopify toplam bildirmiyor, tek durma sinyali boş sayfa (empty_page, bkz. base_parser.fetch_category_pages) - Watsons/Rossmann'daki gibi API toplam bildirmeyen siteler zaten bu deseni destekliyor.

## `raw_html_extraction`

AKTİF category_search_api artık bunu OKUMUYOR (format artık 'json', HTML kazıma değil) - `main_categories_html_barkod_icin_gelecek` görevi aktifleşince tekrar devreye girecek, o yüzden silinmedi. Barkod (x-variants-data) ve ürün URL'sini HTML'den çıkarmak için hâlâ geçerli/doğrulanmış mantık - matching/duckdb_pipeline.py arşivlenmiş ham HTML'i DuckDB'ye landing yaparken kullanır. core/ ve matching/ birbirini import etmediği için (bkz. CLAUDE.md) regex'in kendisi burada, config'te tek doğruluk kaynağı olarak yaşıyor.

**`variant_block_regex`**: Barkod/sku/fiyat içeren blok - hem kategori sayfasında (class='x-variants-data', tek tırnak) hem ürün detay sayfasında (class="variant-selects", çift tırnak) aynı yapı, farklı tırnak stili - ikisini de yakalayan regex (doğrulandı, 2026-07-30).

**`product_url_regex`**: xVariantSelect(...) çağrısındaki ürün URL path'i + product_id çifti (virgülle ayrık, bitişik argümanlar) - x-labels-data.product_id ile eşleşir (doğrulandı).

**Join key alanları**: x-labels-data.variant_id == variant_block'un "id" alanı (doğrulandı, 18/18). Bu alan adları eskiden matching/duckdb_pipeline.py'de LİTERAL olarak gömülüydü (fonksiyon adı bile parse_eveshop_html idi) - site adı hardcode etmeme ilkesi gereği (bkz. CLAUDE.md) config'e taşındı; kod artık sadece "hangi alan neyle join edilecek" bilgisini okuyor.
- `variant_id_field: "id"` — variant_block içindeki kimlik alanı
- `variant_join_key: "variant_id"` — x-labels-data'da buna karşılık gelen alan
- `variant_prefix: "variant."` — birleştirilen varyant alanlarının sütun öneki

**`analytics_regex`** (2026-08-02 EKLENDİ): Shopify'ın sayfaya gömdüğü client-side analytics ("Web Pixels") olayından - "collection_viewed" event payload'ı, sayfanın ÇOK UZAK bir noktasında (kart bloğundan binlerce karakter ötede, `<script>` içindeki bir JSON-string'in İÇİNDE JSON-escape edilmiş halde) duruyor. Kullanıcının "barkod x-variants-data'dan geliyordu, görsel de HTML'den gelebilir mi?" sorusu üzerine arandı ve bulundu - bu x-labels-data/x-variants-data'dan TAMAMEN AYRI, 3. bir gömülü JSON kaynağı. Tek regex geçişinde 3 yeni alan birden verir (18/18 üründe, 2 farklı kategori sayfasında test edildi, sıfır kayıp):
- `vendor`: GERÇEK marka adı (önceden "Eveshop'ta marka kolonu yok" denmişti - YANLIŞTI, burada varmış, örn. 'JOHNSONS BABY', 'GOLDEN ROSE')
- `type`: TAM kategori hiyerarşisi (örn. 'Eve Genel Hiyerarşi > ANNE& BEBEK BAKIM > BEBEK KOZMETİK') - önceden bunun SADECE ayrı bir ürün detay sayfası isteğiyle (/products/{handle}.js, "Kaynak C") alınabileceği düşünülüyordu, YANLIŞTI - kategori sayfasının kendisinde zaten varmış, ekstra istek gerekmiyor.
- `image.src`: GERÇEK ürün görseli (önceden "hiç görsel alanı yok" denmişti - o da YANLIŞTI, sadece x-labels-data/x-variants-data'da değilmiş, bu 3. blokta varmış). Protokolsüz path (//www.eveshop.com.tr/...) - 'https:' önekiyle tam URL olur.

Join key: analytics_regex'in kendi "id" alanı (variant seviyesinde) == x-labels-data.variant_id (doğrulandı, 2 sayfada 18/18 + 18/18 tam kesişim). "sku" alanı da variant.sku ile birebir eşleşiyor (ekstra çapraz doğrulama).

NOT - regex TEK TIRNAKLA yazılmalı (YAML): pattern içinde `\\"` (tek backslash + tırnak, JSON-string-içinde-JSON-string kaçışı) birebir korunmalı - çift tırnaklı YAML string bu kaçışları YANLIŞ katlar, test edilip doğrulandı (2026-08-02).

Yakalanan gruplar sırasıyla: (vendor, product_id, product_url, category_type, variant_id, image_src, sku). Değerler JSON-string-escape'li (\/ -> /, > -> >, vb.) - unescape için `json.loads('"' + deger + '"')` kullanılmalı (matching/duckdb_pipeline.py'de böyle yapılıyor).

`analytics_groups`: yukarıdaki capture group'ların SIRALI çıktı adları. '_' ile başlayanlar tabloya YAZILMAZ, sadece join/hizalama için okunur. Bu eşleme de eskiden kodda gömülüydü.

`analytics_prefixes.image_url`: image.src protokolsüz path olarak geliyor (//www.eveshop.com.tr/...) - tam URL için önek eklenir: "https:".

## `product_id_field`

matching/duckdb_pipeline.py'nin dedup adımı için: ham üründe kararlı/benzersiz kimlik hangi alanda. Eveshop'ta tekrar KARIŞIK (2026-07-30, 422 örnek, HTML kaynağı): 33'ü aynı kategoriden (sayfalama gürültüsü), 389'u farklı kategoriden (gerçek örtüşme - büyük kısmı cok-satan-urunler).

**DÜZELTME (2026-08-07):** products.json'a geçişle `product_id_field` `"product_id"`den `"id"`ye değişti (products.json'ın kendi top-level kimlik alanı). Kategori kavramı artık yok (tek toplu feed) - yukarıdaki "farklı kategoriden gerçek örtüşme" senaryosu bu haliyle geçerli değil. Şu an arşivde tek `_fetch_date` (2026-08-06) olduğu için dedup'ın gün-aşırı davranışı henüz test edilemedi - `id` 7414/7414 satırda benzersiz (canlı doğrulandı), gerçek anlamlı doğrulama ikinci bir günün crawl'ı geldiğinde yapılabilir.

## `field_mapping`

field_mapping — 2026-08-02'de yazıldı (2026-07-30 notu artık geçersiz, normalize.py artık gerçek bir taslak - tests/matching/normalize.py). Kullanıcının elindeki bağımsız kaynaklar ("explanation" alanlarıyla annotate edilmiş örnek sayfa JSON'u + 3309 satırlık CSV) SADECE başlangıç örneklemesi olarak kullanıldı, hiçbir iddiaya doğrudan güvenilmedi (bkz. feedback_verify_ai_reports.md) - her satır raw_eveshop/clean_eveshop'taki gerçek veriyle (N=3309 CSV karşılaştırması) VE kullanıcının CANLI sitede/sepette yaptığı testlerle çapraz kontrol edildi.

**BÜYÜK ÖLÇEKLİ CSV DOĞRULAMASI** (3309 varyant, kullanıcının bağımsız/eski kaydı, clean_eveshop'un 2026-08-01 snapshot'ıyla variant_id üzerinden programatik karşılaştırıldı):
- name: 3282/3282 (%100) BİREBİR | available: 3282/3282 (%100) BİREBİR
- price (Standart Fiyat): 3282/3282 (%100) BİREBİR - istisnasız.
- qty: 3054/3282 (%93.1) - küçük fark doğal stok dalgalanması.
- eve_price: 2383/3282 (%72.6) - kalan fark CSV'nin bizim 07-01 crawl'ımızdan FARKLI bir tarihte alınmış olmasıyla tutarlı (Watsons/Rossmann'da da AYNI zamanlama-kaynaklı fark gözlemlenmişti - kampanya günden güne değişiyor, sistemik hata değil).
- 27 varyant (%0.8) bizim hiç çekmediğimiz kodlar - katalog kapsama farkı.

**EN ÖNEMLİ BULGU** - eve_price ("EVE KART+'A ÖZEL" fiyatı) Rossmann'la AYNI desende, Watsons'tan FARKLI (2026-08-02, kullanıcı 2 ayrı canlı sepet testi yaptı, İKİSİNDE DE): Sayfada İKİ fiyat gösteriliyor (örn. "2.549,50 TL" + "EVE KART +'A ÖZEL 764,50 TL") - Watsons gibi PUBLIC görünür. AMA sepete/ödeme adımına kadar giriş yapmadan gidildiğinde tam `price` tahsil ediliyor (2.549,50 TL, 764,50 DEĞİL) - indirim üyeliğe/karta GERÇEKTEN koşullu. Rossmann'daki "üye olsaydınız X TL kazanırdınız" bildirimi burada çıkmadı ama sonuç aynı: indirim otomatik uygulanmıyor. Bu yüzden Rossmann'daki gibi conditional_promo_price_try deseni kullanıldı, sale_price_try'a coalesce EDİLMEDİ.

confidence seviyeleri (diğer 3 site ile aynı sözleşme):
- `verified_checkout` -> gerçek sepete eklenip/checkout'a gidilip doğrulandı
- `verified_data` -> clean_eveshop/raw_eveshop'taki gerçek veriyle (N=3309 CSV dahil) doğrulandı
- `plausible_unverified` -> mantıklı ama canlı sitede bizzat çapraz kontrol edilemedi

Fiyat alanları: N=3309 CSV doğrulaması + kullanıcının 2 ayrı CANLI sepet testiyle netleşti (bkz. `list_price_try`/`sale_price_try`/`conditional_promo_price_try`/`discount_rate` alanlarının kendi `note:` metinleri, YAML'da duruyor). **Bu bölümdeki her şey HTML-kaynaklı field_mapping'in (aktif değil) tarihsel kaydıdır** - aşağıdaki 2026-08-07 bölümü aktif/güncel durumu anlatıyor.

### field_mapping — 2026-08-07 GÜNCELLEMESİ (products.json şemasına geçiş)

`configs/tr/eveshop.yaml`'ın `field_mapping`'i, `raw_eveshop`/`clean_eveshop`'un HTML şeklinden products.json şekline geçişiyle BAŞTAN yazıldı (JSON/HTML alan karşılaştırması için bkz. `matching/analysis/eveshop/decisions.yaml`'ın 2. sürüm başlık notu). Özet:

**Birim dönüşümü değişti:** eski HTML kaynağında `price` BIGINT/kuruştu (`unit: kurus` formülde /100 yapıyordu). products.json'da `variants[1].price` ZATEN ondalık-TL string (örn. `"418.00"`) - `unit: kurus` kaldırıldı, sadece `CAST(...AS DOUBLE)` yeterli. Tam katalogda (7414) doğrulandı: 0 satır CAST'e dirençli, min 1.0 TL, max 13998.0 TL.

**3 gap - kalıcı olarak kayboldu, hepsi `strategy: none` + gerekçeli `note:`:**
1. **`ean`** (barkod) — products.json'un herkese açık feed'inde `variants[].barcode` alanı YAPISAL OLARAK yok (7414/7414 variant objesi tarandı, hiç görülmedi - NULL değil, alan mevcut değil). Eski HTML kaynağında (x-variants-data) vardı. **Kasıtlı boş** - ileride tasarlanacak bir "HTML-tazeleme" göreviyle doldurulması planlanıyor (bkz. `main_categories_html_barkod_icin_gelecek`). Kullanıcı kararıyla (2026-08-07), eski stale HTML tablosundaki 360/7414 (%4.9 kapsama) barkod YEDEKLENMEDİ - kalıcı görünecek geçici bir lookup mekanizması kurmaya değmedi, sıfırdan başlanıyor.
2. **`conditional_promo_price_try`** (ve dolayısıyla `discount_rate`) — `eve_price` ("EVE KART+'A ÖZEL" fiyatı) Shopify'ın standart bir alanı değildi, Eveshop'un eski kategori sayfası temasının kendi `x-labels-data` attribute'undaydı. products.json'da (ne top-level ne variant seviyesinde) hiçbir karşılığı yok - kurtarma yolu yok.
3. **`stock_qty`** — eski HTML kaynağındaki `qty` (tam stok sayısı) alanının products.json'da hiçbir karşılığı yok, sadece boolean `available` var. `ean`'in aksine bunu hedefleyen bir gelecek görev de yok (HTML-tazeleme sadece barkod için tasarlanacak).

**Kayıp değil, iyileşme olan yerler:** `brand` (eski analytics_regex → yeni doğrudan `vendor`), `url` (eski regex → yeni doğrudan `handle`), `image_url` (eski protokolsüz analytics_regex path'i → yeni zaten-mutlak `images[1].src`), `category_path` (eski analytics_regex `category_hierarchy` → yeni doğrudan `product_type`, AYNI kırpma formülü çalışıyor) - hepsi artık regex'e hiç ihtiyaç duymadan, products.json'un kendi standart alanlarından geliyor. `name` de artık HTML-entity kirliliğinden (eskiden 509 satır) arınmış.

**`compare_at_price`** (Shopify'ın standart "üstü çizili fiyat" alanı, variant düzeyinde) tam katalogda **%100 NULL** çıktı (2026-08-07 canlı tarama, ayrıca DuckDB bu yüzden JSON tipine düşürmüş - Watsons'ın otherPrices'ıyla aynı "tamamen boş sütun JSON'a düşer" fenomeni). Gratis'in normalPrice/discountedPrice deseni gibi kullanılabilir bir sinyal DEĞİL şu an, formül kurulmadı. İleride dolarsa (`list_price_try=compare_at_price`, `sale_price_try=price`) yeniden değerlendirilmeli - o zaman `CAST(...AS DOUBLE)` ile sarmalanması gerekecek (aynı JSON-tipleme riski).

### HTML-supplement mimarisi — 2026-08-07 (yukarıdaki 3 gap'in kısmi çözümü)

Yukarıdaki bölümde "kalıcı olarak kayboldu" denen 3 gap'ten (`ean`, `conditional_promo_price_try`/`discount_rate`, `stock_qty`) ilk oturumda sadece `ean` için bir gelecek görevi ("HTML-tazeleme") planlanmıştı. Aynı gün içinde WAF'ın sakinleştiği doğrulanınca (bkz. Errors bölümündeki 440 sayfa/9 kategori/37.2 dakika/sıfır blok koşusu) kullanıcı kararıyla kapsam genişletildi: **üçü de** (barkod + eve_price + qty) HTML'den ayrı, ara sıra çalışan bir supplement akışıyla dolduruluyor. Bu, `field_mapping`'teki `strategy: none` notlarını GEÇERSİZ KILMAZ — o notlar hâlâ "products.json'da yapısal olarak yok" gerçeğini anlatıyor, sadece artık `ean_html`/`eve_price_html`/`qty_html` adıyla `clean_eveshop`'a ayrı bir yoldan giriyorlar (aşağıya bkz.).

**Config: dormant bloklar.** `configs/tr/eveshop.yaml`'da HTML kaynağının tam çekim şekli, aktif `category_search_api`/`archive`/`rate_limit`/`main_categories` bloklarının YANINDA, `_html_barkod_icin_gelecek` son ekiyle pasif (aktif pipeline'ın hiç okumadığı) bloklar olarak duruyor: `main_categories_html_barkod_icin_gelecek` (9 gerçek kategori), `archive_html_barkod_icin_gelecek`, `category_search_api_html_barkod_icin_gelecek` (`/collections/{category_id}` + `format: html_regex_json` + `x-labels-data` regex'i), `rate_limit_html_barkod_icin_gelecek` (`delay_seconds: 4.0`, 2026-08-07'de 440 sayfada sıfır blokla ampirik doğrulandı). Tek doğruluk kaynağı ilkesi (CLAUDE.md) gereği bu şekil sadece burada yaşıyor, ayrı bir script içinde ad-hoc tekrarlanmadı.

**`matching/eveshop_html_supplement.py`** (yeni dosya) — `duckdb_pipeline.load_site()`'ın genel `config_override`/`raw_table_override`/`tracking_key_override` parametrelerini kullanarak (site adı hardcode edilmeden) yukarıdaki dormant blokları birleştirip ayrı bir sahte-config kurar (`build_html_config()`), `product_id_field: "product_id"` (HTML kaynağının kendi id alanı, `id` değil). Bunu `raw_eveshop_html` tablosuna (tracking_key: `eveshop_html`, `_ingested_objects`'te `raw_eveshop`'unkinden İZOLE) `full_refresh=True` ile yükler — bu script her çalıştığında HTML kaynağının o anki tam halini alır, incremental değil (ara sıra elle/manuel tetiklenen bir görev, DAG'a henüz bağlanmadı). Sonra `build_supplement()` ile `eveshop_html_supplement` tablosunu türetir: `product_id` başına en güncel (`_fetch_date DESC, _row_id`) satırı seçip `ean_html`/`eve_price_html`(`>0` ise, yoksa NULL)/`qty_html`/`html_fetch_date` kolonlarına indirger.

*Bulunan hata:* dedup ilk denemede sadece `_row_id`'ye göre sıralıyordu — MinIO obje adları alfabetik sıralandığı için "2026-08-06" tarihli objeler "2026-08-07"den önce geliyordu, bu da bazı ürünlerde bayat veriyi güncel verinin üzerine seçtiriyordu. `ORDER BY _fetch_date DESC, _row_id` ile düzeltildi, tüm 7292 satır yeniden `html_fetch_date=2026-08-07` gösterdi.

**`matching/analysis/build_clean.py` → `SUPPLEMENT_JOINS`** — `VALUE_CLEAN_OVERRIDES`'a paralel, yeni bir istisna sözlüğü: `clean_{site}` kurulurken (varsa) `eveshop_html_supplement`'ı `id = product_id` üzerinden `LEFT JOIN` edip `ean_html`/`eve_price_html`/`qty_html`/`html_fetch_date` kolonlarını ekliyor. Supplement tablosu henüz üretilmemişse (`_table_exists` kontrolü) JOIN atlanıyor — `clean_eveshop` yine kurulur, o 4 kolon NULL olarak eklenir (script'in hiç çalıştırılmamış olması pipeline'ı kırmaz).

**BUG + DÜZELTME (2026-08-11, taze sunucu kurulumunda yakalandı):** Yukarıdaki "kolonlar NULL kalır" davranışı İLK sürümde YANLIŞTI - tablo yoksa kolonlar hiç EKLENMİYORDU (`clean_eveshop` 20 değil 16 kolon oluyordu). `configs/tr/eveshop.yaml`'ın field_mapping'i bu 4 kolona koşulsuz referans verdiği için (`ean: {path: "ean_html"}` vb.), taze bir sunucuda (supplement script'i hiç çalıştırılmamış) `matching.normalize` "Referenced column ean_html not found" hatasıyla **Eveshop'u TAMAMEN atlıyordu** - `pricing.silver_products`'ta hiç Eveshop verisi yoktu. Dev makinede hiç fark edilmedi çünkü orada supplement tablosu zaten üretilmişti (JOIN her zaman gerçekleşiyordu, NULL-fallback yolu hiç çalışmamıştı). Düzeltme: `SUPPLEMENT_JOINS`'e `add_sql` yerine bare `columns` listesi eklendi, tablo yoksa `NULL AS {col}` ile kolonlar yine de projekte ediliyor - `clean_eveshop` artık supplement'ın var/yok olmasından bağımsız HER ZAMAN 20 kolon. Dev makinede tabloyu geçici yeniden adlandırıp (silmeden) her iki yolu da (var/yok) test ederek doğrulandı.

**`field_mapping` güncellemesi** — `ean`/`conditional_promo_price_try`/`discount_rate`/`stock_qty` artık `strategy: none` değil, sırasıyla `ean_html`/`eve_price_html` (`unit: kurus` — HTML kaynağı kuruş cinsinden, örn. `eve_price_html=39600` → 396.00 TL)/hesaplanmış `discount_rate`/`qty_html`'i okuyor.

**`matching/normalize.py` — kritik yan etki: `raw_{site}` yerine `clean_{site}`'tan okuma.** Supplement kolonları (`ean_html` vb.) sadece `clean_eveshop`'ta var (build_clean.py'nin JOIN'inden geliyorlar), `raw_eveshop`'ta değil — bu yüzden `build_site_select()`'in `FROM raw_{site}` yerine `FROM clean_{site}` okuması ZORUNLU hale geldi. Bu TÜM 4 site için geçerli global bir değişiklik (eveshop'a özel değil). Doğrulama sırasında bunun bir regresyona yol açtığı ortaya çıktı: Watsons'ın `image_url` field_mapping'i hâlâ ham `images` STRUCT'ına doğrudan referans veren bir `computed` formülüydü, ama `clean_watsons`'ta `images` artık yok (`VALUE_CLEAN_OVERRIDES` onu `image_url`'e çevirip drop ediyor). `configs/tr/watsons.yaml`'da `image_url` → `strategy: direct_path, path: "image_url"` yapılarak düzeltildi (clean_watsons zaten hesaplamış durumda).

**Sonuç (2026-08-07 doğrulaması):** `eveshop_html_supplement`: 7292 ürün (raw_eveshop_html: 8051 satır, 9 kategori × ~49 sayfa). `clean_eveshop`: 7414 satır (0 dedup kaybı), 20 kolon (16 temel + 4 supplement). Kapsam: 7292/7414 = **%98.4**. `silver_products` (tüm 4 site): 38019 satır, 0 tekillik ihlali, `ean` NULL oranı **%19.5 → %0.3** (7416 → 124/38019) düştü. Spot-check (id=8644540530826): `ean=[7332531119290]` (canlı doğrulanan HTML barkoduyla birebir eşleşiyor), `conditional_promo_price_try=169.0`, `discount_rate=59.57`, `stock_qty=12` — hepsi doğru.

**Mimari özet — artık 4 katmanlı akış:** `raw_eveshop` (products.json, birincil/sık — günde defalarca) + `raw_eveshop_html` (HTML, ikincil/ara sıra — manuel tetikleme) → `eveshop_html_supplement` (HTML'den türetilmiş, product_id başına tekilleştirilmiş lookup) → `clean_eveshop` (raw_eveshop + supplement'ın LEFT JOIN'i, decisions.yaml'ın keep/use kovaları + SUPPLEMENT_JOINS) → `silver_products` (normalize.py, artık clean_{site}'tan).
