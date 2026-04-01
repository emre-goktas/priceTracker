"""
Async pipeline job orchestrated by APScheduler.
Flow: scrape → normalize → store → analyze → alert
"""

import asyncio
import logging

from config.settings import settings
from scrapers.manager import ScraperManager
from scrapers.vatan import VatanScraper
from scrapers.hepsiburada import HepsiburadaScraper
from normalizer.normalizer import normalize_batch
from storage.database import init_db, make_session_factory, Repository
from analytics.engine import AnalyticsEngine
from alerts.telegram import TelegramAlerter

logger = logging.getLogger(__name__)


async def run_pipeline(dry_run: bool = False):
    logger.info("[Pipeline] Starting scrape run (dry_run=%s)…", dry_run)

    # --- Scrape ---
    manager = ScraperManager(scrapers=[VatanScraper(), HepsiburadaScraper()])
    raw = await manager.run_all()

    if not raw:
        logger.warning("[Pipeline] No products scraped this run.")
        return

    # --- Normalize ---
    products = normalize_batch(raw)
    logger.info("[Pipeline] Normalised %d products.", len(products))

    if dry_run:
        for p in products[:5]:
            logger.info("[DRY-RUN] %s | %.2f TL | stock=%s", p.name, p.price, p.is_in_stock)
        return

    # --- Store & Analyze ---
    engine = await init_db(settings.db_url)
    factory = make_session_factory(engine)

    analytics = AnalyticsEngine(
        strategy=settings.analytics_strategy,
        threshold=settings.alert_threshold,
        z_threshold=settings.z_threshold,
    )
    alerter = TelegramAlerter(
        bot_token=settings.telegram_bot_token,
        chat_id=settings.telegram_chat_id,
    )

    async with factory() as session:
        repo = Repository(session)
        alert_count = 0

        for product in products:
            await repo.upsert_product(product)
            await repo.add_price_record(product)
            history = await repo.get_recent_prices(product.id, limit=30)

            # Skip analysis on first-ever record (no history to compare)
            if len(history) < 2:
                continue

            anomaly = analytics.analyze(product.id, product.price, history)
            if anomaly:
                if anomaly.requires_review:
                    logger.warning(
                        "[Pipeline] ⚠ Circuit breaker: %s dropped %.0f%% – skipping alert",
                        product.name, anomaly.drop_pct * 100,
                    )
                elif not product.is_in_stock:
                    logger.info("[Pipeline] Price drop but out-of-stock, skipping: %s", product.name)
                else:
                    logger.info(
                        "[Pipeline] 🔥 Alert: %s | %.2f → %.2f TL (%.0f%% drop)",
                        product.name, anomaly.old_avg, anomaly.new_price, anomaly.drop_pct * 100,
                    )
                    alerter.send(anomaly, product.name, product.url)
                    alert_count += 1

        await session.commit()

    logger.info("[Pipeline] Done. Alerts sent: %d", alert_count)
    await engine.dispose()
