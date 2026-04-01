"""
HepsiburadaScraper – implementation for Hepsiburada.com.

Risk mitigations:
  - data-test-id based selectors (resilient to DOM changes)
  - playwright-stealth + realistic headers
  - human-like interaction (scrolling)
"""

import asyncio
import logging
import random
import re
from typing import Optional

from playwright.async_api import async_playwright, Page, Locator
from playwright_stealth import Stealth

from scrapers.base import BaseScraper
from config.settings import settings
from config.categories import CATEGORIES

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Selectors (Priority to data-test-id as per research)
# ---------------------------------------------------------------------------
PRODUCT_CARD_SELECTORS = [
    "li:has(article[class*='productCard-module'])",
    "article[class*='productCard-module']",
    "li[class*='productListContent-item']",
    "li.productListContent-zAP0Y5msy8OHq5ov7NPr",
    "[data-test-id='product-card-container']",
]

NAME_SELECTORS = [
    "[class*='title-module_titleText']",
    "h2[data-test-id^='title-']",
    "[data-test-id='product-card-name']",
    "h3[type='comfort']",
    "h3",
]

PRICE_SELECTORS = [
    "[data-test-id^='final-price-']",
    "[class*='price-module_finalPrice']",
    "[data-test-id='price-current-price']",
    "div[class*='price-current-price']",
]

LINK_SELECTORS = [
    "a[class*='productCardLink']",
    "a[data-test-id='product-card-link']",
    "a.moria-ProductCard-ayq7p",
]

# Look for specific labels or buttons indicating stock issues
OUT_OF_STOCK_INDICATORS = [
    "button:has-text('Gelince Haber Ver')",
    ".moria-ProductCard-outOfStock",
    "div:has-text('Tükendi')",
]

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
]


class HepsiburadaScraper(BaseScraper):
    source = "hepsiburada"

    def __init__(self, urls: Optional[list[str]] = None):
        self.urls = urls or self._build_urls()

    def _build_urls(self) -> list[str]:
        base = settings.hepsiburada_base_url.rstrip("/")
        built = []
        for cat in CATEGORIES:
            path = cat.get("paths", {}).get(self.source)
            if path:
                if not path.startswith("/"):
                    path = f"/{path}"
                built.append(f"{base}{path}")
        
        if not built and settings.hepsiburada_urls:
            built = settings.hepsiburada_urls
            
        return built

    async def scrape(self) -> list[dict]:
        results: list[dict] = []

        async with async_playwright() as p:
            browser = await p.chromium.launch(
                headless=False,
                args=["--disable-blink-features=AutomationControlled", "--no-sandbox"]
            )
            context = await browser.new_context(
                user_agent=random.choice(USER_AGENTS),
                viewport={"width": 1920, "height": 1080},
                locale="tr-TR",
            )
            page = await context.new_page()
            await Stealth().apply_stealth_async(page)

            for url in self.urls:
                try:
                    products = await self._scrape_page(page, url)
                    results.extend(products)
                    logger.info("[HepsiburadaScraper] %s → %d products", url, len(products))
                except Exception as exc:
                    logger.error("[HepsiburadaScraper] Failed on %s: %s", url, exc)

                if url != self.urls[-1]:
                    await asyncio.sleep(random.uniform(3, 7))

            await browser.close()

        return results

    async def _scrape_page(self, page: Page, url: str) -> list[dict]:
        logger.info("[HepsiburadaScraper] Navigating to %s", url)
        # Hepsiburada often needs a bit more time for initial state
        await page.goto(url, wait_until="load", timeout=60_000)
        
        # Human-like scroll to load lazy-loaded elements
        for i in range(3):
            await page.evaluate(f"window.scrollBy(0, {400 + i*200})")
            await asyncio.sleep(1.2)

        cards_locator = await self._find_elements(page, PRODUCT_CARD_SELECTORS)
        if not cards_locator:
            return []

        cards = await cards_locator.all()
        products: list[dict] = []
        for card in cards:
            product = await self._extract_product(card)
            if product:
                products.append(product)

        return products

    async def _extract_product(self, card: Locator) -> Optional[dict]:
        try:
            name = await self._extract_text(card, NAME_SELECTORS)
            if not name: return None

            price = await self._extract_price(card)
            if not price: return None

            url = await self._extract_url(card)
            if not url: return None

            is_in_stock = await self._extract_stock(card)

            return {
                "name": name,
                "price": price,
                "url": url,
                "source": self.source,
                "is_in_stock": is_in_stock,
            }
        except Exception:
            return None

    async def _extract_text(self, card: Locator, selectors: list[str]) -> Optional[str]:
        for sel in selectors:
            try:
                el = card.locator(sel).first
                if await el.count():
                    text = (await el.inner_text()).strip()
                    if text: return text
            except Exception: continue
        return None

    async def _extract_price(self, card: Locator) -> Optional[float]:
        for sel in PRICE_SELECTORS:
            try:
                el = card.locator(sel).first
                if await el.count():
                    text = (await el.inner_text()).strip()
                    if text:
                        return self._parse_price(text)
            except Exception: continue
        return None

    async def _extract_url(self, card: Locator) -> Optional[str]:
        for sel in LINK_SELECTORS:
            try:
                el = card.locator(sel).first
                if await el.count():
                    href = await el.get_attribute("href")
                    if href: return self._absolute_url(href)
            except Exception: continue
        return None

    async def _extract_stock(self, card: Locator) -> bool:
        for sel in OUT_OF_STOCK_INDICATORS:
            try:
                if await card.locator(sel).count() > 0:
                    return False
            except Exception: continue
        return True

    @staticmethod
    def _parse_price(text: str) -> Optional[float]:
        # Hepsiburada usually "14.599,00 TL" or "14.599 TL"
        text = text.split("TL")[0].strip()
        text = text.replace(".", "").replace(",", ".")
        clean = re.sub(r"[^\d.]", "", text)
        try:
            return float(clean) if clean else None
        except ValueError:
            return None

    @staticmethod
    def _absolute_url(href: str) -> str:
        if href.startswith("http"): return href
        return f"https://www.hepsiburada.com{href}"

    async def _find_elements(self, page: Page, selectors: list[str]) -> Optional[Locator]:
        for sel in selectors:
            try:
                locator = page.locator(sel)
                if await locator.count() > 0: return locator
            except Exception: continue
        return None
