# price-bot — Proje Talimatları

## Proje Özeti

Türk e-ticaret kozmetik sitelerinden (başlangıçta Gratis, Rossmann, Watsons) fiyat verisi toplayan, ürünleri EAN/GTIN + fuzzy matching ile eşleştiren, kendi kendini onarabilen (self-healing) bir fiyat takip sistemi. Uzun vadeli hedef: çoklu coğrafyaya (AB, ABD, Latin Amerika, Rusya, MENA) ve sosyal medya alert/publish katmanına genişlemek — ancak **şu anki geliştirme kapsamı sadece TR + 3 site + kozmetik kategorisi**. Genişleme, mimari bu 3 site ile uçtan uca stabil çalışana kadar başlamayacak.


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
│   ├── fetchers/                 # category_fetcher.py, product_fetcher.py
│   ├── parsers/
│   │   ├── base_parser.py        # ortak interface (BaseSiteParser)
│   │   └── site_plugins/          # gratis.py, rossmann.py, watsons.py — PLUGIN KATMANI
│   ├── storage.py                # MinIO client wrapper
│   └── db.py                     # Postgres bağlantı
│
├── matching/                    # DuckDB — ürün eşleştirme (ELT)
│   ├── duckdb_pipeline.py
│   ├── normalize.py              # isim temizleme, ml/gr birim standardizasyonu
│   ├── ean_match.py              # GTIN/EAN tam eşleşme (öncelikli yol)
│   ├── fuzzy_match.py            # EAN yoksa fallback (Levenshtein/Jaro-Winkler)
│   ├── sanity_checks.py          # aynı EAN'de anormal fiyat farkı tespiti
│   ├── review_queue.py           # düşük confidence eşleşmeler -> manuel onay
│   └── load_to_postgres.py       # canonical_products / product_matches nihai yazım
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
│   └── versions/{site}/           # self-heal geçmişi, hash'li (active.yaml + history/)
│
├── db/                           # postgres_schema.sql, migrations/
├── dags/                         # Airflow DAG'ları (site bazlı paralel task'lar)
├── shared/                       # hashing.py, http_client.py (retry/backoff), logging_config.py
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
  category/{site}/{YYYY-MM-DD}/{category_id}/{sha256}.json
  product/{site}/{YYYY-MM-DD}/{product_id}/{sha256}.json
```

Kurallar:
- Her obje için yanında bir `.meta.json` sidecar dosyası olmalı: `{fetch_timestamp, site, plugin_version, http_status, content_hash}`
- Dosya adı her zaman content hash (sha256) içermeli — aynı gün tekrar çekimde üzerine yazma, versiyon geçmişi korunsun
- robots.txt **de** ham olarak buraya yazılır (sadece sitemap değil) — çünkü endpoint/parametre yapısı hakkında ipucu içeriyor (bkz. Disallow/Allow pattern analizi)
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

## Ürün Eşleştirme Mantığı (matching/)

Öncelik sırası:
1. **EAN/GTIN tam eşleşme** — varsa direkt `canonical_product_id` ata (confidence: 100)
2. **EAN yoksa** → fuzzy matching fallback: marka+isim normalize (lowercase, ml/gr birim standardize, varyant/ton ayrımı dikkat) + similarity skor
3. Skor eşik altındaysa → `review_queue`'ya düş, otomatik onaylama

Sanity check: Aynı EAN'e sahip ürünler arası fiyat 5-10x fark gösteriyorsa → EAN hatası şüphesi, otomatik onaylama, review_queue'ya düşür.

Kozmetikte ton/renk varyantı = ayrı SKU mu, ürün ailesi mi sorusu her kategori/marka için tutarlı karar verilmeli, şema sonradan değiştirilmemeli.

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
  matching_dag.py      # duckdb transform -> canonical load
  selfheal_dag.py       # ayrı, failure_detector tetiklendiğinde çalışan bağımsız DAG
```

Pool kullan (`llm_diagnosis_pool`, slot=2-3) — LLM çağrılarının eş zamanlı sayısını Airflow seviyesinde sınırla, quota patlamasını kod içinde değil orkestrasyon seviyesinde önle.

---

## Şu An Kapsam Dışı (İleride, Şimdi Değil)

- `content/` (alert_engine, Veo görsel üretimi, publishers: Telegram/Instagram/Twitter) — `matching/` çıktısı stabilleşmeden başlanmayacak
- Çoklu coğrafya (`configs/eu/`, `configs/us/`, vs.) — sadece 3 TR sitesi uçtan uca stabil çalıştıktan sonra
- DuckDB dışında ek analitik katman — Postgres veri hacmi gerçekten sorun yaratmadan eklenmeyecek

---

## Kod Yazarken Dikkat Edilecekler

- Her yeni site: sadece `configs/sites.yaml` + `configs/tr/{site}.yaml` + `core/parsers/site_plugins/{site}.py` — çekirdek kodda değişiklik olmamalı
- HTTP istekleri her zaman `shared/http_client.py` üzerinden (ortak retry/backoff), doğrudan `requests`/`httpx` çağrısı yapılmamalı
- Fiyat parse ederken site-özel format farklarına dikkat (string "129,90 TL" vs kuruş cinsinden integer vs float) — bu mantık sadece ilgili `site_plugins/{site}.py` içinde kalır
- Yeni bir MinIO prefix/klasör seviyesi eklenecekse önce bu dosya güncellenir
