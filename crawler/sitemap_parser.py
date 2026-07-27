from __future__ import annotations

import gzip
import json
import sqlite3
from datetime import date, datetime, timezone
from xml.etree import ElementTree

from crawler.models import Site
from shared.hashing import sha256_text
from shared.http_client import fetch
from shared.logging_config import get_logger
from shared.storage import storage

logger = get_logger(__name__)

BUCKET = "datalake"
PLUGIN_VERSION = "0.1.0"
GZIP_MAGIC = b"\x1f\x8b"


def _decode_body(content: bytes) -> str:
    # Uzantıya değil magic byte'a bakılır: bazı siteler (örn. Watsons) gzip içeriği
    # .gz uzantısı olmayan bir URL üzerinden, content-type: application/gzip ile sunuyor.
    if content[:2] == GZIP_MAGIC:
        content = gzip.decompress(content)
    return content.decode("utf-8", errors="replace")


def _localname(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _extract_locs(xml_text: str) -> tuple[str, list[str]]:
    """Sitemap XML'ini parse eder.

    Döndürülen (root_tag, loc_listesi) ikilisinde root_tag 'sitemapindex' ise
    loc'lar alt sitemap adresleridir (recursion gerekir), 'urlset' ise loc'lar
    gerçek sayfa (kategori/ürün) adresleridir (leaf, recursion durur).
    """
    root = ElementTree.fromstring(xml_text)
    root_tag = _localname(root.tag)
    locs = [el.text.strip() for el in root.iter() if _localname(el.tag) == "loc" and el.text]
    return root_tag, locs


def _upsert_sitemap_urls(
    conn: sqlite3.Connection,
    site_id: int,
    sitemap_snapshot_id: int,
    current_urls: list[str],
    now: str,
) -> None:
    """urlset (leaf) sitemap'ten çıkan gerçek sayfa URL'lerini diff'ler.

    Yeni/değişmeyen URL'ler is_active=1 ile upsert edilir; artık listede
    olmayan URL'ler silinmez, is_active=0 ile soft-delete edilir.
    """
    existing = {
        row[0]
        for row in conn.execute(
            "SELECT url FROM sitemap_urls WHERE sitemap_snapshot_id = ? AND is_active = 1",
            (sitemap_snapshot_id,),
        )
    }
    current_set = set(current_urls)

    for url in current_urls:
        conn.execute(
            """
            INSERT INTO sitemap_urls (site_id, sitemap_snapshot_id, url, is_active, first_seen_at, last_seen_at)
            VALUES (?, ?, ?, 1, ?, ?)
            ON CONFLICT(site_id, url) DO UPDATE SET
                sitemap_snapshot_id = excluded.sitemap_snapshot_id,
                is_active = 1,
                last_seen_at = excluded.last_seen_at
            """,
            (site_id, sitemap_snapshot_id, url, now, now),
        )

    stale_urls = existing - current_set
    for stale_url in stale_urls:
        conn.execute(
            "UPDATE sitemap_urls SET is_active = 0, last_seen_at = ? WHERE site_id = ? AND url = ?",
            (now, site_id, stale_url),
        )

    conn.commit()
    if stale_urls:
        logger.info(f"{sitemap_snapshot_id}: {len(stale_urls)} URL artık listede yok, is_active=0 yapıldı")


async def _fetch_and_archive(
    site: Site,
    site_id: int,
    url: str,
    parent_sitemap_id: int | None,
    conn: sqlite3.Connection,
) -> tuple[int, str, list[str]] | None:
    response = await fetch(url)
    if response.status_code != 200:
        logger.warning(f"{site.name}: sitemap fetch başarısız {url} -> {response.status_code}")
        return None

    xml_text = _decode_body(response.content)

    try:
        root_tag, locs = _extract_locs(xml_text)
    except ElementTree.ParseError as exc:
        logger.warning(f"{site.name}: sitemap parse edilemedi {url} - {exc}")
        return None

    # Hash'ten önce normalize et: loc listesini sort edip hash'le (whitespace/sıra
    # farkı false-positive değişiklik yaratmasın).
    content_hash = sha256_text("\n".join(sorted(locs)))
    now = datetime.now(timezone.utc).isoformat()

    row = conn.execute(
        "SELECT id, content_hash FROM sitemap_snapshots WHERE site_id = ? AND url = ?",
        (site_id, url),
    ).fetchone()

    if row and row[1] == content_hash:
        snapshot_id = row[0]
        conn.execute(
            "UPDATE sitemap_snapshots SET last_checked_at = ? WHERE id = ?",
            (now, snapshot_id),
        )
        conn.commit()
        logger.info(f"{site.name}: sitemap değişmedi, MinIO'ya yeniden yazılmadı -> {url}")
        return snapshot_id, root_tag, locs

    object_name = f"sitemaps/{site.name}/{date.today().isoformat()}/{content_hash}.xml"
    meta = {
        "fetch_timestamp": now,
        "site": site.name,
        "plugin_version": PLUGIN_VERSION,
        "http_status": response.status_code,
        "content_hash": content_hash,
        "source_url": url,
        "root_tag": root_tag,
    }

    storage.put_bytes(BUCKET, object_name, xml_text.encode("utf-8"), content_type="application/xml")
    storage.put_bytes(
        BUCKET,
        f"{object_name}.meta.json",
        json.dumps(meta, ensure_ascii=False, indent=2).encode("utf-8"),
        content_type="application/json",
    )

    conn.execute(
        """
        INSERT INTO sitemap_snapshots
            (site_id, parent_sitemap_id, url, root_tag, content_hash, http_status, object_name, last_checked_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(site_id, url) DO UPDATE SET
            parent_sitemap_id=excluded.parent_sitemap_id,
            root_tag=excluded.root_tag,
            content_hash=excluded.content_hash,
            http_status=excluded.http_status,
            object_name=excluded.object_name,
            last_checked_at=excluded.last_checked_at
        """,
        (site_id, parent_sitemap_id, url, root_tag, content_hash, response.status_code, object_name, now),
    )
    conn.commit()

    snapshot_id = conn.execute(
        "SELECT id FROM sitemap_snapshots WHERE site_id = ? AND url = ?", (site_id, url)
    ).fetchone()[0]

    if root_tag == "urlset":
        _upsert_sitemap_urls(conn, site_id, snapshot_id, locs, now)

    logger.info(f"{site.name}: sitemap MinIO'ya yazıldı ({root_tag}, {len(locs)} loc) -> {object_name}")
    return snapshot_id, root_tag, locs


async def fetch_all_sitemaps(site: Site, site_id: int, root_sitemap_urls: list[str], conn: sqlite3.Connection) -> None:
    """robots.txt'teki `Sitemap:` satırlarından başlayıp, sitemapindex ise son
    child'a (leaf urlset) kadar recursive olarak tüm sitemap dosyalarını çekip
    MinIO'ya arşivler; leaf urlset'lerdeki gerçek sayfa URL'lerini sitemap_urls
    tablosuna diff'leyerek yazar.
    """
    visited: set[str] = set()
    queue: list[tuple[str, int | None]] = [(url, None) for url in root_sitemap_urls]

    while queue:
        url, parent_id = queue.pop(0)
        if url in visited:
            continue
        visited.add(url)

        result = await _fetch_and_archive(site, site_id, url, parent_id, conn)
        if result is None:
            continue

        snapshot_id, root_tag, locs = result
        if root_tag == "sitemapindex":
            queue.extend((loc, snapshot_id) for loc in locs if loc not in visited)
