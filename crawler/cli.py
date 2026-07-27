from __future__ import annotations

import argparse
import asyncio

from crawler.engine import run_discovery, run_robots_discovery
from crawler.sync_to_postgres import run_sync


def main() -> None:
    parser = argparse.ArgumentParser(description="price-bot crawler CLI")
    parser.add_argument(
        "command",
        choices=["robots", "sitemaps", "sync"],
        help=(
            "robots: sadece robots.txt çek+arşivle. "
            "sitemaps: robots.txt üzerinden Sitemap: satırlarını bulup "
            "tüm sitemap dosyalarını (son child'a kadar) çek+arşivle. "
            "sync: crawler.db'deki aktif URL'leri core.discovered_urls'a (Postgres) sync et."
        ),
    )
    args = parser.parse_args()

    if args.command == "robots":
        asyncio.run(run_robots_discovery())
    elif args.command == "sitemaps":
        asyncio.run(run_discovery())
    elif args.command == "sync":
        asyncio.run(run_sync())


if __name__ == "__main__":
    main()
