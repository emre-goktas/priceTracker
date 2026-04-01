"""
VatanScraper – production-grade implementation.

Risk mitigations applied (ref: system_architecture.md):
  §12.2  Rate limit / IP ban  → randomized jitter delays, realistic headers
  §12.3  CAPTCHA / Bot detect → playwright-stealth, human-like scrolling
  §12.4  DOM changes          → multi-selector fallbacks, structural scraping
  §17.1  Stock awareness      → is_in_stock extracted per product card
  §18.1  Heartbeat            → logs warning when 0 products found
  §13    Anti-bot             → user-agent rotation, viewport randomization
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
# Selector fallback chains  (§12.4 DOM resilience strategy)
# ---------------------------------------------------------------------------
PRODUCT_CARD_SELECTORS = [
    ".product-list__item",
    "a.product-list-link",
    "[class*='product-list__item']",
]

NAME_SELECTORS = [
    ".product-list__product-name",
    "h3.product-list__product-name",
    "h3",
    "[class*='product-name']",
]

PRICE_SELECTORS = [
    ".product-list__price-number",
    ".product-list__price",
    "[data-price]",
    "[class*='price']",
]

LINK_SELECTORS = [
    "a.product-list__link",
    "a.product-list-link",
    "a[href*='/urun/']",
    "a[href*='/product/']",
]

OUT_OF_STOCK_SELECTORS = [
    ".out-of-stock",
    ".stok-yok",
    "[class*='out-of-stock']",
    "[class*='stok-yok']",
    ".product-list__add-to-cart--passive",
]

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
]


class VatanScraper(BaseScraper):
    source = "vatan"

    def __init__(self, urls: Optional[list[str]] = None):
        """
        Build URLs dynamically: base_url + category_path.
        Accepts optional urls list for overrides or manual runs.
        """
        self.urls = urls or self._build_urls()

    def _build_urls(self) -> list[str]:
        """Combine settings.vatan_base_url with paths from CATEGORIES."""
        base = settings.vatan_base_url.rstrip("/")
        built = []
        for cat in CATEGORIES:
            path = cat.get("paths", {}).get(self.source)
            if path:
                # Ensure path starts with /
                if not path.startswith("/"):
                    path = f"/{path}"
                built.append(f"{base}{path}")

        # Fallback to .env/settings overrides if no categories matched
        if not built and settings.vatan_urls:
            built = settings.vatan_urls

        return built

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------
    async def scrape(self) -> list[dict]:
        results: list[dict] = []

        async with async_playwright() as p:
            browser = await p.chromium.launch(
                headless=True,
                args=[
                    "--disable-blink-features=AutomationControlled",
                    "--no-sandbox",
                    "--disable-dev-shm-usage",
                    "--disable-gpu",
                ],
            )
            context = await browser.new_context(
                user_agent=random.choice(USER_AGENTS),
                viewport={
                    "width": random.choice([1920, 1440, 1366]),
                    "height": random.choice([1080, 900, 768]),
                },
                locale="tr-TR",
                timezone_id="Europe/Istanbul",
            )
            page = await context.new_page()
            await Stealth().apply_stealth_async(page)

            for url in self.urls:
                try:
                    products = await self._scrape_page(page, url)
                    results.extend(products)
                    if products:
                        logger.info("[VatanScraper] %s → %d products", url, len(products))
                    else:
                        # §18.1 Heartbeat: warn on empty result
                        logger.warning(
                            "[VatanScraper] ⚠ 0 products from %s — DOM may have changed!", url
                        )
                except Exception as exc:
                    logger.error("[VatanScraper] Failed on %s: %s", url, exc, exc_info=True)

                # §12.2 jitter delay between pages
                if url != self.urls[-1]:
                    delay = random.uniform(settings.min_delay_seconds, settings.max_delay_seconds)
                    logger.debug("[VatanScraper] Waiting %.1fs before next URL…", delay)
                    await asyncio.sleep(delay)

            await browser.close()

        return results

    # ------------------------------------------------------------------
    # Page-level scraping
    # ------------------------------------------------------------------
    async def _scrape_page(self, page: Page, url: str) -> list[dict]:
        logger.info("[VatanScraper] Navigating to %s", url)
        await page.goto(url, wait_until="networkidle", timeout=60_000)

        # §12.3 Human-like behaviour: scroll down in two steps
        await page.evaluate("window.scrollBy(0, 500)")
        await asyncio.sleep(random.uniform(1.0, 2.5))
        await page.evaluate("window.scrollBy(0, 1000)")
        await asyncio.sleep(random.uniform(0.8, 2.0))

        # Locate product cards using fallback chain
        cards_locator = await self._find_elements(page, PRODUCT_CARD_SELECTORS)
        if not cards_locator:
            return []

        cards = await cards_locator.all()
        if not cards:
            return []

        products: list[dict] = []
        for card in cards:
            product = await self._extract_product(card)
            if product:
                products.append(product)

        return products

    # ------------------------------------------------------------------
    # Per-card extraction
    # ------------------------------------------------------------------
    async def _extract_product(self, card: Locator) -> Optional[dict]:
        try:
            name = await self._extract_text(card, NAME_SELECTORS)
            if not name:
                return None

            price = await self._extract_price(card)
            if not price:
                return None

            url = await self._extract_url(card)
            if not url:
                return None

            is_in_stock = await self._extract_stock(card)

            return {
                "name": name,
                "price": price,
                "url": url,
                "source": self.source,
                "is_in_stock": is_in_stock,
            }
        except Exception as exc:
            logger.debug("[VatanScraper] Card extraction error: %s", exc)
            return None

    # ------------------------------------------------------------------
    # Field extractors
    # ------------------------------------------------------------------
    async def _extract_text(self, card: Locator, selectors: list[str]) -> Optional[str]:
        for sel in selectors:
            try:
                el = card.locator(sel).first
                if await el.count():
                    text = (await el.inner_text()).strip()
                    if text:
                        return text
            except Exception:
                continue
        return None

    async def _extract_price(self, card: Locator) -> Optional[float]:
        """Try selector chain; parse Turkish price format (e.g. '14.599,00 TL' → 14599.0)."""
        for sel in PRICE_SELECTORS:
            try:
                el = card.locator(sel).first
                if await el.count():
                    # Try data-price attribute first (most reliable)
                    attr = await el.get_attribute("data-price")
                    if attr:
                        return self._parse_price(attr)
                    text = (await el.inner_text()).strip()
                    if text:
                        parsed = self._parse_price(text)
                        if parsed:
                            return parsed
            except Exception:
                continue
        return None

    async def _extract_url(self, card: Locator) -> Optional[str]:
        # If the card itself is an <a> tag
        try:
            tag = await card.evaluate("node => node.tagName")
            if tag == "A":
                href = await card.get_attribute("href")
                if href:
                    return self._absolute_url(href)
        except Exception:
            pass

        for sel in LINK_SELECTORS:
            try:
                el = card.locator(sel).first
                if await el.count():
                    href = await el.get_attribute("href")
                    if href:
                        return self._absolute_url(href)
            except Exception:
                continue
        return None

    async def _extract_stock(self, card: Locator) -> bool:
        """Return False if any out-of-stock indicator is found."""
        for sel in OUT_OF_STOCK_SELECTORS:
            try:
                if await card.locator(sel).count():
                    return False
            except Exception:
                continue
        # If price is missing, treat as out-of-stock
        return True

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    @staticmethod
    def _parse_price(text: str) -> Optional[float]:
        """
        Handle both formats:
          '14.599,00 TL'  → 14599.0
          '14599.00'      → 14599.0
        """
        text = text.strip()
        # Turkish format: dots as thousand separators, comma as decimal
        if "," in text and "." in text:
            # e.g. '14.599,00'
            text = text.replace(".", "").replace(",", ".")
        elif "," in text:
            # e.g. '14599,00'
            text = text.replace(",", ".")
        # Remove non-numeric chars except dot
        clean = re.sub(r"[^\d.]", "", text)
        try:
            return float(clean) if clean else None
        except ValueError:
            return None

    @staticmethod
    def _absolute_url(href: str) -> str:
        if href.startswith("http"):
            return href
        return f"https://www.vatanbilgisayar.com{href}"

    async def _find_elements(self, page: Page, selectors: list[str]) -> Optional[Locator]:
        """Return the first selector that yields at least one element."""
        for sel in selectors:
            try:
                locator = page.locator(sel)
                count = await locator.count()
                if count > 0:
                    logger.debug("[VatanScraper] Using selector '%s' (%d elements)", sel, count)
                    return locator
            except Exception:
                continue

        logger.warning("[VatanScraper] No product cards found with any selector!")
        return None
