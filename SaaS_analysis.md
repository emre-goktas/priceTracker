# Production SaaS Architectural Analysis: priceTracker

## 1. System Overview
**What kind of system is this?**
Currently, this is an advanced, modular price-tracking script/bot. It is built around a localized monolithic architecture where scheduling, scraping, normalization, database writes, and alerting all happen within a single Python process.

**Current maturity level:**
**Prototype / MVP (Not Production-Ready).**
While the code is well-structured using OOP principles, interface classes, and modern async Python, it severely lacks the distributed infrastructure required for a scalable SaaS. It runs in an in-memory event loop (`APScheduler` + `asyncio.gather`), making it a single point of failure and fundamentally unsuited for horizontal scaling.

---

## 2. Critical Risks (Top 10)
1. **Single Point of Failure (Monolith):** The entire pipeline runs in `main.py`. If a single Playwright instance crashes, memory leaks, or hangs indefinitely, the entire system stops.
2. **Synchronous Blocking Operations in Async Loop:** The `TelegramAlerter` uses `requests.post()` instead of an async client like `httpx`. A slow API response from Telegram will block the entire asyncio event loop, pausing all scrapers.
3. **Playwright Resource Hogging:** Launching a full headless browser (Chromium) for every scrape is massively inefficient. Browsers are memory-hungry; scaling this out will quickly lead to Out-Of-Memory (OOM) crashes.
4. **URL-Based Identity (Brittle):** Product IDs are created via `hash(URL)`. If a site adds tracking parameters (e.g., `?utm_source=xyz`), the system treats it as a brand new product, duplicating data and splitting the price history.
5. **No IP/Proxy Rotation:** Relying solely on headers, user-agents, and random delays from a single server IP guarantees an eventual IP ban from Cloudflare/Akamai, effectively blinding the scraper.
6. **SQLite Write Bottleneck:** Even with WAL mode enabled, SQLite is not designed for the highly concurrent, high-throughput writes needed to log thousands of prices every 5 minutes. Database locking will become a massive bottleneck.
7. **Scraping Silent Failures:** If a website undergoes a complete DOM overhaul, the scraper returns `0` products. While it logs a warning, there is no automatic retry, fallback, or quarantine mechanism to prevent bad data downstream.
8. **Fragile Price Parsing:** Extracting prices using string manipulation (`replace(".", "").replace(",", ".")`) is highly vulnerable to unpredicted localization changes or edge cases, leading to massive false-positive price drop alerts.
9. **No Data Lifecycle Management:** `price_history` appends rows indefinitely. At a 5-minute interval across many products, the database will experience explosive bloat within days, degrading query performance.
10. **Absence of Tenant Isolation:** The system has no concept of users, tenants, or individual watchlists. It blasts all alerts to a single Telegram channel, making personalized SaaS offerings impossible.

---

## 3. Scalability Analysis
*(Assumption: Scraping happens aggressively every 5 minutes)*

* **What breaks at 1k products?**
  * **Memory & Timing:** The single server will struggle with memory spikes from Playwright. With random delays (e.g., 2-6 seconds) between page loads, processing 1k products across multiple sites might exceed the 5-minute window, causing overlapping scheduler executions and eventual crashes.
* **What breaks at 10k products?**
  * **Complete System Collapse:** It is mathematically impossible to scrape 10k products sequentially (or with limited concurrency) in 5 minutes using browser automation on a single machine. The SQLite database will lock, the server will OOM, and the IP will be permanently blacklisted.
* **What breaks at 100k products?**
  * **Architectural Wall:** The current design cannot support this. You would require a distributed worker fleet (dozens of servers), a massive residential proxy pool, message queues (RabbitMQ/Kafka), and a shift away from Playwright to lightweight API/HTTP scraping.

---

## 4. Data Pipeline Evaluation
* **Is data reliable?**
  * **Low Reliability.** Data integrity is highly susceptible to superficial web changes.
* **Where can data corruption happen?**
  * **Stock Status Errors:** Relying on negative keywords (e.g., "Tükendi") means if the site changes the text to "Stokta Yok", the product is incorrectly marked as in-stock.
  * **Duplicate Ghost Products:** URL variations create fragmented, disconnected price histories for the exact same item.
  * **False Positives:** A scraper pulling a "bundle price" or "installment price" instead of the main price will trigger massive false price drop alerts.

---

## 5. Scraping Layer Analysis
* **Fragility to DOM changes:** High. Although there are multi-selector fallbacks, they are hardcoded CSS paths. Modern SPAs change class names dynamically (e.g., `styled-components`).
* **Anti-bot risks:** Extremely high. `playwright-stealth` is a band-aid. True anti-bot systems (Cloudflare, Datadome) look at IP reputation and TLS fingerprinting. A datacenter IP will be flagged regardless of stealth mode.
* **Improvements:**
  1. **API Interception:** Reverse-engineer the sites and fetch the raw JSON from their internal XHR/GraphQL endpoints. This is 100x faster and more stable than DOM parsing.
  2. **JSON-LD/Schema Org:** Extract structured metadata (`<script type="application/ld+json">`) from the page rather than parsing HTML elements.

---

## 6. State Management
* **Is product tracking reliable over time?** No, due to the URL-hashing primary key strategy.
* **Missing concepts:**
  * **Global Product Identity (GPID) / SKU Mapping:** There is no cross-site entity resolution. An "iPhone 15 128GB" on Vatan and the same phone on Hepsiburada exist in alternate universes. To build a SaaS, users want to track a *product*, not a specific *URL*.
  * **Job State & Retries:** There is no persistence for the scraping queue. If the script restarts, any ongoing scrapes are lost.

---

## 7. Performance & Cost
* **What will make this expensive?**
  * **Compute Costs (Playwright):** Running headless browsers requires heavy RAM and CPU. Scaling to thousands of targets means provisioning expensive compute nodes.
  * **Proxy Costs:** To avoid bans, you will need a residential proxy network (e.g., BrightData), which charges per GB. Playwright downloads images, CSS, and JS, burning through expensive bandwidth rapidly.
* **How to optimize?**
  * Block images, media, and fonts in Playwright aggressively.
  * Migrate 90% of scraping to pure HTTP requests (`httpx` + `BeautifulSoup`) and only fall back to Playwright when a challenge is detected.

---

## 8. SaaS Readiness
**What is missing to turn this into a paid SaaS?**
* **User Management & Auth:** No authentication, user accounts, or tenant separation.
* **Watchlists & Subscriptions:** Users need to input their own URLs or search queries and set personalized target thresholds (e.g., "Alert me when it drops below 10,000 TL").
* **Payment Gateway:** Stripe or Paddle integration for tiered access.
* **API/Dashboard:** A web interface (React/Next.js) powered by a robust REST/GraphQL API (FastAPI) to view historical charts.
* **Multi-Channel Delivery:** Webhooks, Push Notifications, Discord bots, and SMS, rather than a hardcoded single Telegram channel.

---

## 9. Refactoring Plan
*(Tailored for a Solo Dev or Small Team)*

**Phase 1: Stabilization (Weeks 1-2)**
* Replace `requests` in `telegram.py` with `httpx` to unblock the async loop.
* Swap SQLite for PostgreSQL (already in `requirements.txt`) and run it via Docker.
* Implement URL canonicalization (stripping `?utm=`, `#frag`, etc.) before hashing.

**Phase 2: Decoupling & Queueing (Weeks 3-4)**
* Extract the Scheduler into a separate cron/Celery beat process.
* Introduce Redis + Celery (or ARQ/RQ) to distribute scraping tasks to separate worker processes.
* Implement a rotating Proxy Middleware.

**Phase 3: Scraping Overhaul (Weeks 5-6)**
* Migrate scrapers away from Playwright to raw API/XHR requests wherever possible.
* Implement JSON-LD extraction fallbacks.
* Introduce a database pruning job to compress 5-minute data into daily averages after 7 days.

**Phase 4: SaaS Features (Weeks 7-8)**
* Build a FastAPI REST backend.
* Design `Users` and `Watchlists` tables in the database.
* Implement Global Product IDs (GPID) by extracting EAN/UPC or Model numbers to link cross-site products.

---

## 10. Final Verdict
**Can this become a real product?**
**Yes, but not in its current state.**

Right now, the codebase is a high-quality hobbyist bot. To become a scalable SaaS, it requires a fundamental paradigm shift: moving from a localized, monolithic, browser-based execution model to a distributed, queue-driven, API-first architecture. If you apply the refactoring plan—specifically solving the URL-identity issue and stripping away Playwright for lightweight HTTP requests—this can evolve into an incredibly powerful and profitable platform.
