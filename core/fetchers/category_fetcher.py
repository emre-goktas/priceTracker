from __future__ import annotations

import asyncio
import importlib
from pathlib import Path

import yaml

from core.parsers import base_parser
from core.storage import write_category_page
from shared import http_client
from shared.logging_config import get_logger

logger = get_logger(__name__)

SITES_CONFIG_PATH = Path(__file__).resolve().parents[2] / "configs" / "sites.yaml"


# CLAUDE NOTU: Çekirdek kodda (bu dosyada) site isimleri (gratis, watsons vb.) hardcode
# EDİLEMEZ (bkz. CLAUDE.md Kural 3). Eski sürümde burada statik importlar ve 
# SITE_PLUGINS listesi vardı (Antigravity tarafından düzeltildi).
# Plugin'ler configs/sites.yaml dosyasından "enabled: true" olanlara bakarak
# importlib ile DİNAMİK olarak yüklenmelidir. Lütfen burayı tekrar hardcode'a çevirme!
def load_enabled_plugins():
    raw = yaml.safe_load(SITES_CONFIG_PATH.read_text(encoding="utf-8")) or {}
    plugins = []
    for entry in raw.get("sites", []):
        if entry.get("enabled"):
            site_name = entry["name"]
            module_name = f"core.parsers.site_plugins.{site_name}"
            plugin = importlib.import_module(module_name)
            plugins.append(plugin)
    return plugins


def _log_completeness(
    site: str,
    category_id: str,
    category_name: str,
    page_count: int,
    blocked_count: int,
    collected_items: int,
    last_page,
) -> None:
    """Kategori çekiminin EKSİKSİZ olup olmadığını karar verip loglar.

    Eskiden sadece "N sayfa tamamlandı" yazılıyordu — kaç ürün beklendiği hiç
    karşılaştırılmadığı için erken kesilen bir kategori de başarı gibi görünüyordu.
    Artık API'nin bildirdiği toplam ile gerçekten toplanan ürün sayısı kıyaslanır;
    bildirim yoksa (Eveshop) bunun DOĞRULANAMADIĞI açıkça söylenir — sessizce başarı
    varsayılmaz."""
    prefix = f"{site}: kategori {category_id} ({category_name})"
    if last_page is None:
        logger.error(f"{prefix} hiç sayfa döndürmedi")
        return

    declared = last_page.declared_total
    reason = last_page.stop_reason
    note = f", {blocked_count} bloklu/hatalı" if blocked_count else ""

    if reason == "http_error":
        logger.error(
            f"{prefix} EKSİK: HTTP {last_page.http_status} ile kesildi, "
            f"{page_count} sayfada {collected_items} ürün alındı"
            + (f" (beklenen ~{declared})" if declared else "")
        )
        return

    if reason == "max_pages":
        logger.error(f"{prefix} ANORMAL: sayfa güvenlik sınırında durduruldu ({page_count} sayfa)")
        return

    if declared is None:
        # Eveshop: toplam ürün sayısı bildirilmiyor, tek durma sinyali boş sayfa. Bu bitişin
        # gerçekten katalog sonu mu yoksa bir bot-engeli sayfası mı olduğu BURADAN anlaşılamaz;
        # kapsama doğrulaması ayrı bir adımda (sitemap karşılaştırması) yapılmalı.
        logger.info(
            f"{prefix} tamamlandı: {page_count} sayfa, {collected_items} ürün{note} "
            "(API toplam bildirmiyor - eksiksizlik doğrulanamadı)"
        )
        return

    if collected_items < declared:
        logger.warning(
            f"{prefix} EKSİK OLABİLİR: {collected_items}/{declared} ürün toplandı "
            f"({declared - collected_items} eksik, {page_count} sayfa, durma nedeni: {reason})"
        )
    else:
        logger.info(
            f"{prefix} tamamlandı: {page_count} sayfa, {collected_items}/{declared} ürün{note}"
        )


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
            collected_items = 0
            last_page: base_parser.CategoryPage | None = None

            async for result in base_parser.fetch_category_pages(config, category_id):
                object_name = write_category_page(
                    site, category_id, category_name, result.page, result.http_status, result.content,
                    extension=archive_extension, content_type=archive_content_type,
                )
                page_count += 1
                collected_items += result.item_count
                last_page = result
                if result.http_status == 200:
                    logger.info(
                        f"{site}: kategori {category_id} ({category_name}) sayfa {result.page} "
                        f"arşivlendi ({result.item_count} ürün) -> {object_name}"
                    )
                else:
                    blocked_count += 1
                    logger.warning(
                        f"{site}: kategori {category_id} ({category_name}) sayfa {result.page} "
                        f"HTTP {result.http_status} (BLOK/HATA) -> {object_name}"
                    )

            _log_completeness(site, category_id, category_name, page_count, blocked_count,
                              collected_items, last_page)
        except Exception as exc:
            logger.error(f"{site}: kategori {category_id} ({category_name}) işlenemedi - {exc}")


async def run_category_fetch() -> None:
    plugins = load_enabled_plugins()
    try:
        results = await asyncio.gather(
            *(fetch_site_categories(plugin) for plugin in plugins),
            return_exceptions=True,
        )
        for plugin, result in zip(plugins, results):
            if isinstance(result, Exception):
                logger.error(f"{plugin.SITE_NAME}: category fetch başarısız - {result}")
    finally:
        await http_client.close()


if __name__ == "__main__":
    asyncio.run(run_category_fetch())
