from __future__ import annotations

import re
from datetime import date, datetime, timezone

from shared.hashing import sha256_bytes
from shared.storage import storage

BUCKET = "datalake"
PLUGIN_VERSION = "0.1.0"

_TR_CHARS = str.maketrans({
    "İ": "i", "I": "i", "ı": "i",
    "Ğ": "g", "ğ": "g",
    "Ü": "u", "ü": "u",
    "Ş": "s", "ş": "s",
    "Ö": "o", "ö": "o",
    "Ç": "c", "ç": "c",
})


def slugify(name: str) -> str:
    """Kategori adını MinIO klasör adında güvenle kullanılabilecek bir slug'a çevirir
    (Türkçe karakterler + boşluk/özel karakterler normalize edilir): 'Ev & Yaşam' -> 'ev_yasam'."""
    text = name.translate(_TR_CHARS).lower()
    text = re.sub(r"[^a-z0-9]+", "_", text)
    return text.strip("_")


def write_category_page(
    site: str, category_id: str, category_name: str, page: int, http_status: int, content: bytes
) -> str:
    """Bir kategori sayfasının ham yanıtını MinIO'ya arşivler.

    Şema: category/{site}/{YYYY-MM-DD}/{category_id}_{isim_slug}/{sha256}.json (+ .meta.json).
    Kategori ID'si tek başına anlamsız olduğu için (siteye özel sayısal kod) klasör adına
    okunabilir isim de eklenir — kanonik id->isim eşlemesi configs/tr/{site}.yaml'daki
    main_categories'te yaşar, burada sadece MinIO'nun kendisi de okunabilir olsun diye tekrarlanır.
    Ham veri her zaman önce arşivlenir, işleme (field_mapping/Postgres) sonraki adımdır.
    """
    content_hash = sha256_bytes(content)
    category_folder = f"{category_id}_{slugify(category_name)}"
    object_name = f"category/{site}/{date.today().isoformat()}/{category_folder}/{content_hash}.json"
    meta = {
        "fetch_timestamp": datetime.now(timezone.utc).isoformat(),
        "site": site,
        "category_id": category_id,
        "category_name": category_name,
        "page": page,
        "plugin_version": PLUGIN_VERSION,
        "http_status": http_status,
        "content_hash": content_hash,
    }
    storage.put_json_with_meta(BUCKET, object_name, content, meta)
    return object_name
