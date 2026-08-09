# price-bot — Proje Talimatları

## Proje Özeti

Türk e-ticaret kozmetik sitelerinden (Gratis, Rossmann, Watsons, Eveshop) fiyat verisi toplayan, kendi kendini onarabilen (self-healing) bir fiyat takip sistemi. Uzun vadeli hedef: çoklu coğrafyaya (AB, ABD, Latin Amerika, Rusya, MENA) ve sosyal medya alert/publish katmanına genişlemek — ancak **şu anki geliştirme kapsamı sadece TR + bu 4 site + kozmetik kategorisi**. Genişleme, mimari bu 4 site ile uçtan uca stabil çalışana kadar başlamayacak.

Eveshop (2026-07-30), diğer 3'ü uçtan uca stabil olmadan eklendi — bilinçli bir istisna: Shopify altyapısı JSON API değil HTML kazıma gerektirdiği için mimarinin plugin sınırlarını (bkz. `core/parsers/html_parser.py`) erken test etmek amaçlandı. Yeni bir 5. site eklenmeden önce yine bu 4'ünün stabil çalıştığından emin olunmalı.


---

## Temel Mimari İlkeler

1. **Modüller bağımsız, gevşek bağlı (loosely coupled) olmalı.** `crawler/`, `core/`, `matching/`, `selfheal/` birbirini import etmez; sadece Postgres/MinIO üzerinden veri alışverişi yapar.
2. **Tamamen asenkron olmalı.** Bir modüldeki tıkanıklık (örn. bir sitenin API'si yavaşsa/kırıksa) diğer sitelerin/modüllerin işlemesini durdurmamalı. Airflow task'ları site bazlı paralelleştirilmeli, bir site fail olursa diğerleri devam etmeli. Kuyruk/worker mantığı (Celery executor veya benzeri) bu prensibe göre kurgulanmalı.
3. **Plugin mimarisi zorunlu.** Yeni site eklemek/çıkarmak = yeni bir dosya eklemek/kaldırmak. Çekirdek kodun hiçbir yerinde site adı hardcode edilmez. Bkz. `core/parsers/site_plugins/`.
4. **Ham veri (raw) her zaman önce arşivlenir, sonra işlenir.** robots.txt, sitemap, kategori JSON, ürün JSON — hepsi işlenmeden önce MinIO'ya ham haliyle yazılır. Transform/parse adımı arşivlemeden sonra gelir, asla önce değil.
5. **Tek doğruluk kaynağı (single source of truth) prensibi.** Aynı bilgi iki farklı yerde tutulmaz (örn. `base_url` sadece `configs/sites.yaml`'da, fetch-özel config sadece `configs/tr/{site}.yaml`'da).

---

## Klasör Yapısı

```
price-bot/
├── crawler/                    # (eski adı: discovery) — robots.txt + sitemap keşfi, SQLite
│   ├── db.py                   # SQLite bağlantı, WAL mode
│   ├── models.py                # Site, Sitemap, SitemapUrl
│   ├── robots_parser.py         # robots.txt fetch + Sitemap: + Disallow/Allow pattern çıkarma
│   ├── sitemap_parser.py        # sitemap fetch, gzip, sitemapindex recursion
│   ├── engine.py                 # orchestration: robots -> sitemap -> diff -> hash -> kaydet -> minio
│   ├── sync_to_postgres.py      # active_urls -> core.discovered_urls upsert
│   └── cli.py
│
├── core/                        # fetch + parse + Postgres yazım
│   ├── fetchers/                 # category_fetcher.py (tüm siteler), product_fetcher.py (opsiyonel — bkz. not)
│   ├── parsers/
│   │   ├── base_parser.py        # ortak interface + JSON/XML/HTML-regex pagination engine
│   │   ├── html_parser.py        # genel amaçlı: HTML'e gömülü JSON'u regex ile çıkarma (Shopify-tipi temalar için)
│   │   └── site_plugins/          # gratis.py, rossmann.py, watsons.py, eveshop.py — PLUGIN KATMANI
│   ├── storage.py                # MinIO client wrapper
│   └── db.py                     # Postgres bağlantı
│
├── matching/                    # DuckDB — bronze->silver ELT (eşleştirme YOK, bkz. Kapsam Dışı)
│   ├── duckdb_pipeline.py        # raw_{site} landing (MinIO -> DuckDB, incremental)
│   ├── normalize.py              # isim/birim normalizasyonu, clean_{site} -> silver_products
│   ├── sync_to_postgres.py       # silver_products -> Postgres pricing.silver_products (idempotent upsert)
│   └── analysis/                 # site başına column decisions.yaml + build_clean.py (raw -> clean)
│
├── selfheal/                    # AYRI modül — LLM tabanlı config onarımı
│   ├── failure_detector.py       # HTTP status / boş response / şema sapması tespiti
│   ├── context_builder.py        # curl -v + Playwright network capture + robots.txt disallow context
│   ├── providers/                 # gemini_provider.py, antigravity_cli_provider.py, nvidia_nim_provider.py
│   ├── validator.py               # önerilen config'i sandbox'ta test etme
│   └── engine.py                  # detector -> context -> provider zinciri -> validate -> kaydet
│
├── configs/
│   ├── sites.yaml                # MASTER site registry (name, base_url, country, enabled)
│   ├── tr/{site}.yaml             # fetch-özel config (endpoint template, header, parser mapping)
│   ├── schedule.yaml              # cron saatleri (TZ + price_pipeline/crawler_discovery times) - koda hardcode değil, bkz. scripts/install_cron.py
│   └── versions/{site}/           # self-heal geçmişi, hash'li (active.yaml + history/)
│
├── content/                      # BİLİNÇLİ KISMİ İSTİSNA (bkz. Kapsam Dışı) — sadece site-bazlı fiyat düşüşü alarmı
│   ├── alert_engine.py            # pricing.silver_products -> LEAST(koşulsuz,koşullu) efektif fiyat düşüşü; pricing.alerted_drops ile tekilleştirme, toplu-kampanya özeti
│   └── publishers/
│       └── telegram.py            # Telegram Bot API sendMessage (shared/http_client.py üzerinden)
│
├── db/                           # postgres_schema.sql, migrations/
├── dags/                         # Airflow DAG'ları (site bazlı paralel task'lar) — henüz kurulmadı, bkz. scripts/
├── scripts/                      # cron ile tetiklenen orkestrasyon - alt modülleri ASLA import etmez, sadece `python -m` subprocess ile çağırır
│   ├── run_price_pipeline.py     # category_fetcher -> duckdb_pipeline -> build_clean -> normalize -> sync_to_postgres -> alert_engine
│   ├── run_discovery.py          # crawler.cli sitemaps + sync (fiyat zincirinden ayrı, daha seyrek)
│   ├── install_cron.py           # configs/schedule.yaml -> crontab (idempotent, BEGIN/END işaretli blok)
│   └── _lock.py                  # aynı script'in üst üste binmesini önleyen PID-lock
├── shared/                       # hashing.py, http_client.py (retry/backoff), pg_client.py (Postgres havuzu), logging_config.py
├── .github/workflows/            # CI/CD (bkz. aşağıdaki bölüm)
└── tests/
```

---

## MinIO Klasör Yapısı (KESİN, DEĞİŞTİRİLEMEZ ŞEMA)

Kaybolmamak için bucket yapısı sıkı disiplinle korunmalı. Üst seviye ayrım **veri tipine** göre yapılır (`robots/`, `sitemaps/`, `category/`, `product/`), site adı bir alt seviyededir — böylece tek bir veri tipi tüm sitelerde tutarlı biçimde gözden geçirilebilir:

```
datalake/                      # bucket adı
  robots/{site}/{YYYY-MM-DD}/{sha256}.txt
  sitemaps/{site}/{YYYY-MM-DD}/{sha256}.xml
  category/{site}/{YYYY-MM-DD}/{category_id}_{category_name_slug}/{sha256}.json
  product/{site}/{YYYY-MM-DD}/{product_id}/{sha256}.json
```

Kurallar:
- Her obje için yanında bir `.meta.json` sidecar dosyası olmalı: `{fetch_timestamp, site, plugin_version, http_status, content_hash}`
- Dosya adı her zaman content hash (sha256) içermeli — aynı gün tekrar çekimde üzerine yazma, versiyon geçmişi korunsun
- robots.txt **de** ham olarak buraya yazılır (sadece sitemap değil) — çünkü endpoint/parametre yapısı hakkında ipucu içeriyor (bkz. Disallow/Allow pattern analizi)
- `category/` altında `{category_id}` tek başına siteye özel, anlamsız bir sayısal koddur — klasör adına okunabilir isim de eklenir (`core/storage.py`'deki `slugify`, Türkçe karakterleri sadeleştirir: "Ev & Yaşam" -> "ev_yasam"). Kanonik id->isim eşlemesi tek doğruluk kaynağı olarak `configs/tr/{site}.yaml` -> `main_categories`'te yaşar; MinIO'daki isim sadece okunabilirlik için bir kopyadır, oradan değiştirilmez.
- Yeni bir prefix/klasör seviyesi eklenecekse önce bu dosyada güncellenmeli, sonra kodda

---

## Hash / Değişiklik Tespiti

- Content hash almadan önce normalize et (whitespace/formatting farkı false-positive yaratmasın): XML/JSON'u parse edip URL listesini sort edip hash'le
- HTTP seviyesinde önce `If-Modified-Since`/`ETag` dene (en ucuz yol, MinIO'ya yazmadan atlanabilir)
- Hash aynıysa: MinIO'ya yeni obje yazma, sadece `last_checked_at` güncelle
- Hash farklıysa: yeni obje + DB'ye yeni versiyon kaydı + (sitemap için) URL diff'i çıkar (`is_active` flag ile soft-delete, silme)

---

## robots.txt Disallow/Allow — Risk Farkındalığı

`robots_parser.py` sadece `Sitemap:` değil, `Disallow`/`Allow` satırlarını da parse edip saklar (endpoint/parametre yapısı ipucu için). Ancak:
- robots.txt hukuken bağlayıcı değil ama davranışsal bot-tespit sinyali güçlü
- Disallow edilen path'lere crawl **atılmaz** — sadece context/öğrenme amaçlı saklanır
- `is_disallowed(url_path, disallow_patterns)` kontrolü her yeni endpoint keşfinde otomatik çalışmalı, sonuç loglanmalı
- Ticari kullanım söz konusu olduğu unutulmamalı — ToS kontrolü site bazında insan tarafından yapılmalı, otomatik karar verilmez

---

## Self-Healing Akışı (selfheal/)

```
1. failure_detector: mekanik ayrım yap (4xx/5xx=config sorunu, timeout=network/rate-limit — LLM'e gitme)
2. context_builder: eski cfg.yaml + curl -v + robots.txt disallow pattern + Playwright network capture (yeni endpoint adayları)
3. provider zinciri (öncelik sırası):
   0. antigravity_cli_provider (abonelik, headless: agy -p "..." --headless --approve none --output-format json, auth: ANTIGRAVITY_TOKEN)
   1. gemini_provider (free tier, quota sınırlı)
   2. nvidia_nim_provider (fallback)
4. validator: önerilen config sandbox'ta test edilir (şema doğrulama, ürün/kategori sayısı mantıklı aralıkta mı)
5. Geçerse: configs/versions/{site}/history/{hash}.yaml + active.yaml güncelle, meta.json'a diagnosis/confidence/source yaz
6. Geçmezse: max 3 retry, sonra insan onayına düş (eski config korunur)
```

Playwright, her failure'da değil sadece `failure_detector` "config/şema sorunu" tespit ettiğinde tetiklenir (ağır işlem, gereksiz çağrıyı engelle).

Provider'lar arası confidence skorları birbirine göre kalibre değil — sadece "insan onayına düşsün mü" eşiği için kullanılır, provider'lar arası kıyaslanmaz.

---

## CI/CD (Ücretsiz Katmanlar, Öğrenme + Esneklik Amaçlı)

GitHub Actions (public/private repo'da ücretsiz dakika kotası) önerilen başlangıç noktası:

```
.github/workflows/
  test.yml          # her PR'da: pytest (tests/crawler, tests/core, tests/matching, tests/selfheal)
  lint.yml           # ruff/black formatting kontrolü
  config-validate.yml # configs/*.yaml şema doğrulama (yanlış config main'e girmesin)
```

İlkeler:
- Airflow DAG'ları CI'da **çalıştırılmaz** (o production/home lab işi), CI sadece kod kalitesi + config şema doğrulama + unit test içindir
- Secrets (ANTIGRAVITY_TOKEN, GEMINI_API_KEY, POSTGRES_URL, MinIO credentials) GitHub Actions Secrets'ta tutulur, asla repo'da düz metin olmaz
- Self-hosted runner (Ubuntu home lab sunucusu) ileride entegrasyon testleri için düşünülebilir ama şimdilik GitHub-hosted free runner yeterli
- Bu proje CI/CD'yi hem öğrenme hem de build sürecini esnek tutmak için kullanıyor — production deploy otomasyonu şu aşamada hedef değil, sadece kod kalite kapısı

---

## Airflow DAG Yapısı

Site bazlı paralellik + modüller arası bağımsızlık:

```
dags/
  crawler_dag.py     # robots -> sitemap -> sync (her site ayrı task, biri fail olursa diğerleri etkilenmez)
  fetch_dag.py         # category_fetch -> product_fetch -> raw_store (site bazlı paralel branch)
  matching_dag.py      # duckdb transform (raw -> clean -> silver)
  selfheal_dag.py       # ayrı, failure_detector tetiklendiğinde çalışan bağımsız DAG
```

Pool kullan (`llm_diagnosis_pool`, slot=2-3) — LLM çağrılarının eş zamanlı sayısını Airflow seviyesinde sınırla, quota patlamasını kod içinde değil orkestrasyon seviyesinde önle.

---

## Postgres Senkronizasyonu (matching/sync_to_postgres.py)

`silver_products` (DuckDB, `matching/pricebot.duckdb`) sunucuda üretimde sorgulanabilir/kalıcı olması için Postgres'teki `pricing.silver_products` tablosuna senkronize edilir. Bu, siteler arası ürün eşleştirme DEĞİL (bkz. Şu An Kapsam Dışı) — sadece silver katmanının site-bazlı, eşleştirilmemiş bir kopyasının Postgres'te durması. Şema adı bilinçli olarak `pricing` (`matching` DEĞİL) — `core` şeması da "crawler modülü" değil konu başlığı bazlı adlandırılmıştı (`core.sites`/`core.discovered_urls`), aynı konvansiyon burada da uygulanıyor; ayrıca "matching" adı artık kapsam dışı bırakılan siteler-arası eşleştirmeyle karıştırılmasın diye 2026-08-08'de `matching` şemasından buraya taşındı (veri kopyalanmadı, sadece `ALTER TABLE ... SET SCHEMA`). Doğal anahtar `silver_id` (`site_code + source_product_id + fetch_date`'in md5'i) üzerinden `INSERT ... ON CONFLICT DO UPDATE` ile idempotent yazılır (`crawler/sync_to_postgres.py`'nin `core.discovered_urls` için kullandığı aynı desen) — ayrı bir "senkronize edildi" takip tablosu yok, doğruluk Postgres'in `PRIMARY KEY` kısıtından gelir. Postgres bağlantı havuzu `shared/pg_client.py`'de merkezi: `crawler/`, `matching/` ve `content/` birbirini import edemediği için (mimari ilke 1) ihtiyaç duyan üç modülün de kullandığı bir kaynak istemcisi `shared/`'a taşınır — `shared/storage.py`'nin MinIO için yaptığının aynısı.

---

## Zamanlanmış Çalıştırma (scripts/ + cron)

Airflow henüz kurulmadı (`dags/*.py` hâlâ boş stub, tam kurulumu ayrı/büyük bir iş) — bunun yerine `scripts/run_price_pipeline.py` (fiyat zinciri, günde `configs/schedule.yaml` -> `price_pipeline.times` kadar) ve `scripts/run_discovery.py` (robots/sitemap keşfi, ayrı ve daha seyrek — `crawler_discovery.times`) cron ile tetikleniyor. `scripts/install_cron.py` bu config'ten idempotent bir crontab bloğu üretir (saatler koda hardcode değil, `schedule.yaml` değişip script yeniden çalıştırılınca crontab da güncellenir). Her orkestratör alt modülleri **import etmez** (mimari ilke 1) — her aşamayı ayrı bir `python -m <modül>` subprocess'i olarak çalıştırır, bir aşama başarısız olsa bile bir sonrakine geçilir. `scripts/_lock.py` bir PID-lock ile aynı script'in üst üste binmesini önler (bir çalıştırma normalden uzun sürerse bir sonraki tetikleme sessizce atlanır).

**Site izolasyonu (kritik, cron'la günde 4 kez otomatik çalışacağı için önemi arttı):** `crawler/engine.py`, `core/fetchers/category_fetcher.py`, `matching/duckdb_pipeline.py` zaten per-site izole (`asyncio.gather(..., return_exceptions=True)` veya per-site try/except+continue) — bir sitenin bot/WAF/parse hatası diğer 3 siteyi etkilemez. `matching/normalize.py`/`matching/analysis/build_clean.py` bu izolasyona 2026-08-10'da kavuştu (öncesinde bir sitenin hatası TÜM siteleri o çalıştırmada silver/clean'den mahrum bırakıyordu — düzeltildi, artık aynı log+continue deseni).

**Anti-bot:** `rate_limit.delay_seconds` (config, site başına) artık `core/parsers/base_parser.py` ve `core/fetchers/category_fetcher.py`'de ±%20 jitter'lı uygulanıyor — sabit/deterministik gecikme otomasyon imzası olarak algılanabilir. Konfigürasyon şeması değişmedi, `delay_seconds` hâlâ "ortalama" gecikme.

**Dockerize edilmiş dağıtım (2026-08-10):** `docker-compose.yml`'deki `app` servisi (`Dockerfile` + `docker/entrypoint.sh`) `postgres`+`minio`'nun yanına eklendi - container başlarken `scripts/install_cron.py`'yi çalıştırıp `cron -f`'i foreground'da tutuyor, host'ta ayrı bir Python/venv/cron kurulumuna gerek kalmıyor. `network_mode: host` kullanılıyor (`.env`'deki `localhost` referansları dev makine ile sunucu arasında DEĞİŞMEDEN taşınabilsin diye - Docker'ın kendi servis-adı ağı ayrı bir `.env` gerektirirdi). Tüm proje dizini bind-mount (`.:/app`) - `git pull` sonrası `docker compose restart app` yeterli, `matching/pricebot.duckdb`/`logs/` host'ta kalıcı. Sunucuya taşıma: `git clone/pull` + `.env`'i kopyala + `docker compose up -d --build`. **Host'ta ayrıca `scripts/install_cron.py` çalıştırılmışsa crontab'ı temizleyin** (`crontab -r` veya BEGIN/END bloğunu kaldırın) - hem host hem container cron kurarsa pipeline günde 8 kez tetiklenir.

---

## Şu An Kapsam Dışı (İleride, Şimdi Değil)

- Siteler arası ürün eşleştirme (`canonical_products`/`product_matches`, EAN + fuzzy matching, review queue) — 2026-08-07 itibariyle bilinçli olarak kapsam dışı. Sistem sunucuda 1-2 hafta stabil çalıştıktan sonra yeniden ele alınacak. `matching/` şu an sadece bronze->silver ELT'yi kapsıyor, siteler arası eşleştirme yok.
- `content/`'in TAM vizyonu (siteler arası canonical alert, Instagram/Twitter, Veo görsel üretimi) — ürün eşleştirme kapsam dışı kaldığı sürece bu da başlamayacak. **BİLİNÇLİ KISMİ İSTİSNA (2026-08-07):** site-bazlı (cross-site DEĞİL) fiyat düşüşü alarmı eşleştirme gerektirmediği için öne çekildi — Eveshop'un HTML-scraping istisnasıyla aynı desen (bkz. Proje Özeti). Sadece `content/alert_engine.py` + `content/publishers/telegram.py`, Postgres `pricing.silver_products`'ı okuyor (hem koşulsuz hem koşullu/kart fiyatını, görsel dahil), `crawler/`/`core/`/`matching/` hiçbirini import etmiyor.
- Çoklu coğrafya (`configs/eu/`, `configs/us/`, vs.) — sadece 3 TR sitesi uçtan uca stabil çalıştıktan sonra
- DuckDB dışında ek analitik katman — Postgres veri hacmi gerçekten sorun yaratmadan eklenmeyecek

---

## Kod Yazarken Dikkat Edilecekler

- Her yeni site: sadece `configs/sites.yaml` + `configs/tr/{site}.yaml` + `core/parsers/site_plugins/{site}.py` — çekirdek kodda site adı hardcode edilmemeli. İstisna: `base_parser.py`/`storage.py`'a YENİ, config'ten okunan, site-agnostik bir yetenek eklemek (örn. Eveshop için eklenen `format: html_regex_json`, `zero_indexed` pagination, `archive.extension`) plugin mimarisini ihlal etmez — ölçüt "site adı if/else olarak mı geçiyor" (yasak) vs "yeni bir config anahtarı mı yorumlanıyor" (serbest)
- `product_fetcher.py` her site için zorunlu değil: sadece kategori taraması EAN/barkodun TAMAMINI vermiyorsa (örn. barkod ayrı bir ürün sayfasında/varyantında) devreye girer. Eveshop'ta kategori sayfası (bkz. `configs/tr/eveshop.yaml` başındaki not) hem fiyatı hem barkodu tek istekte verdiği ve ürünler tek-varyantlı olduğu için bilerek kullanılmadı — yeni bir site eklenirken önce bu kontrol yapılmalı, doğrudan product_fetcher'a el atılmamalı
- HTTP istekleri her zaman `shared/http_client.py` üzerinden (ortak retry/backoff), doğrudan `requests`/`httpx` çağrısı yapılmamalı
- Fiyat parse ederken site-özel format farklarına dikkat (string "129,90 TL" vs kuruş cinsinden integer vs float) — bu mantık sadece ilgili `site_plugins/{site}.py` içinde kalır
- Yeni bir MinIO prefix/klasör seviyesi eklenecekse önce bu dosya güncellenir
