from __future__ import annotations

import gzip
import io
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

# Bir sitemap'te önceden aktif olan URL'lerin bu orandan fazlası tek seferde
# "kayboldu" görünürse, bunu gerçek bir diff değil şüpheli bir fetch/parse
# anomalisi (blok, boş yanıt, site yapısı değişikliği) sayıp soft-delete'i atla.
#
# TODO (refactor'da tekrar düşün, 2026-07-30): Bu eşik, _extract_locs'taki gerçek bir
# parser bug'ını (image:loc'ların sayfa URL'i sanılması, bkz. git geçmişi) düzelttiğimizde
# Rossmann için kendi kendini onarmayı ENGELLEDİ (gerçek oran %73-78, eşiği aştı) - o site
# manuel bir one-off script'le (bkz. proje hafızası/commit geçmişi) düzeltildi, kalıcı bir
# çözüm değil. İleride "bilinen bir kod değişikliğinden kaynaklanan kütlesel diff" ile
# "gerçek bot-bloğu/arıza" senaryolarını ayırt edecek bir mekanizma (örn. CLI'da açık bir
# --force/--reason bayrağı) düşünülmeli - şu anki tek yol kodun kendisine dokunmadan DB'yi
# elle düzeltmek, bu kırılgan ve tekrarlanabilir değil.
MASS_DEACTIVATION_SAFETY_RATIO = 0.5


def _decompress_if_needed(content: bytes) -> bytes:
    # Uzantıya değil magic byte'a bakılır: bazı siteler (örn. Watsons) gzip içeriği
    # .gz uzantısı olmayan bir URL üzerinden, content-type: application/gzip ile sunuyor.
    if content[:2] == GZIP_MAGIC:
        return gzip.decompress(content)
    return content


def _localname(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _extract_locs(content: bytes) -> tuple[str, list[str]]:
    """Sitemap XML'ini akışlı (iterparse) parse eder; tüm DOM'u belleğe yüklemez.

    Büyük sitemap dosyalarında (onbinlerce <url>) ElementTree.fromstring ile tüm
    ağacı belleğe kurmak yerine, her elementi işledikten sonra elem.clear() ile
    bellekten düşürülür.

    Döndürülen (root_tag, loc_listesi) ikilisinde root_tag 'sitemapindex' ise
    loc'lar alt sitemap adresleridir (recursion gerekir), 'urlset' ise loc'lar
    gerçek sayfa (kategori/ürün) adresleridir (leaf, recursion durur).

    SADECE <url>/<sitemap>'in DOĞRUDAN çocuğu olan <loc> alınır. Google'ın image/video
    sitemap eklentisi (`xmlns:image`/`xmlns:video`) <url> içine `<image:image><image:loc>...`
    gibi CDN görsel/video URL'leri gömer - bunlar da namespace'siz local-name'de "loc" olduğu
    için önceden (ata kontrolü yokken) sayfa URL'iymiş gibi karışıyordu (örn. Gratis'te ~2x,
    Rossmann'da ~3.76x şişme - ampirik olarak doğrulandı, 2026-07-30). Ebeveyn takibi bunu ayırt eder.
    """
    root_tag: str | None = None
    locs: list[str] = []
    ancestors: list[str] = []

    for event, elem in ElementTree.iterparse(io.BytesIO(content), events=("start", "end")):
        tag = _localname(elem.tag)
        if event == "start":
            if root_tag is None:
                root_tag = tag
            ancestors.append(tag)
            continue

        ancestors.pop()  # kendini çıkar - kalan üstteki eleman gerçek ebeveyn
        parent_tag = ancestors[-1] if ancestors else None
        if tag == "loc" and elem.text and parent_tag in ("url", "sitemap"):
            locs.append(elem.text.strip())
        elem.clear()

    if root_tag is None:
        raise ElementTree.ParseError("boş ya da geçersiz sitemap XML")

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
    olmayan URL'ler silinmez, is_active=0 ile soft-delete edilir. Ancak
    kütle halinde (MASS_DEACTIVATION_SAFETY_RATIO üstü) bir düşüş görülürse
    bu büyük ihtimalle gerçek bir değişiklik değil, geçici bir blok/boş yanıt/
    site yapı değişikliğidir — bu durumda BATCH'İN TAMAMI şüpheli sayılır ve
    hiçbir satıra dokunulmaz (ne upsert ne soft-delete): güvenlik kontrolü
    herhangi bir DB yazımından ÖNCE yapılır, sadece "eski aktif kayıtları
    deaktive etme" değil, "şüpheli/az sayıdaki current_urls'i de aktif diye
    yazma" riskini de önler.
    """
    existing = {
        row[0]
        for row in conn.execute(
            "SELECT url FROM sitemap_urls WHERE sitemap_snapshot_id = ? AND is_active = 1",
            (sitemap_snapshot_id,),
        )
    }
    current_set = set(current_urls)
    stale_urls = existing - current_set

    if existing and (not current_urls or len(stale_urls) / len(existing) > MASS_DEACTIVATION_SAFETY_RATIO):
        logger.warning(
            f"sitemap_snapshot {sitemap_snapshot_id}: {len(stale_urls)}/{len(existing)} URL aniden "
            "kayboldu görünüyor, muhtemelen blok/boş yanıt/site değişikliği - TÜM batch şüpheli "
            "sayıldı, hiçbir satır yazılmadı (mevcut aktif kayıtlar korunuyor, manuel inceleme gerekebilir)"
        )
        return

    now_pairs = [(site_id, sitemap_snapshot_id, url, now, now) for url in current_urls]
    conn.executemany(
        """
        INSERT INTO sitemap_urls (site_id, sitemap_snapshot_id, url, is_active, first_seen_at, last_seen_at)
        VALUES (?, ?, ?, 1, ?, ?)
        ON CONFLICT(site_id, url) DO UPDATE SET
            sitemap_snapshot_id = excluded.sitemap_snapshot_id,
            is_active = 1,
            last_seen_at = excluded.last_seen_at
        """,
        now_pairs,
    )

    if stale_urls:
        conn.executemany(
            "UPDATE sitemap_urls SET is_active = 0, last_seen_at = ? WHERE site_id = ? AND url = ?",
            [(now, site_id, stale_url) for stale_url in stale_urls],
        )

    conn.commit()
    if stale_urls:
        logger.info(f"sitemap_snapshot {sitemap_snapshot_id}: {len(stale_urls)} URL artık listede yok, is_active=0 yapıldı")


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

    content = _decompress_if_needed(response.content)

    try:
        root_tag, locs = _extract_locs(content)
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

    storage.put_bytes(BUCKET, object_name, content, content_type="application/xml")
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

    Kuyruktaki bir URL beklenmedik şekilde patlarsa (ör. ağ hatası, retry'ler
    tükendi), sadece o dal loglanıp atlanır - aynı sitenin diğer kardeş
    sitemap'leri bu çalıştırmada işlenmeye devam eder (bir sonraki çalıştırmada
    hash tabanlı idempotency sayesinde eksik kalan kısım zaten yeniden denenir).
    """
    visited: set[str] = set()
    queue: list[tuple[str, int | None]] = [(url, None) for url in root_sitemap_urls]

    while queue:
        url, parent_id = queue.pop(0)
        if url in visited:
            continue
        visited.add(url)

        try:
            result = await _fetch_and_archive(site, site_id, url, parent_id, conn)
        except Exception as exc:
            logger.error(f"{site.name}: sitemap işlenirken beklenmeyen hata {url} - {exc}")
            continue

        if result is None:
            continue

        snapshot_id, root_tag, locs = result
        if root_tag == "sitemapindex":
            queue.extend((loc, snapshot_id) for loc in locs if loc not in visited)
