from scrapers.base import BaseScraper


class VatanScraper(BaseScraper):
    source = "vatan"

    def scrape(self) -> list[dict]:
        # TODO: Implement Vatan Bilgisayar scraping logic
        raise NotImplementedError
