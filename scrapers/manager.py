import asyncio
import logging
from scrapers.base import BaseScraper

logger = logging.getLogger(__name__)


class ScraperManager:
    """Coordinates all enabled scrapers and runs them concurrently."""

    def __init__(self, scrapers: list[BaseScraper]):
        self.scrapers = scrapers

    async def run_all(self) -> list[dict]:
        results = []
        tasks = [self._run_scraper(s) for s in self.scrapers]
        gathered = await asyncio.gather(*tasks, return_exceptions=True)
        for scraper, outcome in zip(self.scrapers, gathered):
            if isinstance(outcome, Exception):
                logger.error("[ScraperManager] %s failed: %s", scraper.source, outcome)
            else:
                results.extend(outcome)
        return results

    async def _run_scraper(self, scraper: BaseScraper) -> list[dict]:
        logger.info("[ScraperManager] Starting %s", scraper.source)
        data = await scraper.scrape()
        logger.info("[ScraperManager] %s returned %d products", scraper.source, len(data))
        return data
