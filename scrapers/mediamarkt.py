"""
MediaMarktScraper – generic implementation based on the priceTracker architecture.

DOM targets:
  - Product Card: article[data-test='mms-product-card']
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

PRODUCT_CARD_SELECTORS = [
    "article[data-test='mms-product-card']",
    "div[class*='mms-search-productlist'] article",
    "div[data-test='mms-search-srp-productlist'] article"
]

NAME_SELECTORS = [
    "[data-test='product-title']",
    "[data-test='mms-product-title']",
    "[data-test='mms-product-list-item-title']",
    "p[class*='title']",
    "h2",
    "h3",
]

PRICE_SELECTORS = [
    "[data-test='mms-price'] span",
    "[data-test='mms-price']",
    "[class*='price']",
    "span[font-family='price']"
]

LINK_SELECTORS = [
    "a[data-test*='product-list-item-link']",
    "a[data-test='mms-product-list-item-link']",
    "a[href*='/product/']",
    "a:has(picture)",
    "a"
]

OUT_OF_STOCK_SELECTORS = [
    "text='Tükendi'",
    "text='Stokta yok'",
    "text='Online satışa kapalı'",
    "[data-test='mms-out-of-stock']"
]

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
]

class MediaMarktScraper(BaseScraper):
    source = "mediamarkt"

    def __init__(self, urls: Optional[list[str]] = None):
        self.urls = urls or self._build_urls()

    def _build_urls(self) -> list[str]:
        base = getattr(settings, "mediamarkt_base_url", "https://www.mediamarkt.com.tr").rstrip("/")
        built = []
        for cat in CATEGORIES:
            path = cat.get("paths", {}).get(self.source)
            if path:
                if not path.startswith("/"):
                    path = f"/{path}"
                built.append(f"{base}{path}")
        
        if not built and hasattr(settings, "mediamarkt_urls") and getattr(settings, "mediamarkt_urls"):
            built = getattr(settings, "mediamarkt_urls")
            
        return built

    async def scrape(self) -> list[dict]:
        results: list[dict] = []

        async with async_playwright() as p:
            context = await p.chromium.launch_persistent_context(
                user_data_dir="./.browser_profile",
                headless=False,
                args=["--disable-blink-features=AutomationControlled", "--no-sandbox"],
                user_agent=random.choice(USER_AGENTS),
                viewport={"width": 1920, "height": 1080},
                locale="tr-TR",
            )
            page = context.pages[0] if context.pages else await context.new_page()
            await Stealth().apply_stealth_async(page)

            for url in self.urls:
                try:
                    products = await self._scrape_page(page, url)
                    results.extend(products)
                    logger.info("[MediaMarktScraper] %s → %d products", url, len(products))
                except Exception as exc:
                    logger.error("[MediaMarktScraper] Failed on %s: %s", url, exc)

                if url != self.urls[-1]:
                    await asyncio.sleep(random.uniform(2, 5))

            await context.close()

        return results

    async def _scrape_page(self, page: Page, url: str) -> list[dict]:
        logger.info("[MediaMarktScraper] Navigating to %s", url)
        # MediaMarkt uses React/Client-side rendering. Wait for load event.
        await page.goto(url, wait_until="load", timeout=60_000)
        
        # Scroll logic to force React lazy components to mount (like images and price blocks)
        for i in range(4):
            await page.evaluate(f"window.scrollBy(0, {500 + i*200})")
            await asyncio.sleep(1.0)

        cards_locator = await self._find_elements(page, PRODUCT_CARD_SELECTORS)
        if not cards_locator:
            logger.warning("[MediaMarktScraper] ⚠ _find_elements returned no cards for %s", url)
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
        # Priority 1: Target specific price selectors
        for sel in PRICE_SELECTORS:
            try:
                el = card.locator(sel).first
                if await el.count():
                    text = (await el.inner_text()).strip()
                    if text:
                        parsed = self._parse_price(text)
                        if parsed: return parsed
            except Exception: continue

        # Priority 2: Fallback to regex search on the whole card if selectors fail
        try:
            raw_text = await card.inner_text()
            # Look for ₺ followed by numbers
            match = re.search(r'₺\s*(\d+[.\d,]*)', raw_text)
            if match:
                parsed = self._parse_price(match.group(1))
                if parsed: return parsed
        except Exception:
            pass

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
        for sel in OUT_OF_STOCK_SELECTORS:
            try:
                if await card.locator(sel).count() > 0:
                    return False
            except Exception: continue
        return True

    @staticmethod
    def _parse_price(text: str) -> Optional[float]:
        # Handle "₺69.999,–", "14.599 TL" etc.
        # MediaMarkt uses en-dash (–) or em-dash (—) frequently.
        text = text.replace("₺", "").replace("TL", "").replace(",-", "").replace(",–", "").replace(",—", "").strip()
        
        # If multiple values remain (like from dual spans), take the first numeric block
        match = re.search(r'(\d+[.\d,]*)', text)
        if not match:
            return None
            
        clean = match.group(1)
        
        # Handle Turkish decimal format (1.234,56 -> 1234.56)
        if "," in clean and "." in clean:
            clean = clean.replace(".", "").replace(",", ".")
        elif "," in clean:
            clean = clean.replace(",", ".")
            
        # Final pass to remove any non-numeric except dot
        clean = re.sub(r"[^\d.]", "", clean)
        
        try:
            return float(clean) if clean else None
        except ValueError:
            return None

    @staticmethod
    def _absolute_url(href: str) -> str:
        if href.startswith("http"): return href
        return f"https://www.mediamarkt.com.tr{href}"

    async def _find_elements(self, page: Page, selectors: list[str]) -> Optional[Locator]:
        for sel in selectors:
            try:
                await page.wait_for_selector(sel, state="attached", timeout=15000)
                locator = page.locator(sel)
                if await locator.count() > 0: 
                    return locator
            except Exception: 
                continue
        logger.warning("[MediaMarktScraper] No product cards found. Possibly blocked by PerimeterX or still loading.")
        return None
