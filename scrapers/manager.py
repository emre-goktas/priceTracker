import concurrent.futures
from scrapers.base import BaseScraper


class ScraperManager:
    """Coordinates all enabled scrapers and runs them in parallel."""

    def __init__(self, scrapers: list[BaseScraper]):
        self.scrapers = scrapers

    def run_all(self) -> list[dict]:
        results = []
        with concurrent.futures.ThreadPoolExecutor() as executor:
            futures = {executor.submit(s.scrape): s for s in self.scrapers}
            for future in concurrent.futures.as_completed(futures):
                scraper = futures[future]
                try:
                    data = future.result()
                    results.extend(data)
                except Exception as exc:
                    print(f"[ScraperManager] {scraper.source} failed: {exc}")
        return results
