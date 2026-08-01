"""
GEÇİCİ DEBUG SCRIPT - production koduna dahil değil, kullanılmadığında silinebilir.

Gratis kategori API'sindeki pagination drift'in (ürün kaybı/tekrarı) kök sebebini izole eder:
sort mu, page_size mi, yoksa ikisi birden mi asıl çözüm? 2x2 faktöriyel test, tek kategori
(varsayılan: 501 - configs/tr/gratis.yaml'da bu "Makyaj", "Parfüm" değil (parfüm=504) - CLI
argümanıyla değiştirilebilir).

Diğer alanlar (inStock/filterActiveProducts/fromHomepageBestsellers) tüm testlerde SABİT
tutulur - sadece sort ve page_size değişir, izolasyon bunlarla sınırlı.

Kullanım: python core/debug/pagination_test.py [category_id]
Çıktı: matching/exploration/pagination_findings.md
"""
from __future__ import annotations

import asyncio
import base64
import json
import sys
import time
from collections import Counter
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

from shared.http_client import fetch, close  # noqa: E402
from shared.logging_config import get_logger  # noqa: E402

logger = get_logger(__name__)

URL = "https://api.gratis.retter.io/1oakekr4e/CALL/Search/search/default"
DELAY_SECONDS = 1.5
MAX_RETRIES = 3

TESTS = [
    {"name": "A", "sort": False, "page_size": 100},
    {"name": "B", "sort": True, "page_size": 100},
    {"name": "C", "sort": False, "page_size": 25},
    {"name": "D", "sort": True, "page_size": 25},  # mevcut/bilinen config, referans
]


def build_payload(category_id: str, offset: int, size: int, use_sort: bool) -> dict:
    query = {
        "searchTerm": "",
        "from": offset,
        "size": size,
        "filters": [{"filterId": "categories", "filterValues": [category_id]}],
        "inStock": False,
        "sortBy": [{"attribute": "createdAt", "order": "asc"}] if use_sort else [],
        "filterActiveProducts": True,
        "fromHomepageBestsellers": False,
    }
    return {"query": query}


async def fetch_page(category_id: str, offset: int, size: int, use_sort: bool) -> dict | None:
    payload = build_payload(category_id, offset, size, use_sort)
    b64 = base64.b64encode(json.dumps(payload).encode()).decode()
    params = {"__culture": "tr_TR", "__platform": "WEB", "data": b64, "__isbase64": "true"}

    response = None
    for attempt in range(1, MAX_RETRIES + 1):
        response = await fetch(URL, params=params)
        if response.status_code == 200:
            return response.json()
        logger.warning(f"offset={offset}: HTTP {response.status_code}, deneme {attempt}/{MAX_RETRIES}")
        await asyncio.sleep(2.0 * attempt)
    logger.error(f"offset={offset}: {MAX_RETRIES} denemede başarısız, bu sayfa atlanıyor")
    return None


async def run_test(name: str, category_id: str, size: int, use_sort: bool) -> dict:
    all_ids: list[str] = []
    item_count = 0
    offset = 0
    page = 0
    t0 = time.monotonic()

    while True:
        if page > 0:
            await asyncio.sleep(DELAY_SECONDS)

        data = await fetch_page(category_id, offset, size, use_sort)
        page += 1
        if data is None:
            break

        item_count = data.get("itemCount", 0)
        items = data.get("data", [])
        page_ids = [item["id"] for item in items]
        all_ids.extend(page_ids)

        offset += size
        if not items or offset >= item_count:
            break

    unique_ids = set(all_ids)
    counter = Counter(all_ids)
    duplicates = {pid: c for pid, c in counter.items() if c > 1}
    missing_pct = (item_count - len(unique_ids)) / item_count * 100 if item_count else 0.0
    elapsed = time.monotonic() - t0

    logger.info(
        f"Test {name} bitti ({elapsed:.0f}s, {page} sayfa): expected={item_count}, "
        f"unique={len(unique_ids)}, kayıp=%{missing_pct:.2f}, mükerrer={len(duplicates)}"
    )

    return {
        "name": name,
        "sort": use_sort,
        "page_size": size,
        "expected_total": item_count,
        "unique_fetched": len(unique_ids),
        "missing_pct": missing_pct,
        "duplicate_count": len(duplicates),
        "unique_ids": unique_ids,
        "duplicates": duplicates,
        "page_count": page,
        "elapsed_seconds": elapsed,
    }


def write_report(category_id: str, results: list[dict]) -> Path:
    out_dir = PROJECT_ROOT / "matching" / "exploration"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "pagination_findings.md"

    reference = next((r for r in results if r["name"] == "D"), results[-1])
    ref_ids = reference["unique_ids"]

    lines = [
        "# Gratis Kategori API Pagination Drift - Kök Sebep İzolasyon Testi",
        "",
        f"Kategori: `{category_id}` | Test sırası: A -> B -> C -> D (art arda, aynı zaman diliminde) | "
        f"Diğer alanlar (inStock/filterActiveProducts/fromHomepageBestsellers) tüm testlerde SABİT.",
        "",
        "| Test | sort | page_size | expected_total | unique_fetched | kayıp_% | mükerrer_sayı | sayfa | süre(s) |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for r in results:
        sort_label = "VAR (createdAt asc)" if r["sort"] else "YOK"
        lines.append(
            f"| {r['name']} | {sort_label} | {r['page_size']} | {r['expected_total']} | "
            f"{r['unique_fetched']} | %{r['missing_pct']:.2f} | {r['duplicate_count']} | "
            f"{r['page_count']} | {r['elapsed_seconds']:.0f} |"
        )
    lines.append("")

    for r in results:
        sort_label = "VAR" if r["sort"] else "YOK"
        lines.append(f"## Test {r['name']} detay (sort={sort_label}, page_size={r['page_size']})")
        if r["name"] != "D":
            missing_vs_ref = ref_ids - r["unique_ids"]
            lines.append(f"- Test D'ye (referans) göre eksik ürün sayısı: {len(missing_vs_ref)}")
            if missing_vs_ref:
                lines.append(f"- Eksik ID'ler: {sorted(missing_vs_ref)}")
        if r["duplicates"]:
            lines.append(f"- Kendi içinde mükerrer ID'ler ({len(r['duplicates'])} adet): {r['duplicates']}")
        else:
            lines.append("- Kendi içinde mükerrer ID yok.")
        lines.append("")

    out_path.write_text("\n".join(lines), encoding="utf-8")
    return out_path


async def main() -> None:
    category_id = sys.argv[1] if len(sys.argv) > 1 else "501"
    logger.info(f"Pagination izolasyon testi başlıyor: category_id={category_id}")

    results = []
    for t in TESTS:
        logger.info(f"=== Test {t['name']}: sort={t['sort']}, page_size={t['page_size']} ===")
        result = await run_test(t["name"], category_id, t["page_size"], t["sort"])
        results.append(result)

    await close()

    out_path = write_report(category_id, results)
    logger.info(f"Rapor yazıldı -> {out_path}")


if __name__ == "__main__":
    asyncio.run(main())
