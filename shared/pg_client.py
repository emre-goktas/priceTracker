from __future__ import annotations

import os

import asyncpg
from dotenv import load_dotenv

load_dotenv()

# Ortak Postgres bağlantı havuzu. crawler/ ve matching/ birbirini import edemediği (loosely
# coupled) için Postgres erişimi shared/ altında tutulur - shared/storage.py'nin MinIO için
# yaptığı ile aynı gerekçe. asyncpg'nin async doğası nedeniyle storage.py'deki
# proxy-attribute deseni yerine async bir getter fonksiyonu + modül-seviyesi cache kullanılır.
_pool: asyncpg.Pool | None = None


async def get_pg_pool() -> asyncpg.Pool:
    global _pool
    if _pool is None:
        _pool = await asyncpg.create_pool(dsn=os.environ["POSTGRES_URL"])
    return _pool


async def close_pg_pool() -> None:
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None
