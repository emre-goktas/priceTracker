"""
Async SQLAlchemy session factory and repository helpers.
Uses aiosqlite for SQLite (dev) or asyncpg for PostgreSQL (prod).
"""

import logging
from typing import Optional
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy import select, event
from sqlalchemy.pool import Pool

from storage.models import Base, PriceHistory, Product
from normalizer.normalizer import NormalizedProduct

logger = logging.getLogger(__name__)

# Apply SQLite optimization PRAGMAs globally on new connections
@event.listens_for(Pool, "connect")
def set_sqlite_pragma(dbapi_connection, connection_record):
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA synchronous=NORMAL")
    cursor.execute("PRAGMA cache_size=-64000")  # 64MB cache
    cursor.close()

def make_engine(db_url: str):
    return create_async_engine(db_url, echo=False, future=True)


async def init_db(db_url: str):
    """Create all tables."""
    engine = make_engine(db_url)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    logger.info("[DB] Tables initialised.")
    return engine


def make_session_factory(engine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False)


class Repository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def upsert_product(self, product: NormalizedProduct) -> Product:
        result = await self.session.get(Product, product.id)
        if not result:
            result = Product(
                id=product.id,
                name=product.name,
                url=product.url,
                source=product.source,
            )
            self.session.add(result)
        else:
            result.name = product.name  # update if name changed
        return result

    async def add_price_record(self, product: NormalizedProduct) -> Optional[PriceHistory]:
        # Prevent database bloat by skipping duplicate consecutive prices
        stmt = (
            select(PriceHistory)
            .where(PriceHistory.product_id == product.id)
            .order_by(PriceHistory.timestamp.desc())
            .limit(1)
        )
        last_record = (await self.session.execute(stmt)).scalar_one_or_none()
        
        if last_record and last_record.price == product.price and last_record.is_in_stock == product.is_in_stock:
            return None

        record = PriceHistory(
            product_id=product.id,
            price=product.price,
            is_in_stock=product.is_in_stock,
            shipping_fee=product.shipping_fee,
        )
        self.session.add(record)
        return record

    async def get_recent_prices(self, product_id: str, limit: int = 30) -> list[PriceHistory]:
        stmt = (
            select(PriceHistory)
            .where(PriceHistory.product_id == product_id)
            .order_by(PriceHistory.timestamp.desc())
            .limit(limit)
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())
