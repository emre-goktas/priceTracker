from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

import asyncpg
import duckdb

from shared.logging_config import get_logger
from shared.pg_client import get_pg_pool

logger = get_logger(__name__)

DB_PATH = Path(__file__).resolve().parent / "pricebot.duckdb"
SCHEMA_PATH = Path(__file__).resolve().parent.parent / "db" / "postgres_schema.sql"

# DESCRIBE silver_products ile birebir aynı sıra.
COLUMNS = [
    "silver_id", "site_code", "source_product_id", "fetch_date", "source_sku", "ean",
    "url", "image_url", "currency", "list_price_try", "sale_price_try",
    "conditional_promo_price_try", "discount_rate", "stock_status_raw", "is_in_stock",
    "stock_qty", "category_path", "name", "name_raw", "name_normalized", "brand",
    "brand_raw", "brand_normalized", "size_value", "size_unit", "name_full_normalized",
]
UPDATE_COLUMNS = [c for c in COLUMNS if c != "silver_id"]


async def ensure_schema(pool: asyncpg.Pool) -> None:
    schema_sql = SCHEMA_PATH.read_text(encoding="utf-8")
    async with pool.acquire() as conn:
        await conn.execute(schema_sql)


async def sync_silver(
    pool: asyncpg.Pool, con: duckdb.DuckDBPyConnection, sites: list[str] | None
) -> int:
    where, params = "", []
    if sites:
        where = f"WHERE site_code IN ({', '.join('?' for _ in sites)})"
        params = list(sites)

    rows = con.execute(
        f"SELECT {', '.join(COLUMNS)} FROM silver_products {where}", params
    ).fetchall()

    if not rows:
        logger.info("silver_products boş veya filtreyle eşleşen satır yok")
        return 0

    set_clause = ",\n            ".join(f"{c} = EXCLUDED.{c}" for c in UPDATE_COLUMNS)
    placeholders = ", ".join(f"${i}" for i in range(1, len(COLUMNS) + 1))
    insert_sql = f"""
        INSERT INTO pricing.silver_products ({", ".join(COLUMNS)}, synced_at)
        VALUES ({placeholders}, now())
        ON CONFLICT (silver_id) DO UPDATE SET
            {set_clause},
            synced_at = now()
    """

    async with pool.acquire() as pg_conn:
        await pg_conn.executemany(insert_sql, rows)

    logger.info(f"pricing.silver_products: {len(rows)} satır sync edildi")
    return len(rows)


async def run_sync(sites: list[str] | None = None) -> int:
    pool = await get_pg_pool()
    con = duckdb.connect(str(DB_PATH), read_only=True)
    try:
        await ensure_schema(pool)
        return await sync_silver(pool, con, sites)
    finally:
        con.close()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="DuckDB silver_products -> Postgres pricing.silver_products"
    )
    parser.add_argument("--sites", nargs="*", help="sadece bu siteler (varsayılan: hepsi)")
    args = parser.parse_args()
    asyncio.run(run_sync(args.sites))


if __name__ == "__main__":
    main()
