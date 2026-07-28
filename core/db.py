from __future__ import annotations

import os

import asyncpg
from dotenv import load_dotenv

load_dotenv()

_pool: asyncpg.Pool | None = None


async def get_pool() -> asyncpg.Pool:
    global _pool
    if _pool is None:
        _pool = await asyncpg.create_pool(dsn=os.environ["POSTGRES_URL"])
    return _pool


async def get_discovered_urls(site_name: str, source_contains: str | None = None) -> list[str]:
    """crawler/'ın senkronize ettiği core.discovered_urls'tan bir sitenin URL'lerini okur.

    core/ ve crawler/ birbirini import edemediği için (loosely coupled) bu, iki modül
    arasındaki tek bağlantı noktasıdır: Postgres üzerinden veri alışverişi.
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        if source_contains:
            rows = await conn.fetch(
                """
                SELECT du.url FROM core.discovered_urls du
                JOIN core.sites s ON s.id = du.site_id
                WHERE s.name = $1 AND du.source_sitemap_url ILIKE $2
                """,
                site_name,
                f"%{source_contains}%",
            )
        else:
            rows = await conn.fetch(
                """
                SELECT du.url FROM core.discovered_urls du
                JOIN core.sites s ON s.id = du.site_id
                WHERE s.name = $1
                """,
                site_name,
            )
    return [row["url"] for row in rows]
