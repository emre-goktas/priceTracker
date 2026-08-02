from __future__ import annotations

from datetime import date, datetime, timezone

from shared.constants import DATALAKE_BUCKET, PLUGIN_VERSION
from shared.hashing import sha256_bytes
from shared.storage import storage
from shared.text import slugify

# BUCKET/PLUGIN_VERSION ve Türkçe slugify tablosu daha önce burada ve crawler/ içinde
# ayrı ayrı kopyalanmıştı — tek doğruluk kaynağı olarak shared/ altına taşındı
# (shared/constants.py, shared/text.py). Bu isimler geriye dönük uyumluluk için
# modül seviyesinde yeniden dışa verilir.
BUCKET = DATALAKE_BUCKET

__all__ = ["BUCKET", "PLUGIN_VERSION", "slugify", "write_category_page"]


def write_category_page(
    site: str,
    category_id: str,
    category_name: str,
    page: int,
    http_status: int,
    content: bytes,
    extension: str = "json",
    content_type: str = "application/json",
) -> str:
    """Bir kategori sayfasının ham yanıtını MinIO'ya arşivler.

    Şema: category/{site}/{YYYY-MM-DD}/{category_id}_{isim_slug}/{sha256}.{extension}
    (+ .meta.json). Kategori ID'si tek başına anlamsız olduğu için (siteye özel sayısal kod)
    klasör adına okunabilir isim de eklenir — kanonik id->isim eşlemesi configs/tr/{site}.yaml'daki
    main_categories'te yaşar, burada sadece MinIO'nun kendisi de okunabilir olsun diye tekrarlanır.
    Ham veri her zaman önce arşivlenir, işleme (field_mapping/Postgres) sonraki adımdır.

    extension/content_type varsayılan olarak JSON (Gratis/Watsons/Rossmann'ın kategori API
    yanıtları) - HTML kazıyan siteler (Eveshop) ham HTML'i olduğu gibi arşivlemek için
    extension="html", content_type="text/html" geçer.
    """
    content_hash = sha256_bytes(content)
    category_folder = f"{category_id}_{slugify(category_name)}"
    object_name = f"category/{site}/{date.today().isoformat()}/{category_folder}/{content_hash}.{extension}"
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
    storage.put_json_with_meta(BUCKET, object_name, content, meta, content_type=content_type)
    return object_name
