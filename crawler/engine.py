from __future__ import annotations

import asyncio
import json
import sqlite3
from datetime import date, datetime, timezone
from pathlib import Path

import yaml

from crawler.db import get_connection, upsert_site
from crawler.models import RobotsResult, Site
from crawler.robots_parser import fetch_robots_txt
from crawler.sitemap_parser import fetch_all_sitemaps
from shared import http_client
from shared.hashing import sha256_text
from shared.logging_config import get_logger
from shared.storage import storage

logger = get_logger(__name__)

SITES_CONFIG_PATH = Path(__file__).resolve().parent.parent / "configs" / "sites.yaml"
BUCKET = "datalake"
PLUGIN_VERSION = "0.1.0"


def load_enabled_sites() -> list[Site]:
    raw = yaml.safe_load(SITES_CONFIG_PATH.read_text(encoding="utf-8")) or {}
    return [Site(**entry) for entry in raw.get("sites", []) if entry.get("enabled")]


def sync_sites(conn: sqlite3.Connection, sites: list[Site]) -> dict[str, int]:
    site_ids = {
        site.name: upsert_site(conn, site.name, site.base_url, site.country, site.enabled)
        for site in sites
    }
    conn.commit()
    return site_ids


async def process_site_robots(site: Site, site_id: int, conn: sqlite3.Connection) -> RobotsResult:
    result = await fetch_robots_txt(site.base_url)
    content_hash = sha256_text(result.raw_text)
    now = datetime.now(timezone.utc).isoformat()

    row = conn.execute(
        "SELECT content_hash FROM robots_snapshots WHERE site_id = ?", (site_id,)
    ).fetchone()

    if row and row[0] == content_hash:
        conn.execute(
            "UPDATE robots_snapshots SET last_checked_at = ? WHERE site_id = ?",
            (now, site_id),
        )
        conn.commit()
        logger.info(f"{site.name}: robots.txt değişmedi, MinIO'ya yeniden yazılmadı")
        return result

    object_name = f"robots/{site.name}/{date.today().isoformat()}/{content_hash}.txt"
    meta = {
        "fetch_timestamp": now,
        "site": site.name,
        "plugin_version": PLUGIN_VERSION,
        "http_status": result.http_status,
        "content_hash": content_hash,
    }

    storage.put_bytes(BUCKET, object_name, result.raw_text.encode("utf-8"), content_type="text/plain")
    storage.put_bytes(
        BUCKET,
        f"{object_name}.meta.json",
        json.dumps(meta, ensure_ascii=False, indent=2).encode("utf-8"),
        content_type="application/json",
    )

    conn.execute(
        """
        INSERT INTO robots_snapshots (site_id, content_hash, http_status, object_name, last_checked_at)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(site_id) DO UPDATE SET
            content_hash=excluded.content_hash,
            http_status=excluded.http_status,
            object_name=excluded.object_name,
            last_checked_at=excluded.last_checked_at
        """,
        (site_id, content_hash, result.http_status, object_name, now),
    )
    conn.commit()

    snapshot_id = conn.execute(
        "SELECT id FROM robots_snapshots WHERE site_id = ?", (site_id,)
    ).fetchone()[0]

    # Snapshot değiştiği için endpoint listesi baştan yazılır (eski satırlar artık geçersiz).
    conn.execute("DELETE FROM robots_endpoints WHERE robots_snapshot_id = ?", (snapshot_id,))
    endpoint_rows = [
        (snapshot_id, site_id, "disallow", pattern, now) for pattern in result.disallow_patterns
    ] + [(snapshot_id, site_id, "allow", pattern, now) for pattern in result.allow_patterns]
    conn.executemany(
        """
        INSERT OR IGNORE INTO robots_endpoints (robots_snapshot_id, site_id, directive, pattern, discovered_at)
        VALUES (?, ?, ?, ?, ?)
        """,
        endpoint_rows,
    )
    conn.commit()

    if result.disallow_patterns:
        logger.info(
            f"{site.name}: {len(result.disallow_patterns)} disallow pattern kaydedildi "
            "(crawl edilmeyecek, sadece context/öğrenme amaçlı saklanıyor)"
        )

    logger.info(f"{site.name}: yeni robots.txt MinIO'ya yazıldı -> {object_name}")
    return result


async def process_site(site: Site, site_id: int, conn: sqlite3.Connection) -> None:
    robots_result = await process_site_robots(site, site_id, conn)
    if not robots_result.sitemap_urls:
        logger.warning(f"{site.name}: robots.txt içinde Sitemap: satırı bulunamadı")
        return
    await fetch_all_sitemaps(site, site_id, robots_result.sitemap_urls, conn)


# NOT: process_site_robots/process_site coroutine'leri asyncio.gather ile "paralel"
# çalıştırılıyor ama hepsi TEK bir sqlite3.Connection'ı (conn) paylaşıyor. Bu güvenli, çünkü
# asyncio tek thread'li kooperatif zamanlama yapar: sqlite3'ün senkron (await'siz) execute()/
# commit() çağrıları asla bölünmeden, tek seferde tamamlanır - iki coroutine'in aynı anda
# gerçekten aynı anda DB'ye yazması mümkün değil, sadece I/O beklerken (await fetch(...))
# sırayla el değiştiriyorlar. Ayrı connection açmak (WAL modunda bile) burada gereksiz ve
# hatta gerçek kilit çekişmesi riski ekleyebilirdi.


async def run_robots_discovery() -> None:
    sites = load_enabled_sites()
    conn = get_connection()
    try:
        site_ids = sync_sites(conn, sites)
        results = await asyncio.gather(
            *(process_site_robots(site, site_ids[site.name], conn) for site in sites),
            return_exceptions=True,
        )
        for site, result in zip(sites, results):
            if isinstance(result, Exception):
                logger.error(f"{site.name}: robots.txt işlenemedi - {result}")
    finally:
        conn.close()
        await http_client.close()


async def run_discovery() -> None:
    """robots.txt -> Sitemap: satırları -> recursive sitemap arşivleme (tüm siteler paralel)."""
    sites = load_enabled_sites()
    conn = get_connection()
    try:
        site_ids = sync_sites(conn, sites)
        results = await asyncio.gather(
            *(process_site(site, site_ids[site.name], conn) for site in sites),
            return_exceptions=True,
        )
        for site, result in zip(sites, results):
            if isinstance(result, Exception):
                logger.error(f"{site.name}: discovery işlenemedi - {result}")
    finally:
        conn.close()
        await http_client.close()


if __name__ == "__main__":
    asyncio.run(run_discovery())
