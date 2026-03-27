from scrapers.base import BaseScraper


class AmazonScraper(BaseScraper):
    source = "amazon"

    def scrape(self) -> list[dict]:
        # TODO: Implement Amazon scraping logic
        raise NotImplementedError
