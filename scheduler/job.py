"""
APScheduler-based periodic job runner.
Calls the full pipeline: scrape → normalize → store → analyze → alert.
"""

from apscheduler.schedulers.blocking import BlockingScheduler

from config.settings import settings
from scrapers.manager import ScraperManager
from normalizer.normalizer import normalize_batch
from storage.database import init_db, Repository, get_engine
from analytics.engine import AnalyticsEngine
from alerts.telegram import TelegramAlerter
from sqlalchemy.orm import Session

# Import enabled scrapers
from scrapers.vatan import VatanScraper


def run_pipeline():
    print("[Scheduler] Pipeline started.")
    engine = get_engine(settings.db_url)

    manager = ScraperManager(scrapers=[VatanScraper()])
    raw = manager.run_all()
    products = normalize_batch(raw)

    analytics = AnalyticsEngine(
        strategy=settings.analytics_strategy,
        threshold=settings.alert_threshold,
    )
    alerter = TelegramAlerter(
        bot_token=settings.telegram_bot_token,
        chat_id=settings.telegram_chat_id,
    )

    with Session(engine) as session:
        repo = Repository(session)
        for product in products:
            repo.upsert_product(product)
            repo.add_price_record(product)
            history = repo.get_recent_prices(product.id, limit=30)
            anomaly = analytics.analyze(product.id, product.price, history)
            if anomaly and not anomaly.requires_review and product.is_in_stock:
                alerter.send(anomaly, product.name, product.url)
        session.commit()

    print(f"[Scheduler] Done. Processed {len(products)} products.")


def start():
    scheduler = BlockingScheduler()
    scheduler.add_job(run_pipeline, "interval", minutes=settings.scrape_interval_minutes)
    print(f"[Scheduler] Running every {settings.scrape_interval_minutes} minutes.")
    scheduler.start()


if __name__ == "__main__":
    start()
