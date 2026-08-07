# gratis.yaml — Notlar

`configs/tr/gratis.yaml`'daki tüm `#` yorum satırları buraya taşındı (2026-08-06). YAML artık sade — sadece config verisi. `field_mapping` altındaki `note:`/`confidence:` alanları YAML'da kaldı (gerçek veri, yorum değil).

## Genel

Gratis fetch-özel config. Kapsam: sitenin TAMAMI (kategori filtresi yok). Doğrulandı: curl_cffi (impersonate=chrome120) ile 200 OK, tek istekte EAN+fiyat+stok geliyor. Regex/kategori ID gibi kırılgan bilgiler burada (YAML) yaşar, core/parsers/site_plugins/gratis.py içine literal olarak gömülmez — self-heal sadece bu dosyayı güncelleyebildiği için (bkz. CLAUDE.md).

## `rate_limit`

Sayfalar/kategoriler arası bekleme + retry — hardcode değil, buradan okunur (bkz. core/parsers/base_parser.py). Ampirik olarak gözlemlendi: throttle'sız art arda istekte Gratis WAF'ı 11 sayfayı 403 ile bloklamıştı; delay_seconds ile tekrar denendiğinde 0 blok.

## `main_categories` / `category_discovery`

Ana kategori ID'leri artık statik (main_categories) — bunlar veritabanı/API key'i, kolay kolay değişmez. Çalışma zamanında her seferinde canlı keşfe gerek yok.

category_discovery aşağıda REFERANS olarak duruyor (bu ID listesinin nasıl doğrulandığının kaydı) ve normal fetch akışında ÇALIŞTIRILMAZ; selfheal katmanında kategori drift kontrolü (Gratis yeni bir ana kategori ekler/kaldırırsa fark etmek) için değerlendirilecek.

`category_discovery.response`: salesCountData ve newProductsData aynı kategori ID setini anahtar olarak taşıyor; "all" gerçek bir kategori değil, toplu/varsayılan bölüm — hariç tutulur.

## `category_search_api`

Payload base64 encode edilip 'data' query param'ına, ayrıca '__isbase64=true' eklenir (`request_style: base64_json_payload`).

`payload_template` gerçek tarayıcı isteğiyle (Chrome DevTools Network, 2026-08-01) eşleştirildi - inStock/filterActiveProducts/fromHomepageBestsellers eskiden hiç GÖNDERİLMİYORDU, eklendi. `size` alanı BİLEREK gerçek siteden farklı (site 24 kullanıyor, biz 100) - aşağıdaki pagination notuna bak, size'ın kayıp oranına etkisi yok, 100 daha az istekle aynı işi görüyor.

**`sortBy` KRİTİK** - boş bırakılırsa (gerçek sitenin de yaptığı gibi!) Gratis'in arama motorunun sıralaması KARARSIZ: aynı ürün ardışık sayfalarda tekrar edip başka ürünlerin hiç görünmemesine sebep oluyor (doğrulandı 2026-08-01: kategori 516'da 258 üründen 54'ü kaçıyordu, 11 sayfa boyunca deterministik/tekrarlanabilir - CDN/rastgelelik değil, sıralama kararsızlığı).

**KESİN DOĞRULANDI (2026-08-01):** `attribute: createdAt, order: asc` ile hem küçük (516, 258/258 ürün) hem EN BÜYÜK kategoride (501 Makyaj, 197 sayfa, 4725/4725 ürün) SIFIR tekrar + SIFIR kayıp - tam kod yolu (base_parser.fetch_category_pages) üzerinden test edildi, üretim gecikmesiyle (1.5s) çalıştı. Bu, GERÇEK sitenin gönderdiğinden farklı (site sortBy:[] gönderiyor) ama kasıtlı - amacımız (eksiksiz veri) normal bir kullanıcının amacından (birkaç sayfa gezip ürün bulma) farklı, kararsız sıralama onlar için sorun değil bizim için eksik veri demek. `sortBy` şeması API'nin 400 validasyon hatalarından adım adım çıkarıldı (field->attribute, order zorunlu). Kabul edilen diğer alan: displayName.keyword (createdAt tercih edildi, muhtemelen benzersiz zaman damgası - displayName'de teorik çakışma riski var).

**`pagination.page_size=100`** (24 DEĞİL - gerçek sitenin kullandığı değer, kasıtlı farklılaştırıldı): 2x2 izole test (eski core/debug/pagination_test.py + matching/exploration/pagination_findings.md, 2026-08-01 — bu iki dosya artık projede yok, bulgu burada kayıtlı) kanıtladı ki kayıp oranını belirleyen TEK FAKTÖR sortBy - page_size'ın ölçülebilir etkisi yok (sort YOK: size 100 -> %20.76 kayıp, size 25 -> %20.61 kayıp, neredeyse aynı; sort VAR: size 100 -> %0.02 kayıp, size 25 -> %0.17 kayıp - size 100 hatta biraz DAHA tam). Aynı kategoride (501, 4725 ürün) size=100 SADECE 48 istek gerektirirken size=25 189 istek gerektiriyordu (~4x fazla) - aynı/daha iyi sonuç için gereksiz yere Gratis'in sunucusuna fazladan yük binmesin diye 100'e geri dönüldü.

## `product_id_field`

matching/duckdb_pipeline.py'nin dedup adımı için: ham üründe kararlı/benzersiz kimlik hangi alanda (flatten sonrası sütun adı). Aynı ürün, sayfalama sırasında sunucu sıralaması kayması yüzünden 2 farklı sayfada çıkabiliyor (doğrulandı, 2026-07-30) - bu alan üzerinden dedupe edilir.

## `raw_columns.drop_prefixes`

Ham veriye HİÇ alınmayacak alan önekleri (bkz. matching/duckdb_pipeline.py -> _drop_prefixes). `attributes.boosts.*` = Gratis'in Elasticsearch sıralama ağırlıkları (44 sütun): kategori boost'ları, "recommendedBestsellers", ve kampanya etiketleri (tags_6temsac, tags_1agsmkyaj...). Ürün hakkında hiçbir bilgi taşımazlar - Gratis'in arama motorunun hangi ürünü hangi kampanyada öne çıkardığının iç ayarı. Her yeni kampanya ham tabloya yeni bir sütun eklediği için şema zamanla sınırsız şişiyordu; ayrıca 13 üyeli brand_* ailesi tekrarlı-grup eşiğini aşıp gereksiz bir child tablo (raw_gratis_attributes_boosts_brand) üretiyordu.

## `field_mapping`

field_mapping — 2026-08-01'de YENİDEN yazıldı (önceki sürüm bu oturumda daha erken silinmişti: hiçbir kod tarafından tüketilmiyordu ve içeriği doğrulanmamıştı, bkz. proje geçmişi). Bu sefer her satır gerçek veriyle doğrulandı: clean_gratis tablosu (10402 satır) + canlı sayfa + GERÇEK SEPET/CHECKOUT testi (fiyat alanları için — en güçlü kanıt).

confidence alanı üç seviye:
- `verified_checkout` -> gerçek sepete eklenip sonuç doğrulandı (sadece fiyat alanları)
- `verified_data` -> clean_gratis'teki gerçek veriyle (doluluk/değer aralığı) doğrulandı
- `plausible_unverified` -> mantıklı ama canlı sitede bizzat çapraz kontrol edilmedi

Fiyat alanları: 4 canlı örnek + GERÇEK SEPET/CHECKOUT testiyle doğrulandı (2026-08-01) - detaylar `list_price_try`/`sale_price_try`/`conditional_promo_price_try` alanlarının kendi `note:` metinlerinde, YAML'da duruyor.
