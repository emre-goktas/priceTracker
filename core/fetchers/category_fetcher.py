from __future__ import annotations

import asyncio
from pathlib import Path

import yaml

from core.parsers import base_parser
from core.parsers.site_plugins import gratis, rossmann, watsons
from core.storage import write_category_page
from shared.logging_config import get_logger

logger = get_logger(__name__)

SITES_CONFIG_PATH = Path(__file__).resolve().parent.parent.parent / "configs" / "sites.yaml"
SITE_PLUGINS = [gratis, watsons, rossmann]


def _load_base_urls() -> dict[str, str]:
    raw = yaml.safe_load(SITES_CONFIG_PATH.read_text(encoding="utf-8")) or {}
    return {entry["name"]: entry["base_url"] for entry in raw.get("sites", []) if entry.get("enabled")}


async def fetch_site_categories(plugin, base_url: str) -> None:
    site = plugin.SITE_NAME
    config = plugin.load_config()

    # Kategori ID'leri artık configs/tr/{site}.yaml'daki main_categories'ten statik okunur
    # (veritabanı/API key'i, kolay değişmez). category_discovery orada referans olarak duruyor,
    # normal akışta çalıştırılmaz — selfheal katmanında drift kontrolü için değerlendirilecek.
    categories = [entry["id"] for entry in config["main_categories"]]
    logger.info(f"{site}: {len(categories)} ana kategori (config'ten)")

    for category_id in categories:
        try:
            page_count = 0
            async for page, status, content in base_parser.fetch_category_pages(config, category_id):
                object_name = write_category_page(site, category_id, page, status, content)
                page_count += 1
                logger.info(f"{site}: kategori {category_id} sayfa {page} ({status}) arşivlendi -> {object_name}")
            logger.info(f"{site}: kategori {category_id} tamamlandı ({page_count} sayfa)")
        except Exception as exc:
            logger.error(f"{site}: kategori {category_id} işlenemedi - {exc}")


async def run_category_fetch() -> None:
    base_urls = _load_base_urls()
    results = await asyncio.gather(
        *(fetch_site_categories(plugin, base_urls[plugin.SITE_NAME]) for plugin in SITE_PLUGINS),
        return_exceptions=True,
    )
    for plugin, result in zip(SITE_PLUGINS, results):
        if isinstance(result, Exception):
            logger.error(f"{plugin.SITE_NAME}: category fetch başarısız - {result}")


if __name__ == "__main__":
    asyncio.run(run_category_fetch())
