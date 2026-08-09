from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from scripts._lock import single_instance
from shared.logging_config import get_logger

logger = get_logger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# robots.txt + sitemap keşfi, sonra Postgres senkronu - fiyat zincirinden AYRI ve daha seyrek
# çalışır (bkz. configs/schedule.yaml -> crawler_discovery), sitemap saatlik değişmiyor.
STAGES = [
    ["crawler.cli", "sitemaps"],
    ["crawler.cli", "sync"],
]


def run_stage(args: list[str]) -> bool:
    label = " ".join(args)
    logger.info(f"Başlıyor: {label}")
    result = subprocess.run([sys.executable, "-m", *args], cwd=PROJECT_ROOT)
    if result.returncode != 0:
        logger.error(f"{label}: başarısız (exit {result.returncode}), sonraki aşamaya geçiliyor")
        return False
    logger.info(f"Bitti: {label}")
    return True


def main() -> None:
    with single_instance("discovery"):
        failed = [" ".join(s) for s in STAGES if not run_stage(s)]
        if failed:
            logger.warning(f"{len(failed)} aşama başarısız oldu: {failed}")
        else:
            logger.info("Tüm aşamalar başarıyla tamamlandı")


if __name__ == "__main__":
    main()
