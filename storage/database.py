"""
Database session factory and repository helpers.
"""

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from storage.models import Base, PriceHistory, Product
from normalizer.normalizer import NormalizedProduct


def get_engine(db_url: str):
    return create_engine(db_url, echo=False, future=True)


def init_db(db_url: str):
    """Create all tables if they don't exist."""
    engine = get_engine(db_url)
    Base.metadata.create_all(engine)
    return engine


class Repository:
    def __init__(self, session: Session):
        self.session = session

    def upsert_product(self, product: NormalizedProduct) -> Product:
        db_product = self.session.get(Product, product.id)
        if not db_product:
            db_product = Product(
                id=product.id,
                name=product.name,
                url=product.url,
                source=product.source,
            )
            self.session.add(db_product)
        else:
            db_product.name = product.name  # update name if changed
        return db_product

    def add_price_record(self, product: NormalizedProduct) -> PriceHistory:
        record = PriceHistory(
            product_id=product.id,
            price=product.price,
            is_in_stock=product.is_in_stock,
            shipping_fee=product.shipping_fee,
        )
        self.session.add(record)
        return record

    def get_recent_prices(self, product_id: str, limit: int = 20) -> list[PriceHistory]:
        return (
            self.session.query(PriceHistory)
            .filter(PriceHistory.product_id == product_id)
            .order_by(PriceHistory.timestamp.desc())
            .limit(limit)
            .all()
        )
