from __future__ import annotations

import asyncio

from core.parsers import base_parser
from core.parsers.site_plugins import eveshop, gratis, rossmann, watsons
from core.storage import write_category_page
from shared import http_client
from shared.logging_config import get_logger

logger = get_logger(__name__)

SITE_PLUGINS = [gratis, watsons, rossmann, eveshop]


async def fetch_site_categories(plugin) -> None:
    site = plugin.SITE_NAME
    config = plugin.load_config()

    # Kategori ID'leri artık configs/tr/{site}.yaml'daki main_categories'ten statik okunur
    # (veritabanı/API key'i, kolay değişmez). category_discovery orada referans olarak duruyor,
    # normal akışta çalıştırılmaz — selfheal katmanında drift kontrolü için değerlendirilecek.
    categories = [(entry["id"], entry["name"]) for entry in config["main_categories"]]
    delay_seconds = config.get("rate_limit", {}).get("delay_seconds", 1.5)
    # Varsayılan JSON (Gratis/Watsons/Rossmann'ın kategori API yanıtı) - HTML kazıyan siteler
    # (Eveshop) configs/tr/{site}.yaml -> archive ile override eder (bkz. core/storage.py).
    archive_cfg = config.get("archive", {})
    archive_extension = archive_cfg.get("extension", "json")
    archive_content_type = archive_cfg.get("content_type", "application/json")
    logger.info(f"{site}: {len(categories)} ana kategori (config'ten)")

    for index, (category_id, category_name) in enumerate(categories):
        if index > 0:
            await asyncio.sleep(delay_seconds)  # kategoriler arası da bekleme - art arda istek WAF'ı tetikleyebiliyor

        try:
            page_count = 0
            blocked_count = 0
            async for page, status, content in base_parser.fetch_category_pages(config, category_id):
                object_name = write_category_page(
                    site, category_id, category_name, page, status, content,
                    extension=archive_extension, content_type=archive_content_type,
                )
                page_count += 1
                if status == 200:
                    logger.info(f"{site}: kategori {category_id} ({category_name}) sayfa {page} arşivlendi -> {object_name}")
                else:
                    blocked_count += 1
                    logger.warning(
                        f"{site}: kategori {category_id} ({category_name}) sayfa {page} HTTP {status} (BLOK/HATA) -> {object_name}"
                    )
            status_note = f", {blocked_count} bloklu/hatalı" if blocked_count else ""
            logger.info(f"{site}: kategori {category_id} ({category_name}) tamamlandı ({page_count} sayfa{status_note})")
        except Exception as exc:
            logger.error(f"{site}: kategori {category_id} ({category_name}) işlenemedi - {exc}")


async def run_category_fetch() -> None:
    try:
        results = await asyncio.gather(
            *(fetch_site_categories(plugin) for plugin in SITE_PLUGINS),
            return_exceptions=True,
        )
        for plugin, result in zip(SITE_PLUGINS, results):
            if isinstance(result, Exception):
                logger.error(f"{plugin.SITE_NAME}: category fetch başarısız - {result}")
    finally:
        await http_client.close()


if __name__ == "__main__":
    asyncio.run(run_category_fetch())
