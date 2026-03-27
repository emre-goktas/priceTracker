from scrapers.base import BaseScraper


class MediaMarktScraper(BaseScraper):
    source = "mediamarkt"

    def scrape(self) -> list[dict]:
        # TODO: Implement MediaMarkt scraping logic
        raise NotImplementedError
