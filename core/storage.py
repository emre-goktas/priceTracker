from __future__ import annotations

from datetime import date, datetime, timezone

from shared.hashing import sha256_bytes
from shared.storage import storage

BUCKET = "datalake"
PLUGIN_VERSION = "0.1.0"


def write_category_page(site: str, category_id: str, page: int, http_status: int, content: bytes) -> str:
    """Bir kategori sayfasının ham yanıtını MinIO'ya arşivler.

    Şema: category/{site}/{YYYY-MM-DD}/{category_id}/{sha256}.json (+ .meta.json sidecar).
    Ham veri her zaman önce arşivlenir, işleme (field_mapping/Postgres) sonraki adımdır.
    """
    content_hash = sha256_bytes(content)
    object_name = f"category/{site}/{date.today().isoformat()}/{category_id}/{content_hash}.json"
    meta = {
        "fetch_timestamp": datetime.now(timezone.utc).isoformat(),
        "site": site,
        "category_id": category_id,
        "page": page,
        "plugin_version": PLUGIN_VERSION,
        "http_status": http_status,
        "content_hash": content_hash,
    }
    storage.put_json_with_meta(BUCKET, object_name, content, meta)
    return object_name
