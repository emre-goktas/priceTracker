from abc import ABC, abstractmethod


class BaseScraper(ABC):
    """Base class for all site-specific scrapers."""

    source: str = ""

    @abstractmethod
    def scrape(self) -> list[dict]:
        """
        Scrape products from the target site.
        Returns a list of raw product dicts in the format:
        {
            "name": str,
            "price": float,
            "url": str,
            "source": str,
            "is_in_stock": bool,
        }
        """
        pass
