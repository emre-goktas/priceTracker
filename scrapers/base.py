from abc import ABC, abstractmethod


class BaseScraper(ABC):
    """Base class for all site-specific scrapers (async interface)."""

    source: str = ""

    @abstractmethod
    async def scrape(self) -> list[dict]:
        """
        Scrape products from the target site.
        Returns a list of raw product dicts:
        {
            "name": str,
            "price": float,
            "url": str,
            "source": str,
            "is_in_stock": bool,
        }
        """
        pass
