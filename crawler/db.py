from __future__ import annotations

import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent / "crawler.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS sites (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    base_url TEXT NOT NULL,
    country TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS robots_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    site_id INTEGER NOT NULL UNIQUE REFERENCES sites(id) ON DELETE CASCADE,
    content_hash TEXT NOT NULL,
    http_status INTEGER NOT NULL,
    object_name TEXT NOT NULL,
    last_checked_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS robots_endpoints (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    robots_snapshot_id INTEGER NOT NULL REFERENCES robots_snapshots(id) ON DELETE CASCADE,
    site_id INTEGER NOT NULL REFERENCES sites(id) ON DELETE CASCADE,
    directive TEXT NOT NULL CHECK (directive IN ('disallow', 'allow')),
    pattern TEXT NOT NULL,
    discovered_at TEXT NOT NULL,
    UNIQUE (robots_snapshot_id, directive, pattern)
);

CREATE TABLE IF NOT EXISTS sitemap_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    site_id INTEGER NOT NULL REFERENCES sites(id) ON DELETE CASCADE,
    parent_sitemap_id INTEGER REFERENCES sitemap_snapshots(id) ON DELETE CASCADE,
    url TEXT NOT NULL,
    root_tag TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    http_status INTEGER NOT NULL,
    object_name TEXT NOT NULL,
    last_checked_at TEXT NOT NULL,
    UNIQUE (site_id, url)
);

CREATE TABLE IF NOT EXISTS sitemap_urls (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    site_id INTEGER NOT NULL REFERENCES sites(id) ON DELETE CASCADE,
    sitemap_snapshot_id INTEGER NOT NULL REFERENCES sitemap_snapshots(id) ON DELETE CASCADE,
    url TEXT NOT NULL,
    is_active INTEGER NOT NULL DEFAULT 1,
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    UNIQUE (site_id, url)
);
"""


def get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA foreign_keys=ON;")
    conn.executescript(SCHEMA)
    return conn


def upsert_site(conn: sqlite3.Connection, name: str, base_url: str, country: str, enabled: bool) -> int:
    conn.execute(
        """
        INSERT INTO sites (name, base_url, country, enabled)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(name) DO UPDATE SET
            base_url = excluded.base_url,
            country = excluded.country,
            enabled = excluded.enabled
        """,
        (name, base_url, country, int(enabled)),
    )
    row = conn.execute("SELECT id FROM sites WHERE name = ?", (name,)).fetchone()
    return row[0]
