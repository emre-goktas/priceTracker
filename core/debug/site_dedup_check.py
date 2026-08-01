"""
GEÇİCİ DEBUG SCRIPT - production koduna dahil değil, kullanılmadığında silinebilir.

Gratis'te bulunan "kararsız sıralama -> pagination drift" (aynı ürün sayfalar arası kayıyor,
bazı ürünler hiç görünmüyor) hatasının Watsons/Rossmann/Eveshop'ta da olup olmadığını kontrol
eder. pagination_test.py'nin aksine (Gratis'e özel, sort/page_size'ı elle değiştiren 2x2 test)
bu script site adı bilmez - core/parsers/base_parser.fetch_category_pages'i (üretimde kullanılan
GERÇEK fetch yolu) configs/tr/{site}.yaml üzerinden çağırır, ekstra parametre denemez. Amaç:
"şu anki production config, bir kategoriyi eksiksiz/tekrarsız mı çekiyor?" sorusuna cevap.

Kullanım: python core/debug/site_dedup_check.py <site> <category_id>
Örnek:    python core/debug/site_dedup_check.py watsons 100
          python core/debug/site_dedup_check.py rossmann 3
          python core/debug/site_dedup_check.py eveshop makyaj
"""
from __future__ import annotations

import asyncio
import importlib
import sys
from collections import Counter
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

from core.parsers.base_parser import fetch_category_pages, parse_response_body  # noqa: E402
from shared.http_client import close  # noqa: E402
from shared.jsonpath import resolve_path  # noqa: E402
from shared.logging_config import get_logger  # noqa: E402

logger = get_logger(__name__)


def _as_list(value: object) -> list:
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def _flatten_items(items: list) -> list[dict]:
    """html_regex_json formatında (örn. Eveshop) her regex eşleşmesi kendi içinde bir JSON
    dizisi olabilir (bkz. configs/tr/eveshop.yaml x-labels-data) - matching/duckdb_pipeline.py
    (parse_eveshop_html) bunu tek seviye flatten ediyor, burada da aynı davranış tekrarlanır."""
    flat: list[dict] = []
    for entry in items:
        if isinstance(entry, list):
            flat.extend(e for e in entry if isinstance(e, dict))
        elif isinstance(entry, dict):
            flat.append(entry)
    return flat


async def run(site: str, category_id: str) -> None:
    plugin = importlib.import_module(f"core.parsers.site_plugins.{site}")
    config = plugin.load_config()
    api_cfg = config["category_search_api"]
    response_cfg = api_cfg["response"]
    items_path = response_cfg.get("items_path")
    product_id_field = config["product_id_field"]

    all_ids: list[str] = []
    pages = 0
    expected_total = None

    async for page_no, status, content in fetch_category_pages(config, category_id):
        pages += 1
        if status != 200:
            logger.error(f"sayfa {page_no}: HTTP {status}, durduruluyor")
            break

        data = parse_response_body(content, response_cfg)
        items = _as_list(resolve_path(data, items_path)) if items_path else _as_list(data)
        if response_cfg.get("format") == "html_regex_json":
            items = _flatten_items(items)

        total_count_path = response_cfg.get("total_count_path")
        if total_count_path:
            count = resolve_path(data, total_count_path)
            if count:
                expected_total = int(count)

        for item in items:
            pid = resolve_path(item, product_id_field)
            if pid is not None:
                all_ids.append(str(pid))

        logger.info(f"sayfa {page_no}: {len(items)} ürün (kümülatif ham: {len(all_ids)})")

    await close()

    unique_ids = set(all_ids)
    counter = Counter(all_ids)
    duplicates = {pid: c for pid, c in counter.items() if c > 1}

    print()
    print(f"=== {site} / kategori {category_id} ===")
    print(f"sayfa sayısı        : {pages}")
    print(f"beklenen toplam      : {expected_total if expected_total is not None else 'API bildirmiyor'}")
    print(f"ham çekilen          : {len(all_ids)}")
    print(f"benzersiz            : {len(unique_ids)}")
    if expected_total:
        missing_pct = (expected_total - len(unique_ids)) / expected_total * 100
        print(f"kayıp (beklenene göre): %{missing_pct:.2f}")
    print(f"mükerrer ID sayısı   : {len(duplicates)}")
    if duplicates:
        sample = dict(list(duplicates.items())[:20])
        print(f"mükerrer örnek (ilk 20): {sample}")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print("Kullanım: python core/debug/site_dedup_check.py <site> <category_id>")
        sys.exit(1)
    asyncio.run(run(sys.argv[1], sys.argv[2]))
