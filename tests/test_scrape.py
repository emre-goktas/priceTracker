import asyncio
from scrapers.mediamarkt import MediaMarktScraper

async def run():
    urls = ["https://www.mediamarkt.com.tr/tr/search.html?query=iphone"]
    scraper = MediaMarktScraper(urls=urls)
    # Patch to print out what apolloState actually has
    original_extract = scraper._extract_product
    async def debug_extract(card, apollo_state=None):
        import re
        url = await scraper._extract_url(card)
        
        # print raw card text to see where 10.0 came from
        raw_text = await card.inner_text()
        print(f"--- RAW TEXT FOR {url} ---\n{raw_text}\n--- END RAW ---")

        if url and apollo_state:
            match = re.search(r'-(\d+)\.html', url)
            if match:
                prod_id = match.group(1)
                prod_info = apollo_state.get(f"Product:{prod_id}")
                if prod_info:
                    print(f"Product ID: {prod_id}")
                    print(f"price info: {prod_info.get('price')}")
                else:
                    print(f"Product ID {prod_id} not in apollo_state. Looking for matching keys: {[k for k in apollo_state.keys() if prod_id in k]}")

        return await original_extract(card, apollo_state)
    
    scraper._extract_product = debug_extract
    results = await scraper.scrape()
    print(f"\n[Test] Scraped {len(results)} items.")
    for p in results[:3]:
        print(f"{p['name']} | {p['price']} | Stock: {p['is_in_stock']}")

if __name__ == "__main__":
    asyncio.run(run())
