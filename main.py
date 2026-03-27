"""
Entry point.

Usage:
  python main.py            # start scheduler (runs every N minutes)
  python main.py --dry-run  # scrape once, print results, skip DB + Telegram
"""

import asyncio
import logging
import sys

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

from config.settings import settings
from scheduler.job import run_pipeline


async def _start_scheduler():
    from apscheduler.schedulers.asyncio import AsyncIOScheduler

    scheduler = AsyncIOScheduler()
    scheduler.add_job(
        run_pipeline,
        "interval",
        minutes=settings.scrape_interval_minutes,
        id="pipeline",
        replace_existing=True,
    )
    scheduler.start()
    logging.info(
        "[Main] Scheduler started. Running every %d minutes.",
        settings.scrape_interval_minutes,
    )
    # Keep alive
    try:
        while True:
            await asyncio.sleep(60)
    except (KeyboardInterrupt, SystemExit):
        scheduler.shutdown()
        logging.info("[Main] Scheduler stopped.")


async def _main():
    dry_run = "--dry-run" in sys.argv

    if dry_run:
        logging.info("[Main] Dry-run mode – single scrape, no DB, no Telegram.")
        await run_pipeline(dry_run=True)
    else:
        # Run once immediately before starting the scheduler loop
        await run_pipeline()
        await _start_scheduler()


if __name__ == "__main__":
    asyncio.run(_main())
