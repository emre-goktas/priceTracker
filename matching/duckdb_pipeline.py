from __future__ import annotations

import html as html_module
import json
import re
import tempfile
from collections import defaultdict
from pathlib import Path

import duckdb
import yaml

from shared.jsonpath import resolve_path
from shared.logging_config import get_logger
from shared.storage import storage

# matching/ core/ birbirini import etmez (bkz. CLAUDE.md) - bu modül configs/tr/{site}.yaml'ı
# (veri, kod değil) okur ve MinIO'daki ham arşivi kendi başına parse eder. Eveshop'un HTML
# extraction regex'leri core/parsers/site_plugins/eveshop.py'de DE var (fetch-time kullanım
# için) - kod paylaşılmıyor ama regex pattern'in kendisi configs/tr/eveshop.yaml'da tek
# doğruluk kaynağı, iki modül de oradan okur.

logger = get_logger(__name__)

DB_PATH = Path(__file__).resolve().parent / "pricebot.duckdb"
CONFIG_DIR = Path(__file__).resolve().parent.parent / "configs" / "tr"
BUCKET = "datalake"
JSON_SITES = ["gratis", "watsons", "rossmann"]

# Bazı siteler (örn. Rossmann/Magento) 1-e-çok ilişkileri (bir ürünün üye olduğu HER kategori
# için ayrı bir alan, örn. "_source.cat_pos_1837") tek satırda yüzlerce sütuna yayarak
# kodluyor - bu geniş/wide tabloda saçma sayıda sütuna yol açar (doğrulandı: Rossmann'da 1187).
# _REPEATING_GROUP_MIN_MEMBERS eşiğinin üzerinde (aynı önek + sayısal sonek) key ailesi
# görülürse otomatik olarak ayrı bir ilişki/child tabloya taşınır (bkz. _split_repeating_groups)
# - site adı hardcode edilmeden, veri şekline bakarak. Küçük aileler (örn. name1/name2, 2 üye)
# eşiğin altında kalır, normal sütun olarak durur - kasıtlı ayrı alanlar oldukları için.
_REPEATING_GROUP_MIN_MEMBERS = 10
_SUFFIX_RE = re.compile(r"^(.*?)(\d+)$")


def load_site_config(site: str) -> dict:
    return yaml.safe_load((CONFIG_DIR / f"{site}.yaml").read_text(encoding="utf-8"))


def flatten(obj: object, prefix: str = "", out: dict | None = None) -> dict:
    """Nested dict'leri nokta ile ayrılmış düz sütun adlarına açar (örn. 'attributes.eanUpc') -
    ham JSON'daki path'le birebir aynı isim çıkar, normalize.py yazılırken DuckDB'deki
    raw_{site}/clean_{site} tablolarından doğrudan okunabilir. Listeler AÇILMAZ (olduğu gibi
    kalır) - DuckDB native LIST/STRUCT destekliyor, satır sayısını değiştirecek bir "explode"
    burada istenmiyor (ham veri = 1 ürün = 1 satır)."""
    if out is None:
        out = {}
    if isinstance(obj, dict):
        for key, value in obj.items():
            full_key = f"{prefix}.{key}" if prefix else key
            if isinstance(value, dict):
                flatten(value, full_key, out)
            else:
                out[full_key] = value
    return out


def _list_archive_objects(site: str, extension: str) -> list[str]:
    prefix = f"category/{site}/"
    return [
        o.object_name
        for o in storage._client.list_objects(BUCKET, prefix=prefix, recursive=True)
        if o.object_name.endswith(f".{extension}") and not o.object_name.endswith(".meta.json")
    ]


def _read_object(object_name: str) -> bytes:
    return storage._client.get_object(BUCKET, object_name).read()


def _provenance(object_name: str) -> dict:
    """category/{site}/{date}/{category_folder}/{hash}.ext yolundan izlenebilirlik alanları -
    ham tabloda hangi kategori/tarih/dosyadan geldiği kaybolmasın diye her satıra eklenir."""
    parts = object_name.split("/")
    return {
        "_source_object": object_name,
        "_fetch_date": parts[2] if len(parts) > 2 else None,
        "_category_folder": parts[3] if len(parts) > 3 else None,
    }


def collect_json_site_rows(site: str) -> list[dict]:
    """Gratis/Watsons/Rossmann gibi JSON API döndüren siteler için genel toplayıcı - site adı
    hardcode değil, configs/tr/{site}.yaml'daki items_path'e göre çalışır."""
    config = load_site_config(site)
    items_path = config["category_search_api"]["response"].get("items_path")

    rows: list[dict] = []
    skipped = 0
    for object_name in _list_archive_objects(site, "json"):
        try:
            data = json.loads(_read_object(object_name))
        except json.JSONDecodeError:
            # örn. Watsons bazen XML dönüyor (format: json_or_xml, bkz. watsons.yaml) - bu
            # landing adımı sadece JSON kapsıyor, XML fallback'i şimdilik bilerek atlanıyor.
            skipped += 1
            continue

        items = resolve_path(data, items_path) if items_path else data
        if not isinstance(items, list):
            continue

        prov = _provenance(object_name)
        for item in items:
            if isinstance(item, dict):
                rows.append({**flatten(item), **prov})

    if skipped:
        logger.warning(f"{site}: {skipped} dosya JSON olarak parse edilemedi (muhtemelen XML), atlandı")

    return rows


def _json_string_unescape(value: str) -> str:
    """analytics_regex'in yakaladığı değerler JSON-string içinde JSON-string olarak escape
    edilmiş (\\/  -> /, \\u003e -> >, vb.) - value'yu çift tırnak arasına koyup json.loads
    etmek standart JSON kaçış kurallarının hepsini (unicode dahil) doğru çözer."""
    try:
        return json.loads('"' + value + '"')
    except json.JSONDecodeError:
        return value


def parse_eveshop_html(
    html: str,
    extract_regex: str,
    variant_block_regex: str,
    product_url_regex: str,
    analytics_regex: str | None = None,
) -> list[dict]:
    """Eveshop kategori sayfası HTML'inden gömülü blokları çıkarıp variant_id/product_id
    üzerinden birleştirir (doğrulanmış join key'ler, bkz. configs/tr/eveshop.yaml notu)."""
    labels: list[dict] = []
    for match in re.finditer(extract_regex, html, re.DOTALL):
        try:
            parsed = json.loads(html_module.unescape(match.group(1)))
        except json.JSONDecodeError:
            continue
        labels.extend(parsed if isinstance(parsed, list) else [parsed])

    variants_by_id: dict[object, dict] = {}
    for match in re.finditer(variant_block_regex, html, re.DOTALL):
        try:
            parsed = json.loads(match.group(1))
        except json.JSONDecodeError:
            continue
        for variant in parsed if isinstance(parsed, list) else [parsed]:
            if isinstance(variant, dict) and "id" in variant:
                variants_by_id[variant["id"]] = variant

    url_by_product_id = {int(pid): path for path, pid in re.findall(product_url_regex, html, re.DOTALL)}

    # analytics_by_variant_id: Shopify web-pixels "collection_viewed" event'inden marka/tam
    # kategori hiyerarşisi/görsel URL'i (bkz. configs/tr/eveshop.yaml -> analytics_regex notu).
    # x-labels-data/x-variants-data'dan TAMAMEN AYRI, 3. bir gömülü JSON kaynağı - 2026-08-02'de
    # eklendi, önceden bu 3 alanın (brand/category/image) hiçbiri elde edilemiyordu.
    analytics_by_variant_id: dict[int, dict] = {}
    if analytics_regex:
        for match in re.finditer(analytics_regex, html, re.DOTALL):
            vendor, _product_id, product_url, category_type, variant_id, image_src, _sku = match.groups()
            try:
                vid = int(variant_id)
            except ValueError:
                continue
            analytics_by_variant_id[vid] = {
                "brand": _json_string_unescape(vendor),
                "category_hierarchy": _json_string_unescape(category_type),
                "image_url": "https:" + _json_string_unescape(image_src),
                "product_url": _json_string_unescape(product_url),
            }

    merged = []
    for label in labels:
        if not isinstance(label, dict):
            continue
        row = dict(label)
        variant = variants_by_id.get(label.get("variant_id"))
        if variant:
            for key, value in variant.items():
                row[f"variant.{key}"] = value
        url = url_by_product_id.get(label.get("product_id"))
        if url:
            row["product_url"] = url
        analytics = analytics_by_variant_id.get(label.get("variant_id"))
        if analytics:
            row["brand"] = analytics["brand"]
            row["category_hierarchy"] = analytics["category_hierarchy"]
            row["image_url"] = analytics["image_url"]
            if "product_url" not in row:
                row["product_url"] = analytics["product_url"]
        merged.append(row)
    return merged


def collect_eveshop_rows() -> list[dict]:
    config = load_site_config("eveshop")
    extract_regex = config["category_search_api"]["response"]["extract_regex"]
    variant_block_regex = config["raw_html_extraction"]["variant_block_regex"]
    product_url_regex = config["raw_html_extraction"]["product_url_regex"]
    analytics_regex = config["raw_html_extraction"].get("analytics_regex")

    rows: list[dict] = []
    for object_name in _list_archive_objects("eveshop", "html"):
        html = _read_object(object_name).decode("utf-8", errors="replace")
        prov = _provenance(object_name)
        for item in parse_eveshop_html(html, extract_regex, variant_block_regex, product_url_regex, analytics_regex):
            rows.append({**flatten(item), **prov})

    return rows


def _slugify_prefix(prefix: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9]+", "_", prefix).strip("_").lower()
    return slug or "group"


def _split_repeating_groups(rows: list[dict]) -> tuple[list[dict], dict[str, list[dict]]]:
    """Aynı önek + sayısal sonekle biten (>= _REPEATING_GROUP_MIN_MEMBERS) key ailelerini
    ana satırlardan çıkarıp ayrı child kayıt listelerine taşır. Döner: (temizlenmiş ana
    satırlar, {aile_slug: [{_row_id, suffix, value}, ...]})."""
    all_keys: set[str] = set()
    for row in rows:
        all_keys.update(row.keys())

    families: dict[str, list[str]] = defaultdict(list)
    for key in all_keys:
        match = _SUFFIX_RE.match(key)
        if match:
            families[match.group(1)].append(key)

    key_to_family = {
        key: prefix
        for prefix, members in families.items()
        if len(members) >= _REPEATING_GROUP_MIN_MEMBERS
        for key in members
    }
    if not key_to_family:
        return rows, {}

    children: dict[str, list[dict]] = defaultdict(list)
    cleaned_rows = []
    for row_id, row in enumerate(rows):
        row = {**row, "_row_id": row_id}
        cleaned = {}
        for key, value in row.items():
            family_prefix = key_to_family.get(key)
            if family_prefix is None:
                cleaned[key] = value
                continue
            if value is None:
                continue
            suffix = key[len(family_prefix):]
            slug = _slugify_prefix(family_prefix)
            children[slug].append({"_row_id": row_id, "suffix": suffix, "value": value})
        cleaned_rows.append(cleaned)

    for prefix, members in families.items():
        if len(members) >= _REPEATING_GROUP_MIN_MEMBERS:
            logger.info(
                f"tekrarlı grup tespit edildi: '{prefix}*' ({len(members)} üye) -> "
                f"raw_{{site}}_{_slugify_prefix(prefix)} alt tablosuna taşınıyor"
            )

    return cleaned_rows, children


def _load_jsonl(con: duckdb.DuckDBPyConnection, table: str, rows: list[dict]) -> None:
    with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False, encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")
        tmp_path = f.name

    # sample_size=-1: şemayı ÖRNEKLEMEDEN, TÜM satırları tarayarak çıkar - ürüne özel/nadir
    # key'ler de sütun olarak kaçırılmasın (kullanıcı isteği), eksik olduğu satırlarda NULL.
    con.execute(f"DROP TABLE IF EXISTS {table}")
    con.execute(f"CREATE TABLE {table} AS SELECT * FROM read_json_auto(?, sample_size=-1)", [tmp_path])
    Path(tmp_path).unlink(missing_ok=True)


def _prepare_main_rows(rows: list[dict]) -> tuple[list[dict], dict[str, list[dict]]]:
    """_split_repeating_groups'u çalıştırır + hiç aile bulunamasa bile her satıra _row_id
    ekler. raw_{site} VE clean_{site} AYNI (temizlenmiş) taban satırlardan beslenmeli - aksi
    halde clean tablo hâlâ binlerce sütunluk (örn. Rossmann'ın cat_pos_*) ham satırı görür ve
    DuckDB'nin şema çıkarımı bozulur (doğrulandı, 2026-07-30 - bkz. bug geçmişi)."""
    main_rows, child_groups = _split_repeating_groups(rows)
    if not child_groups:
        main_rows = [{**row, "_row_id": i} for i, row in enumerate(rows)]
    return main_rows, child_groups


def _write_to_duckdb(con: duckdb.DuckDBPyConnection, site: str, main_rows: list[dict], child_groups: dict[str, list[dict]]) -> int:
    """Ham (bronze) tablo: raw_{site}. Bu fonksiyon dokunulmadan kalır - kullanıcı ham
    tabloların olduğu gibi durmasını istedi (bkz. 2026-07-30 talebi)."""
    if not main_rows:
        logger.warning(f"{site}: hiç satır bulunamadı, tablo oluşturulmadı")
        return 0

    table = f"raw_{site}"
    _load_jsonl(con, table, main_rows)
    count = con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
    col_count = len(con.execute(f"DESCRIBE {table}").fetchall())
    logger.info(f"{site}: {count} satır, {col_count} sütun -> {table}")

    for slug, child_rows in child_groups.items():
        child_table = f"raw_{site}_{slug}"
        _load_jsonl(con, child_table, child_rows)
        child_count = con.execute(f"SELECT COUNT(*) FROM {child_table}").fetchone()[0]
        logger.info(f"{site}: {child_count} satır -> {child_table} (child, _row_id ile {table}'a bağlı)")

    return count


def _leaf_only(rows: list[dict]) -> list[dict]:
    """Herhangi bir satırda LİSTE değeri taşıyan key'leri TÜM satırlardan çıkarır - sadece
    'leaf' (skaler: string/sayı/bool/null) key'ler sütun olur. Kullanıcı isteği (2026-07-30):
    clean_{site} tabloları basit/analiz-hazır kalsın, iç içe/çok-değerli alanlarla uğraşmasın -
    o alanlar ham tabloda (raw_{site}) zaten duruyor, kaybolmuyor."""
    list_valued_keys: set[str] = set()
    for row in rows:
        for key, value in row.items():
            if isinstance(value, list):
                list_valued_keys.add(key)
    return [{k: v for k, v in row.items() if k not in list_valued_keys} for row in rows]


def _dedupe_by_id(rows: list[dict], id_field: str) -> list[dict]:
    """id_field bazında EN GÜNCEL (_fetch_date en yeni) satırı tutar, eskilerini atar.

    DÜZELTME (2026-08-01): Önceden "ilk görüleni tut" idi ve bu genelde zararsızdı çünkü
    tek bir crawl içindeki tekrarların (aynı gün, sayfalama kayması) içeriği zaten birebir
    aynıydı. Ama arşivde BİRDEN FAZLA TARİH birikince (aynı ürün 29 Temmuz VE 1 Ağustos'ta
    arşivlenmiş) `_list_archive_objects`'in MinIO'dan aldığı liste tarih klasörüne göre
    lexicographic (=kronolojik, eskiden yeniye) sıralı olduğu için "ilk görülen" hep EN ESKİ
    satır oluyordu - taze bir crawl çalıştırılıp clean_gratis'e hâlâ 3 gün önceki fiyatın
    yazıldığı canlı olarak yakalandı. Artık _fetch_date'e göre açıkça sıralanıp en yenisi
    seçiliyor - MinIO'nun listeleme sırasına güvenilmiyor."""
    sorted_rows = sorted(rows, key=lambda r: r.get("_fetch_date") or "", reverse=True)
    seen: set = set()
    result = []
    for row in sorted_rows:
        key = row.get(id_field)
        if key is None or key not in seen:
            if key is not None:
                seen.add(key)
            result.append(row)
    return result


def build_clean_table(con: duckdb.DuckDBPyConnection, site: str, main_rows: list[dict]) -> int:
    """clean_{site}: sadece leaf/skaler sütunlar + product_id_field bazında dedupe edilmiş -
    raw_{site}'a dokunmaz, ayrı/ek bir tablo (bkz. 2026-07-30 talebi). main_rows, raw_{site}
    için de kullanılan (tekrarlı-grup ayrımı yapılmış) taban satırlar olmalı - ham/bölünmemiş
    satırlar DEĞİL (bkz. _prepare_main_rows notu)."""
    config = load_site_config(site)
    id_field = config.get("product_id_field")
    if not id_field:
        logger.warning(f"{site}: configs/tr/{site}.yaml'da product_id_field yok, clean tablo atlanıyor")
        return 0

    leaf_rows = _leaf_only(main_rows)
    deduped = _dedupe_by_id(leaf_rows, id_field)

    table = f"clean_{site}"
    _load_jsonl(con, table, deduped)
    count = con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
    col_count = len(con.execute(f"DESCRIBE {table}").fetchall())
    logger.info(f"{site}: {len(main_rows)} ham satırdan {count} satır ({len(main_rows) - count} dedupe edildi), {col_count} sütun -> {table}")
    return count


def _load_site(con: duckdb.DuckDBPyConnection, site: str, rows: list[dict]) -> None:
    main_rows, child_groups = _prepare_main_rows(rows)
    _write_to_duckdb(con, site, main_rows, child_groups)
    build_clean_table(con, site, main_rows)


def load_all() -> None:
    con = duckdb.connect(str(DB_PATH))
    try:
        for site in JSON_SITES:
            _load_site(con, site, collect_json_site_rows(site))
        _load_site(con, "eveshop", collect_eveshop_rows())
    finally:
        con.close()


if __name__ == "__main__":
    load_all()
