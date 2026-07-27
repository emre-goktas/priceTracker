from __future__ import annotations

import asyncio
import os
import sqlite3
from datetime import datetime
from pathlib import Path

import asyncpg
from dotenv import load_dotenv

from crawler.db import get_connection
from shared.logging_config import get_logger

load_dotenv()
logger = get_logger(__name__)

SCHEMA_PATH = Path(__file__).resolve().parent.parent / "db" / "postgres_schema.sql"


async def get_pg_pool() -> asyncpg.Pool:
    return await asyncpg.create_pool(dsn=os.environ["POSTGRES_URL"])


async def ensure_schema(pool: asyncpg.Pool) -> None:
    schema_sql = SCHEMA_PATH.read_text(encoding="utf-8")
    async with pool.acquire() as conn:
        await conn.execute(schema_sql)


async def sync_site(pool: asyncpg.Pool, sqlite_conn: sqlite3.Connection, site_row: tuple) -> None:
    site_id_sqlite, name, base_url, country, enabled = site_row

    async with pool.acquire() as pg_conn:
        pg_site_id = await pg_conn.fetchval(
            """
            INSERT INTO core.sites (name, base_url, country, enabled)
            VALUES ($1, $2, $3, $4)
            ON CONFLICT (name) DO UPDATE SET
                base_url = EXCLUDED.base_url,
                country = EXCLUDED.country,
                enabled = EXCLUDED.enabled
            RETURNING id
            """,
            name,
            base_url,
            country,
            bool(enabled),
        )

        rows = sqlite_conn.execute(
            """
            SELECT su.url, sm.url, su.is_active, su.first_seen_at, su.last_seen_at
            FROM sitemap_urls su
            JOIN sitemap_snapshots sm ON sm.id = su.sitemap_snapshot_id
            WHERE su.site_id = ?
            """,
            (site_id_sqlite,),
        ).fetchall()

        if not rows:
            logger.info(f"{name}: sync edilecek URL yok")
            return

        payload = [
            (
                pg_site_id,
                url,
                source_sitemap_url,
                bool(is_active),
                datetime.fromisoformat(first_seen_at),
                datetime.fromisoformat(last_seen_at),
            )
            for url, source_sitemap_url, is_active, first_seen_at, last_seen_at in rows
        ]

        await pg_conn.executemany(
            """
            INSERT INTO core.discovered_urls (site_id, url, source_sitemap_url, is_active, first_seen_at, last_seen_at)
            VALUES ($1, $2, $3, $4, $5, $6)
            ON CONFLICT (site_id, url) DO UPDATE SET
                source_sitemap_url = EXCLUDED.source_sitemap_url,
                is_active = EXCLUDED.is_active,
                last_seen_at = EXCLUDED.last_seen_at
            """,
            payload,
        )

        active_count = sum(1 for row in rows if row[2])
        logger.info(
            f"{name}: {len(payload)} URL core.discovered_urls'a sync edildi "
            f"({active_count} aktif, {len(payload) - active_count} pasif)"
        )


async def run_sync() -> None:
    pool = await get_pg_pool()
    sqlite_conn = get_connection()
    try:
        await ensure_schema(pool)
        sites = sqlite_conn.execute(
            "SELECT id, name, base_url, country, enabled FROM sites"
        ).fetchall()
        for site_row in sites:
            await sync_site(pool, sqlite_conn, site_row)
    finally:
        sqlite_conn.close()
        await pool.close()


if __name__ == "__main__":
    asyncio.run(run_sync())
