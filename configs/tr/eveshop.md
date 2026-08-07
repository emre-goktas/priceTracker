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

matching/duckdb_pipeline.py'nin dedup adımı için: ham üründe kararlı/benzersiz kimlik hangi alanda. Eveshop'ta tekrar KARIŞIK (2026-07-30, 422 örnek): 33'ü aynı kategoriden (sayfalama gürültüsü), 389'u farklı kategoriden (gerçek örtüşme - büyük kısmı cok-satan-urunler).

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

Fiyat alanları: N=3309 CSV doğrulaması + kullanıcının 2 ayrı CANLI sepet testiyle netleşti (bkz. `list_price_try`/`sale_price_try`/`conditional_promo_price_try`/`discount_rate` alanlarının kendi `note:` metinleri, YAML'da duruyor).
