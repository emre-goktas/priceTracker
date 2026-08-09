CREATE SCHEMA IF NOT EXISTS core;

CREATE TABLE IF NOT EXISTS core.sites (
    id SERIAL PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    base_url TEXT NOT NULL,
    country TEXT NOT NULL,
    enabled BOOLEAN NOT NULL DEFAULT TRUE
);

CREATE TABLE IF NOT EXISTS core.discovered_urls (
    id BIGSERIAL PRIMARY KEY,
    site_id INTEGER NOT NULL REFERENCES core.sites(id) ON DELETE CASCADE,
    url TEXT NOT NULL,
    source_sitemap_url TEXT NOT NULL,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    first_seen_at TIMESTAMPTZ NOT NULL,
    last_seen_at TIMESTAMPTZ NOT NULL,
    synced_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (site_id, url)
);

CREATE INDEX IF NOT EXISTS idx_discovered_urls_site_active
    ON core.discovered_urls (site_id, is_active);

CREATE SCHEMA IF NOT EXISTS pricing;

CREATE TABLE IF NOT EXISTS pricing.silver_products (
    silver_id TEXT PRIMARY KEY,
    site_code TEXT NOT NULL,
    source_product_id TEXT NOT NULL,
    fetch_date DATE NOT NULL,
    source_sku TEXT,
    ean TEXT[],
    url TEXT,
    image_url TEXT,
    currency TEXT,
    list_price_try DOUBLE PRECISION,
    sale_price_try DOUBLE PRECISION,
    conditional_promo_price_try DOUBLE PRECISION,
    discount_rate DOUBLE PRECISION,
    stock_status_raw TEXT,
    is_in_stock BOOLEAN,
    stock_qty BIGINT,
    category_path TEXT,
    name TEXT,
    name_raw TEXT,
    name_normalized TEXT,
    brand TEXT,
    brand_raw TEXT,
    brand_normalized TEXT,
    size_value DOUBLE PRECISION,
    size_unit TEXT,
    name_full_normalized TEXT,
    synced_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_silver_products_site_date
    ON pricing.silver_products (site_code, fetch_date);
CREATE INDEX IF NOT EXISTS idx_silver_products_source_product
    ON pricing.silver_products (site_code, source_product_id);
