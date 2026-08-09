from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from scripts._lock import single_instance
from shared.logging_config import get_logger

logger = get_logger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# category_fetcher -> duckdb_pipeline -> build_clean -> normalize -> sync_to_postgres ->
# alert_engine. Alt modüller import EDİLMEZ (mimari ilke 1) - her aşama ayrı bir subprocess
# olarak çalışır, crawler/sync_to_postgres.py'nin CLI konvansiyonuyla tutarlı. Bir aşama
# başarısız olsa bile sonraki aşamaya geçilir (best-effort) - her aşamanın kendi iç per-site
# izolasyonu (bkz. duckdb_pipeline.load_all/normalize.build/build_clean.main) zaten sitesel
# hataları yutuyor; burada durdurmama sebebi sadece aşama-seviyesi bir çökmenin (örn. Postgres
# o an kapalıysa) diğer aşamaları da tamamen engellememesi.
STAGES = [
    "core.fetchers.category_fetcher",
    "matching.duckdb_pipeline",
    "matching.analysis.build_clean",
    "matching.normalize",
    "matching.sync_to_postgres",
    "content.alert_engine",
]


def run_stage(module: str) -> bool:
    logger.info(f"Başlıyor: {module}")
    result = subprocess.run([sys.executable, "-m", module], cwd=PROJECT_ROOT)
    if result.returncode != 0:
        logger.error(f"{module}: başarısız (exit {result.returncode}), sonraki aşamaya geçiliyor")
        return False
    logger.info(f"Bitti: {module}")
    return True


def main() -> None:
    with single_instance("price_pipeline"):
        failed = [module for module in STAGES if not run_stage(module)]
        if failed:
            logger.warning(f"{len(failed)} aşama başarısız oldu: {failed}")
        else:
            logger.info("Tüm aşamalar başarıyla tamamlandı")


if __name__ == "__main__":
    main()
