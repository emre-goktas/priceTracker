# Price Tracking Bot - System Architecture

## 1. Overview
This system is a modular price tracking and anomaly detection pipeline designed to monitor multiple e-commerce platforms, detect significant price drops, and send alerts (e.g., Telegram).

Core idea:
> Multi-source scraping → normalization → storage → analytics → alerting

---

## 2. High-Level Architecture

```
                +-------------------+
                |   Scheduler       |
                +---------+---------+
                          |
                          v
                +-------------------+
                | Scraper Manager   |
                +---------+---------+
                          |
        +-----------------+------------------+
        |                 |                  |
        v                 v                  v
  Vatan Scraper    Amazon Scraper     Other Scrapers
        |                 |                  |
        +-----------------+------------------+
                          |
                          v
                +-------------------+
                |  Normalizer       |
                +---------+---------+
                          |
                          v
                +-------------------+
                |  Storage (DB)     |
                +---------+---------+
                          |
                          v
                +-------------------+
                | Analytics Engine  |
                +---------+---------+
                          |
                          v
                +-------------------+
                | Alert Engine      |
                +-------------------+
```

---

## 3. Core Modules

## 3.1 Scheduler
Responsible for triggering scraping jobs periodically.

- Runs every X minutes (e.g. 5-10 min)
- Can be implemented using:
  - cron
  - APScheduler
  - Celery Beat

---

## 3.2 Scraper Manager
Coordinates all scrapers.

Responsibilities:
- Load enabled sources
- Run scrapers in parallel
- Collect raw product data

Interface:
```python
class BaseScraper:
    def scrape(self) -> list[dict]:
        pass
```

Output format:
```json
{
  "name": "Kingston 16GB DDR4",
  "price": 1499.90,
  "url": "https://...",
  "source": "vatan"
}
```

---

## 3.3 Scrapers (Plugins)
Each site is an independent module.

Structure:
```
scrapers/
  vatan.py
  amazon.py
  teknosa.py
  mediamarkt.py
  trendyol.py
```

Key principle:
> Each scraper is replaceable and independent

---

## 3.4 Normalizer
Transforms raw scraped data into consistent format.

Responsibilities:
- Clean product names
- Extract model identifiers
- Standardize price format
- Deduplicate products

Example:
```python
def normalize(product):
    return {
        "id": hash(product["url"]),
        "name": clean(product["name"]),
        "price": float(product["price"]),
        "url": product["url"],
        "source": product["source"]
    }
```

---

## 3.5 Storage Layer (Database)

### Tables

#### products (static metadata)
- id (PK)
- name
- url
- source
- category

#### price_history (time series)
- id (PK)
- product_id (FK)
- price
- timestamp

Design principle:
> Keep product metadata separate from price history

---

## 3.6 Analytics Engine
Detects price anomalies.

### Simple Rule-Based Version
```python
if new_price < avg_price * 0.8:
    trigger_alert()
```

### Statistical Version
```python
z = (price - mean) / std
if z < -2:
    trigger_alert()
```

### Advanced options (future):
- Moving averages
- Trend detection
- Seasonal adjustment
- ML-based anomaly detection

---

## 3.7 Alert Engine
Sends notifications when anomaly detected.

Supported channels:
- Telegram Bot (primary)
- Discord webhook (optional)
- Email (optional)

Example payload:
```json
{
  "title": "Price Drop Alert",
  "product": "Kingston 16GB RAM",
  "old_price": 14999,
  "new_price": 9999,
  "url": "..."
}
```

---

## 4. Category System (Dynamic Product Targeting)

Instead of hardcoding products, use categories.

Example:
```json
{
  "category": "ram",
  "keywords": ["DDR4", "DDR5", "16GB", "32GB"],
  "sites": ["vatan", "amazon", "teknosa"]
}
```

This allows:
- RAM today
- GPU tomorrow
- Air fryer next week

---

## 5. Data Flow Pipeline

```
Scheduler
  ↓
Scraper Manager
  ↓
Parallel Scrapers
  ↓
Normalizer
  ↓
Database Write (price_history)
  ↓
Analytics Engine (compare historical prices)
  ↓
If anomaly → Alert Engine
```

---

## 6. Scaling Strategy

### Phase 1 (MVP)
- 1–2 sites
- 1 category
- simple threshold alerts

### Phase 2
- 5+ sites
- multiple categories
- improved anomaly detection

### Phase 3
- distributed scraping
- queue system (RabbitMQ/Kafka)
- ML anomaly detection

---

## 7. Handling Real-World Issues

### 7.1 DOM Changes
Solution:
- each scraper isolated
- fallback selectors
- monitoring scraper failures

---

### 7.2 Rate Limits / Blocking
- randomized delays
- rotating headers
- proxy support (optional)

---

### 7.3 Duplicate Products
- URL hashing
- fuzzy matching on product name

---

## 8. Tech Stack Suggestion

- Python (core)
- BeautifulSoup / Playwright (scraping)
- PostgreSQL (DB)
- Redis (optional caching)
- APScheduler (job scheduling)
- Telegram Bot API

---

## 9. Key Design Principles

- Modular (plugin-based scrapers)
- Stateless scraping layer
- Time-series optimized storage
- Event-driven alerts
- Easy category expansion

---

## 10. Future Enhancements

- AI-based product matching
- Price prediction
- Chrome extension for instant alerts
- Web dashboard
- User-defined watchlists

---

## 11. Summary

This system is not just a scraper.
It is a:

> **real-time price intelligence engine**

capable of scaling into a full product analytics platform.

---

## 12. Key Risks (VERY IMPORTANT)

### 12.1 Legal / Terms of Service Risk
- Some e-commerce sites explicitly forbid scraping
- Risk: IP blocking or legal warnings

Mitigation:
- Respect robots.txt where applicable
- Low-frequency scraping
- Prefer public pages only

---

### 12.2 IP Ban / Rate Limit Risk
- Too many requests → IP ban
- Especially Amazon / large retailers

Mitigation:
- Randomized delays (jitter)
- Request throttling per domain
- Proxy rotation (advanced)

---

### 12.3 CAPTCHA / Bot Detection
- Sites may trigger CAPTCHA or JS challenges

Mitigation:
- Use Playwright (real browser automation)
- Headless stealth mode
- Session reuse (cookies)

---

### 12.4 DOM Structure Changes (CRITICAL FAILURE POINT)
- Websites frequently change HTML structure
- Scrapers break silently

Mitigation:
- Multiple fallback selectors per field
- Avoid brittle CSS chains
- Use semantic anchors (product cards, aria labels)
- Monitoring system for scraper failure alerts

---

### 12.5 Data Mismatch / Wrong Product Mapping
- Same product appears with different names
- Risk of duplicate or wrong comparisons

Mitigation:
- URL-based ID hashing (primary key)
- Fuzzy name matching (optional)
- Model number extraction (best signal)

---

### 13. Anti-Bot / Anti-Ban Strategy

Core techniques:

- Request rate limiting per domain
- Randomized user-agent rotation
- Header spoofing (Accept-Language, etc.)
- Proxy pool (rotating IPs)
- Retry with exponential backoff
- Session persistence (cookies)

Advanced (optional):
- Playwright stealth mode
- Human-like timing simulation

---

## 14. DOM Resilience Strategy

Goal:
> Make scrapers survive UI changes

### Strategy 1: Multi-Selector Fallbacks
```python
price_selectors = [
    ".price",
    "span[data-price]",
    ".product-price",
]
```

### Strategy 2: Structural scraping (preferred)
- scrape product card containers
- extract fields relative to container

### Strategy 3: Schema-based extraction
- define expected product schema
- validate scraped output

### Strategy 4: Monitoring layer
- if scrape count drops → alert
- if price extraction fails → fallback mode

---

## 15. Recommended Tooling Stack (Best-in-class)

### Scraping
- Playwright (modern JS-heavy sites)
- BeautifulSoup4 (simple parsing)
- lxml (fast parsing)
- httpx (fast HTTP client)
- scrapy (scalable crawling framework)

### Anti-bot / stealth
- undetected-chromedriver
- playwright-stealth (plugin)

### Data & Backend
- PostgreSQL (primary DB)
- Redis (cache / queue buffer)
- SQLAlchemy or Prisma-like ORM

### Pipeline / Orchestration
- Celery (distributed tasks)
- Redis queue
- APScheduler (simple MVP scheduling)
- Kafka (scale phase)

### Data validation
- Pydantic (schema enforcement)

---

## 17. Critical Functional Omissions (Product Leaks)

### 17.1 Stock Status Awareness (CRITICAL)
A price drop is irrelevant if the item is out of stock. 
- **Required Change:** Scrapers must extract `is_in_stock` (boolean).
- **Logic:** The Alert Engine must filter out price drops for unavailable items.

### 17.2 Shipping & Hidden Costs
- **Leak:** A "cheaper" price might be more expensive after shipping.
- **Requirement:** Normalizer should attempt to extract shipping fees or flag "Free Shipping" eligibility.

### 17.3 Global Product Identity (GPID)
- **Problem:** URL hashing only tracks one product on one site.
- **Solution:** A cross-site matching layer using:
  - SKU / Model Number extraction (Highest priority).
  - EAN/UPC barcodes (if available in metadata).
  - Normalized name fuzzy matching.
- **Goal:** Allow "Price Comparison" (Site A vs Site B) rather than just "Price History" (Site A over time).

---

## 18. Operational Observability & Health

### 18.1 Scraper Heartbeats
Scrapers fail silently when DOMs change.
- **Metric:** "Last Successful Scrape" per source.
- **Alert:** Notify developers if a scraper returns 0 products for > 3 consecutive runs.

### 18.2 Price Sanity "Circuit Breaker"
- **Risk:** A scraper error (e.g., picking up a "Quantity" field instead of "Price") triggers a false mass-alert.
- **Logic:** If `new_price < old_price * 0.2` (80% drop), flag for manual review or secondary verification before alerting.

---

## 19. Data Retention & Lifecycle Management

### 19.1 Time-Series Compression
- **Problem:** 5-minute scraping across 10,000 products will explode the DB size.
- **Strategy:** 
  - Keep 1-minute/5-minute data for 7 days.
  - Aggregate into "Daily Min/Max/Avg" after 30 days.
  - Prune raw logs after 90 days.

---

## 20. Advanced Risks & Mitigation (The "Boss" View)

| Risk | Impact | Mitigation |
| :--- | :--- | :--- |
| **Scraper Fatigue** | High | Use a "Low-Code" or "Schema-Driven" scraper definition to allow quick fixes without redeploying the whole core. |
| **Proxy Cost Burn** | Medium | Implement "Smart Throttling": Scrape popular/high-volatility categories more often than stable ones. |
| **Legal/TOS Pivot** | High | Maintain a "Human-in-the-loop" toggle to pause scrapers immediately if a Cease & Desist is received. |
| **Data Poisoning** | Low | Implement "Source Trust Scores": If a small site shows a suspicious price, verify against a "Major" (Amazon/Vatan) before alerting. |

---

## 21. Conclusion: The Path to Production

The system is currently a **Scraper**. To become a **Product**, it must transition from "data collection" to "data intelligence" by implementing the Stock Awareness and GPID Matching layers defined in these final sections.


-----------

04_PriceTracker/
├── main.py                        ← Giriş noktası (DB init + scheduler başlatma)
├── requirements.txt               ← Tüm bağımlılıklar
├── .env.example                   ← Ortam değişkeni şablonu
├── .gitignore
│
├── scrapers/                      ← Plugin tabanlı scraper katmanı
│   ├── base.py                    ← BaseScraper ABC
│   ├── manager.py                 ← Paralel çalıştırma (ThreadPoolExecutor)
│   ├── vatan.py / amazon.py / teknosa.py / mediamarkt.py / trendyol.py
│
├── normalizer/
│   └── normalizer.py              ← Pydantic schema, URL hashing, isim temizleme
│
├── storage/
│   ├── models.py                  ← SQLAlchemy ORM: products + price_history tabloları
│   └── database.py                ← Repository pattern (upsert, price kaydetme)
│
├── analytics/
│   └── engine.py                  ← Threshold + Z-score stratejisi, circuit breaker
│
├── alerts/
│   ├── base.py                    ← BaseAlertChannel ABC
│   └── telegram.py                ← Telegram Bot API entegrasyonu
│
├── scheduler/
│   └── job.py                     ← APScheduler ile tam pipeline orkestrasyon
│
├── config/
│   ├── settings.py                ← pydantic-settings (.env desteği)
│   └── categories.py             ← Dinamik kategori tanımları
│
├── tests/
│   └── unit/
│       ├── test_normalizer.py
│       └── test_analytics.py      ← Circuit breaker testi dahil
│
├── docs/ / logs/                  ← Boş klasörler
