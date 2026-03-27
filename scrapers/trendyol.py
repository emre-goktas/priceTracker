from scrapers.base import BaseScraper


class TrendyolScraper(BaseScraper):
    source = "trendyol"

    def scrape(self) -> list[dict]:
        # TODO: Implement Trendyol scraping logic
        raise NotImplementedError
