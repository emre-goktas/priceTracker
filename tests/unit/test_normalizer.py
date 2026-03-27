import pytest
from normalizer.normalizer import normalize


def test_normalize_basic():
    raw = {
        "name": "  Kingston 16GB DDR4  ",
        "price": "1499.90",
        "url": "https://vatan.com/product/123",
        "source": "vatan",
        "is_in_stock": True,
    }
    product = normalize(raw)
    assert product.name == "Kingston 16GB DDR4"
    assert product.price == 1499.90
    assert product.source == "vatan"
    assert len(product.id) == 64  # SHA256 hex


def test_normalize_strips_whitespace():
    raw = {
        "name": "  RTX   4090  ",
        "price": "52999",
        "url": "https://amazon.com/dp/XYZ",
        "source": "amazon",
    }
    product = normalize(raw)
    assert product.name == "RTX 4090"
