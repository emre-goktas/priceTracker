# watsons.yaml — Notlar

`configs/tr/watsons.yaml`'daki tüm `#` yorum satırları buraya taşındı (2026-08-06). YAML artık sade — sadece config verisi. `field_mapping` altındaki `note:`/`confidence:` alanları YAML'da kaldı (gerçek veri, yorum değil).

## Genel

Watsons fetch-özel config. Kapsam: sitenin TAMAMI (kategori filtresi yok). Doğrulandı: curl_cffi (impersonate=chrome120) ile 200 OK, tek istekte EAN+fiyat+stok geliyor. Regex/kategori ID gibi kırılgan bilgiler burada (YAML) yaşar, core/parsers/site_plugins/watsons.py içine literal olarak gömülmez — self-heal sadece bu dosyayı güncelleyebildiği için (bkz. CLAUDE.md).

## `rate_limit`

Sayfalar/kategoriler arası bekleme + retry — hardcode değil, buradan okunur (bkz. core/parsers/base_parser.py).

## `main_categories` / `category_discovery`

Ana kategori ID'leri artık statik (main_categories) — bunlar veritabanı/API key'i, kolay kolay değişmez. Ampirik olarak doğrulandı (2026-07-29): bu kategorilerin toplamı (~10136, çakışma üst sınırı) tüm site kapsamını karşılıyor — ayrı bir "tüm alt kategorileri de tara" adımına gerek yok (parent categoryCode zaten anchor-category davranışıyla tüm alt-ağacın ürünlerini kapsıyor, tek tek 791 alt kod taramak devasa bir redundant crawl olurdu).

category_discovery aşağıda REFERANS olarak duruyor (bu listenin nasıl türetildiğinin kaydı: sitemap regex + derinlik/alt-URL sayımı) ve normal fetch akışında ÇALIŞTIRILMAZ; selfheal katmanında kategori drift kontrolü için değerlendirilecek.

NOT (main_categories listesi hakkında): "0001" (Sürdürülebilir Yaşam'ın bir diğer kodu) 0 ürün döndürdüğü için (ölü/boş kod) bilerek dahil edilmedi. "parfum" (100013) ve "sadece-watsonsta" (50058) sitemap'te hiç bulunamadı — muhtemelen bilerek sitemap'e eklenmemiş kampanya/nav öğeleri.

`category_discovery` doğrulaması: Doğrulandı (2026-07-28), sıfır yeni ağ isteğiyle (tamamen kendi veritabanımızdan):
- toplam benzersiz /c/ kodu: 791 (tüm derinlikler, crawl kapsamı budur)
- main_category_rule ile ayıklanan gerçek ana kategoriler (alt-URL sayısı ile): makyaj/100(187), kisisel-bakim/103(136), cilt-bakim/101(134), sac-bakim/102(129), erkek-bakim/105(59), saglikli-yasam/106(28), ev-ve-yasam/108(21), aksesuar/109(15), surdurulebilir-yasam/0001|107|30002(5 her biri), k-beauty/104(1)
- kullanıcının bildiği "parfum" (100013) ve "sadece-watsonsta" (50058) sitemap'te hiç yok (muhtemelen bilerek sitemap'e eklenmemiş kampanya/nav öğeleri) -> crawl'a dahil olamazlar, ama zaten all_category_codes zaten en kapsamlı kaynak, eksik kalan bu 2 tanesi ürün kaybı yaratmaz çünkü o ürünler başka /c/ kodlarında da (örn. üst kategori) muhtemelen yer alır.

## `category_search_api`

`response.format: json_or_xml` — content-type bazen XML bazen JSON dönüyor (aynı veri, sunucu/edge tarafı bir content-negotiation tutarsızlığı — bot engeli DEĞİL, doğrulandı). İkisi de handle edilecek.

## `product_id_field`

matching/duckdb_pipeline.py'nin dedup adımı için: ham üründe kararlı/benzersiz kimlik hangi alanda. Watsons'ta bu, gerçek çoklu-kategori üyeliğinden geliyor (aynı ürün gerçekten birden fazla kategoriye ait, sayfalama hatası değil - doğrulandı, 2026-07-30).

## `field_mapping`

field_mapping — 2026-08-02'de yazıldı. Gemini'nin 01/02/03 raporları SADECE başlangıç örneklemesi olarak kullanıldı, doğrudan güvenilmedi (bkz. feedback_verify_ai_reports.md) — gerçek clean_watsons/raw_watsons verisiyle + kullanıcının CANLI sitede/sepette yaptığı testlerle çapraz kontrol edildi. Gemini'nin 3 somut hatası bulundu:
1. `price.savePrice`/`price.isTpr`/`price.priceType` "indirim göstergesi" diye sundu ama 8788 satırın TAMAMINDA sabit (''/False/'BUY') - hiçbir bilgi taşımıyor, KULLANILMIYOR.
2. `roundelCategoryBadge.image.url`'i ürün görseli sandı - aslında TÜM ürünlerde AYNI genel "Sadece Watsons'ta" rozet ikonu (sadecewatsons.png), ürüne özel değil. Gerçek görsel raw_watsons.images[1].url (STRUCT list, imageType='PRIMARY', tek eleman, 195x195 thumbnail) - clean_watsons'ta YOK (liste değerli sütun, leaf-only filtre düşürüyor), silver'a raw'dan join gerekir (Gratis'in category_path'i gibi).
3. EN ÖNEMLİSİ: Gemini'nin 3 raporunda da HİÇ bahsi geçmeyen bir alan bulundu: `otherPrices` (raw_watsons'ta liste, priceSource='MEMBER'). %34.7 satırda dolu.

confidence seviyeleri (Gratis'teki ile aynı sözleşme):
- `verified_checkout` -> gerçek sepete eklenip/checkout'a gidilip doğrulandı
- `verified_data` -> clean_watsons/raw_watsons'taki gerçek veriyle (doluluk/tutarlılık) doğrulandı
- `plausible_unverified` -> mantıklı ama canlı sitede bizzat çapraz kontrol edilemedi

### Fiyat alanları

Kullanıcının CANLI sepete ekleyip checkout'a kadar gittiği testler + geniş istatistiksel sorgularla doğrulandı (2026-08-02).

**ÖNEMLİ TARİHSEL BULGU:** otherPrices 07-29 crawl'ında satırların %69.4'ünde doluyken, 08-01 crawl'ında (o zamanki en güncel) TAMAMEN BOŞ (0/10159) - ürün bazlı bir kural değil, site çapında bir kampanya penceresinin kapanması (aynı anda 24 fiyat-alakasız boolean bayrağın TAMAMI - multibuy/newIn/preOrder/paidLoyalty/vs. - tüm veri setinde sabit False çıktı, yani bu bayraklar kategori-listeleme API'sinde hiç doldurulmuyor, otherPrices'ın açıklaması onlarda değil). BP_1458835 örneği bunu netleştirdi: 07-29'da price.value=19999 (referans) + otherPrices=9999.9 (indirimli) AYRI veriliyordu; 08-01'de API artık indirimli fiyatı DOĞRUDAN price.value'ya yazıyor (9999.9) ve eski 19999 referansı HİÇBİR alanda görünmüyor - ama kullanıcının canlı sepetinde "Genel Toplam: 19.999,00 TL" hâlâ çıktı, yani o referans sistemde hâlâ var, sadece artık bizim çektiğimiz API yüzeyinde değil.

**BÜYÜK ÖLÇEKLİ DOĞRULAMA (2026-08-02):** kullanıcının elindeki bağımsız/eski bir CSV (283 ürün, "Standart Fiyat"/"Watsons Club Fiyatı"/"Club İndirimi Var mı?" kolonlu, bizimle BİRLİKTE üretilmemiş, farklı bir zamanda hazırlanmış) raw_watsons'ın 2026-07-29 snapshot'ıyla programatik olarak satır satır karşılaştırıldı:
- "Standart Fiyat" == price.value: 283/283 (%100) BİREBİR - istisnasız.
- "Watsons Club Fiyatı" == otherPrices[priceSource='MEMBER'].value: kulüp indirimi olan 236 üründe 208'i (%88.1) BİREBİR eşleşti. Kalan ~28'i net 2x değil, 1.4x-2.8x arası dağınık bir oranla farklı - CSV'nin bizim 07-29 crawl'ımızdan FARKLI bir tarihte alınmış olmasıyla tam tutarlı (otherPrices'ın gün gün değiştiği zaten bağımsız olarak kanıtlanmıştı, bkz. yukarı). Sistemik bir hata değil, zamanlama farkı.
- 6 ürün bizim hiçbir tarihte hiç çekmediğimiz kodlar (katalog kapsama farkı, fiyat mantığıyla ilgisiz).

Bu, list_price_try/sale_price_try formülünün 1-2 örnekten çok daha geniş bir örneklemle (N=283) doğrulanmış olduğu anlamına gelir - confidence seviyeleri yükseltildi.

`sale_price_try`, `list_price_try`, `discount_rate` formüllerinin ayrıntılı gerekçeleri (otherPrices'ın CAST düzeltmesi dahil, 2026-08-06) kendi `note:` metinlerinde, YAML'da duruyor.
