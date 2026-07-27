from __future__ import annotations

import argparse
import asyncio

from crawler.engine import run_discovery, run_robots_discovery


def main() -> None:
    parser = argparse.ArgumentParser(description="price-bot crawler CLI")
    parser.add_argument(
        "command",
        choices=["robots", "sitemaps"],
        help=(
            "robots: sadece robots.txt çek+arşivle. "
            "sitemaps: robots.txt üzerinden Sitemap: satırlarını bulup "
            "tüm sitemap dosyalarını (son child'a kadar) çek+arşivle."
        ),
    )
    args = parser.parse_args()

    if args.command == "robots":
        asyncio.run(run_robots_discovery())
    elif args.command == "sitemaps":
        asyncio.run(run_discovery())


if __name__ == "__main__":
    main()
