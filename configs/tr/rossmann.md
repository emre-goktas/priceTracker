# rossmann.yaml — Notlar

`configs/tr/rossmann.yaml`'daki tüm `#` yorum satırları buraya taşındı (2026-08-06). YAML artık sade — sadece config verisi. `field_mapping` altındaki `note:`/`confidence:` alanları YAML'da kaldı (gerçek veri, yorum değil).

## Genel

Rossmann fetch-özel config. Kapsam: sitenin TAMAMI (kategori filtresi yok). Doğrulandı: curl_cffi (impersonate=chrome120) ile 200 OK, tek istekte EAN+fiyat+stok geliyor. Regex/kategori ID gibi kırılgan bilgiler burada (YAML) yaşar, core/parsers/site_plugins/rossmann.py içine literal olarak gömülmez — self-heal sadece bu dosyayı güncelleyebildiği için (bkz. CLAUDE.md).

## `rate_limit`

Sayfalar/kategoriler arası bekleme + retry — hardcode değil, buradan okunur (bkz. core/parsers/base_parser.py).

## `main_categories` / `category_discovery`

Ana kategori ID'leri artık statik (main_categories) — bunlar veritabanı/API key'i, kolay kolay değişmez. Ampirik olarak doğrulandı (2026-07-29): bu 7 kategorinin toplamı (~8839, çakışma üst sınırı) kategori filtresi olmadan bulunan tüm katalog sayısıyla (~8786) pratik olarak birebir örtüşüyor — yani eksiksiz kapsama. "Saç Bakım"/"Erkek Bakım" gibi navbar'da göze çarpan bazı öğeler yapısal olarak kisisel-bakim'in (id=4) ÇOCUĞU, ayrı kök değil; parent ID'yle sorgulamak zaten tüm alt-ağacın ürünlerini kapsıyor (anchor-category davranışı) — bu yüzden tek tek alt kategori taramaya gerek yok.

category_discovery aşağıda REFERANS olarak duruyor (her kök kategori sayfası kendi JS state'inde `currentCategory: N` taşıyor — bu listenin nasıl türetildiğinin kaydı) ve normal fetch akışında ÇALIŞTIRILMAZ; selfheal katmanında kategori drift kontrolü için değerlendirilecek.

`extraction.children_json_key`: childPathsMapping örneği: `{"1/2/4/50": {"entity_id":"50","name":"Ağız ve Diş Bakım"}, ...}`.

Doğrulandı (2026-07-28), navbar isimleriyle sıra dahil birebir eşleşti:
- kisisel-bakim id=4 -> 50 Ağız ve Diş Bakım, 178 Banyo Ürünleri, 51 Parfüm & Deodorant, 52 Saç Bakım, 56 Kadın Bakım Ürünleri, 57 Epilasyon/Ağda/Tıraş, 58 Ayak Bakımı, 53 Erkek Bakım
- temizlik id=7 -> 188 Genel Temizlik, 156 Çamaşır, 157 Bulaşık, 158 Fırın, 160 Banyo, 159 Mutfak Temizlik Malzemeleri, 162 Temizlik Malzemeleri, 163 Kokular
- makyaj id=3 -> 9 Dudak, 10 Göz-Kaş, 11 Yüz, 12 Tırnak, 13 Makyaj Aksesuarları, 14 Doğal Makyaj Ürünleri
- diğer kök slug'lar (cilt-bakimi, ev-yasam, anne-bebek, saglik-gida) aynı yöntemle işlenir.

NOT: "Saç Bakım"/"Erkek Bakım" navbar'da göze çarpsa da yapısal olarak kisisel-bakim'in ÇOCUĞU (id=4 altında) — ayrı kök kategori değiller, bu kasıtlı bir tasarım, eksiklik değil.

## `category_search_api`

`pagination.page_size: 60` — eski rossmann_batch.py'de 60 max limit notu vardı; 12 ve 60 ikisi de 200 döndü.

`response.items_path`: her hit, gerçek ürün verisi `hit._source` altında sarmalanmış.

## `product_id_field`

matching/duckdb_pipeline.py'nin dedup adımı için: ham üründe kararlı/benzersiz kimlik hangi alanda. Rossmann'da bu, gerçek çoklu-kategori üyeliğinden geliyor - sayfalama hatası değil.

## `raw_columns.drop_prefixes`

Ham veriye HİÇ alınmayacak alan önekleri (bkz. matching/duckdb_pipeline.py -> _drop_prefixes). `cat_pos_*` = Magento'nun ürünün HER üye olduğu kategori listesinde kaçıncı sırada göründüğünü tutan iç sıralama alanı (örn. cat_pos_1837=42). Ürün başına 100+ tane var (doğrulandı: 1.089.357 satır, ürün başına ortalama ~107) - fiyat takibiyle hiçbir ilgisi yok, hiçbir kod tarafından okunmuyordu (2026-08-03 denetiminde doğrulandı: sıfır gerçek kullanım). Eskiden ayrı bir child tabloya (raw_rossmann_source_cat_pos) taşınıyordu - kullanıcının kararıyla (2026-08-03) artık hiç arşivlenmiyor, bu da _split_repeating_groups mekanizmasını tamamen gereksiz kıldı (kaldırıldı, bkz. matching/duckdb_pipeline.py) - 4 sitenin hiçbirinde artık >=10 üyeli tekrarlı alan ailesi kalmadı.

## `field_mapping`

field_mapping — 2026-08-02'de yazıldı. Kullanıcının elindeki bağımsız kaynaklar (tek ürünlük örnek JSON, "explanation" alanlarıyla annotate edilmiş + 364 satırlık CSV) SADECE başlangıç örneklemesi/kroki olarak kullanıldı, Gemini'nin doc1/02/03'teki iddiaları da dahil hiçbir şeye doğrudan güvenilmedi (bkz. feedback_verify_ai_reports.md) - her satır ya raw_rossmann'daki gerçek veriyle (N=364 CSV karşılaştırması) ya da kullanıcının CANLI sitede/sepette yaptığı testlerle çapraz kontrol edildi.

**BÜYÜK ÖLÇEKLİ CSV DOĞRULAMASI** (364 ürün, kullanıcının bağımsız/eski kaydı, raw_rossmann'ın 2026-08-01 snapshot'ıyla programatik karşılaştırıldı):
- barcode: 359/359 (%100) BİREBİR | name: 359/359 (%100) BİREBİR
- price: 358/359 (%99.7) | special_price: 358/359 (%99.7) | is_in_stock: 350/359 (%97.5)
- crm_price: 270/359 (%75.2, kalan fark zamanlama - CSV daha eski bir tarihten, kampanya değişken - Watsons'taki otherPrices'la aynı doğa)
- qty: 119/359 (%33.1) ve sold: 14/359 (%3.9) - DÜŞÜK ama SORUN DEĞİL, bunlar doğası gereği sürekli değişen canlı sayaçlar (stok satıldıkça azalır, sold hep artar), aynı anda alınmayan 2 snapshot'ta eşleşmeleri zaten beklenmez.

**EN ÖNEMLİ BULGU** - crm_price ("Rossmann Card Fiyatı") GERÇEKTEN KOŞULLU, Watsons'ın otherPrices/MEMBER'ından FARKLI (2026-08-02, kullanıcı canlı sepet testi): Ürün SR12062947, price=179, crm_price=159. Sayfada İKİ fiyat da gösteriliyor (179,00 TL + "Rossmann Card ile 159,00") - Watsons gibi PUBLIC görünür. AMA sepete eklenince "Ara Toplam: 179,00 TL" çıktı + banner: "Rossmann Card üyesi olmanız halinde toplam indirim tutarı: 20.00 TL" - yani indirim GERÇEKTEN karta/üyeliğe koşullu, giriş yapmadan/kart olmadan UYGULANMIYOR. Watsons'ta member fiyatı login GEREKTİRMEDEN gerçekten ödenen fiyattı (3 checkout testinde doğrulanmıştı) - Rossmann'da AYNI VARSAYIMI yapıp crm_price'ı sale_price_try'a coalesce etmek YANLIŞ olurdu, bu yüzden Gratis'in conditional_promo_price_try deseni kullanıldı (ayrı alan, sale_price_try'a KARIŞTIRILMADI).

confidence seviyeleri (Gratis/Watsons'la aynı sözleşme):
- `verified_checkout` -> gerçek sepete eklenip/checkout'a gidilip doğrulandı
- `verified_data` -> raw_rossmann'daki gerçek veriyle (N=364 CSV dahil) doğrulandı
- `plausible_unverified` -> mantıklı ama canlı sitede bizzat çapraz kontrol edilemedi

Fiyat alanları: N=364 CSV doğrulaması + kullanıcının CANLI sepet testiyle netleşti (2026-08-02) - `sale_price_try`/`conditional_promo_price_try` formüllerinin çok katmanlı doğrulama süreçleri (ross_60_price/crm_price/cmp_100/50/20_price katmanları, GA4 analytics'in yanıltıcı olduğu ders) kendi `note:` metinlerinde, YAML'da duruyor.
