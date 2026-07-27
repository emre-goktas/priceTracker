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
