"""
Integration test – live Vatan scrape (no DB writes, no Telegram).
Run: pytest tests/integration/test_vatan_scrape.py -v -s
Requires: playwright chromium installed, internet access.
"""

import asyncio
import pytest
from scrapers.vatan import VatanScraper

# Short URL list for CI – only the RAM category
TEST_URLS = ["https://www.vatanbilgisayar.com/arama/ram"]


@pytest.mark.asyncio
async def test_vatan_scrape_returns_products():
    scraper = VatanScraper(urls=TEST_URLS)
    products = await scraper.scrape()

    assert len(products) > 0, "No products scraped – DOM may have changed"

    for p in products:
        assert "name" in p and p["name"], f"Missing name: {p}"
        assert "price" in p and p["price"] > 0, f"Invalid price: {p}"
        assert "url" in p and p["url"].startswith("https://"), f"Invalid URL: {p}"
        assert "source" in p and p["source"] == "vatan"
        assert "is_in_stock" in p and isinstance(p["is_in_stock"], bool)

    print(f"\n✅ Scraped {len(products)} products from Vatan")
    for p in products[:3]:
        print(f"  {p['name'][:60]} | {p['price']} TL | stock={p['is_in_stock']}")
