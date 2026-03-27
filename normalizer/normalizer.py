import hashlib
import re
from pydantic import BaseModel


class NormalizedProduct(BaseModel):
    id: str               # SHA256 of URL
    name: str
    price: float
    url: str
    source: str
    is_in_stock: bool
    shipping_fee: float | None = None


def _clean_name(name: str) -> str:
    """Strip extra whitespace and normalize unicode."""
    return re.sub(r"\s+", " ", name).strip()


def _hash_url(url: str) -> str:
    return hashlib.sha256(url.encode()).hexdigest()


def normalize(product: dict) -> NormalizedProduct:
    """Transform a raw scraper dict into a validated NormalizedProduct."""
    return NormalizedProduct(
        id=_hash_url(product["url"]),
        name=_clean_name(product["name"]),
        price=float(product["price"]),
        url=product["url"],
        source=product["source"],
        is_in_stock=product.get("is_in_stock", True),
        shipping_fee=product.get("shipping_fee"),
    )


def normalize_batch(products: list[dict]) -> list[NormalizedProduct]:
    return [normalize(p) for p in products]
