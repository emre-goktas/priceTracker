from __future__ import annotations

import asyncio

from core.parsers import base_parser
from core.parsers.site_plugins import gratis, rossmann, watsons
from core.storage import write_category_page
from shared.logging_config import get_logger

logger = get_logger(__name__)

SITE_PLUGINS = [gratis, watsons, rossmann]


async def fetch_site_categories(plugin) -> None:
    site = plugin.SITE_NAME
    config = plugin.load_config()

    # Kategori ID'leri artık configs/tr/{site}.yaml'daki main_categories'ten statik okunur
    # (veritabanı/API key'i, kolay değişmez). category_discovery orada referans olarak duruyor,
    # normal akışta çalıştırılmaz — selfheal katmanında drift kontrolü için değerlendirilecek.
    categories = [entry["id"] for entry in config["main_categories"]]
    delay_seconds = config.get("rate_limit", {}).get("delay_seconds", 1.5)
    logger.info(f"{site}: {len(categories)} ana kategori (config'ten)")

    for category_id in categories:
        if category_id != categories[0]:
            await asyncio.sleep(delay_seconds)  # kategoriler arası da bekleme - art arda istek WAF'ı tetikleyebiliyor

        try:
            page_count = 0
            blocked_count = 0
            async for page, status, content in base_parser.fetch_category_pages(config, category_id):
                object_name = write_category_page(site, category_id, page, status, content)
                page_count += 1
                if status == 200:
                    logger.info(f"{site}: kategori {category_id} sayfa {page} arşivlendi -> {object_name}")
                else:
                    blocked_count += 1
                    logger.warning(f"{site}: kategori {category_id} sayfa {page} HTTP {status} (BLOK/HATA) -> {object_name}")
            status_note = f", {blocked_count} bloklu/hatalı" if blocked_count else ""
            logger.info(f"{site}: kategori {category_id} tamamlandı ({page_count} sayfa{status_note})")
        except Exception as exc:
            logger.error(f"{site}: kategori {category_id} işlenemedi - {exc}")


async def run_category_fetch() -> None:
    results = await asyncio.gather(
        *(fetch_site_categories(plugin) for plugin in SITE_PLUGINS),
        return_exceptions=True,
    )
    for plugin, result in zip(SITE_PLUGINS, results):
        if isinstance(result, Exception):
            logger.error(f"{plugin.SITE_NAME}: category fetch başarısız - {result}")


if __name__ == "__main__":
    asyncio.run(run_category_fetch())
