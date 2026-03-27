from scrapers.base import BaseScraper


class TeknosasScraper(BaseScraper):
    source = "teknosa"

    def scrape(self) -> list[dict]:
        # TODO: Implement Teknosa scraping logic
        raise NotImplementedError
