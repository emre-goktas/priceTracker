"""
SQLAlchemy ORM models.

Tables:
  products      – static product metadata (one row per unique URL)
  price_history – time-series price records (many rows per product)
"""

from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    String,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class Product(Base):
    __tablename__ = "products"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)  # SHA256 of URL
    name: Mapped[str] = mapped_column(String(512))
    url: Mapped[str] = mapped_column(String(2048), unique=True)
    source: Mapped[str] = mapped_column(String(64))
    category: Mapped[str | None] = mapped_column(String(128), nullable=True)

    price_history: Mapped[list["PriceHistory"]] = relationship(
        back_populates="product", cascade="all, delete-orphan"
    )


class PriceHistory(Base):
    __tablename__ = "price_history"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    product_id: Mapped[str] = mapped_column(ForeignKey("products.id"))
    price: Mapped[float] = mapped_column(Float)
    is_in_stock: Mapped[bool] = mapped_column(Boolean, default=True)
    shipping_fee: Mapped[float | None] = mapped_column(Float, nullable=True)
    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    product: Mapped["Product"] = relationship(back_populates="price_history")
