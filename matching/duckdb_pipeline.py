from __future__ import annotations

import argparse
import html as html_module
import json
import re
import tempfile
from collections import defaultdict
from pathlib import Path
from xml.etree import ElementTree

import duckdb
import yaml

from shared.constants import DATALAKE_BUCKET
from shared.jsonpath import resolve_path
from shared.logging_config import get_logger
from shared.storage import storage

# matching/ core/ birbirini import etmez (bkz. CLAUDE.md) - bu modül configs/tr/{site}.yaml'ı
# (veri, kod değil) okur ve MinIO'daki ham arşivi kendi başına parse eder. Eveshop'un HTML
# extraction regex'leri core/parsers/site_plugins/eveshop.py'de DE var (fetch-time kullanım
# için) - kod paylaşılmıyor ama regex pattern'in kendisi configs/tr/eveshop.yaml'da tek
# doğruluk kaynağı, iki modül de oradan okur.
#
# SITE ADI HARDCODE EDİLMEZ (CLAUDE.md ilke #3): hangi sitelerin işleneceği configs/tr/
# dizininden, ham verinin JSON mu HTML mi olduğu ise config'teki `archive.extension` ve
# `category_search_api.response.format` alanlarından türetilir. Eskiden burada
# JSON_SITES = ["gratis","watsons","rossmann"] listesi ve ayrıca "eveshop" literali vardı;
# 5. bir site eklemek bu dosyayı düzenlemeyi gerektiriyordu.

logger = get_logger(__name__)

DB_PATH = Path(__file__).resolve().parent / "pricebot.duckdb"
CONFIG_DIR = Path(__file__).resolve().parent.parent / "configs" / "tr"
BUCKET = DATALAKE_BUCKET

# Bazı siteler (örn. Rossmann/Magento) 1-e-çok ilişkileri (bir ürünün üye olduğu HER kategori
# için ayrı bir alan, örn. "_source.cat_pos_1837") tek satırda yüzlerce sütuna yayarak
# kodluyor - bu geniş/wide tabloda saçma sayıda sütuna yol açar (doğrulandı: Rossmann'da 1187).
# _REPEATING_GROUP_MIN_MEMBERS eşiğinin üzerinde (aynı önek + sayısal sonek) key ailesi
# görülürse otomatik olarak ayrı bir ilişki/child tabloya taşınır (bkz. _split_repeating_groups)
# - site adı hardcode edilmeden, veri şekline bakarak. Küçük aileler (örn. name1/name2, 2 üye)
# eşiğin altında kalır, normal sütun olarak durur - kasıtlı ayrı alanlar oldukları için.
_REPEATING_GROUP_MIN_MEMBERS = 10
_SUFFIX_RE = re.compile(r"^(.*?)(\d+)$")

# İşlenmiş MinIO objelerinin kaydı. Bu tablo olmadan her çalıştırma TÜM arşiv tarihçesini
# baştan indirip parse ediyordu (günlük crawl'da doğrusal büyüyen, tamamen gereksiz bir iş).
INGEST_LOG_TABLE = "_ingested_objects"


def load_site_config(site: str) -> dict:
    return yaml.safe_load((CONFIG_DIR / f"{site}.yaml").read_text(encoding="utf-8"))


def discover_sites() -> list[str]:
    """configs/tr/*.yaml içinde category_search_api tanımlı siteleri bulur - site listesi
    kodda hardcode edilmez, config dizininden türetilir."""
    sites = []
    for path in sorted(CONFIG_DIR.glob("*.yaml")):
        config = yaml.safe_load(path.read_text(encoding="utf-8"))
        if config.get("category_search_api"):
            sites.append(path.stem)
    return sites


def archive_extension(config: dict) -> str:
    """Ham arşivin dosya uzantısı - core/storage.py ile AYNI varsayılanı kullanır
    (JSON API'ler için 'json', HTML kazıyan siteler config'te 'html' der)."""
    return config.get("archive", {}).get("extension", "json")


def flatten(obj: object, prefix: str = "", out: dict | None = None) -> dict:
    """Nested dict'leri nokta ile ayrılmış düz sütun adlarına açar (örn. 'attributes.eanUpc') -
    ham JSON'daki path'le birebir aynı isim çıkar, normalize.py yazılırken DuckDB'deki
    raw_{site} tablosundan doğrudan okunabilir. Listeler AÇILMAZ (olduğu gibi
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
    return sorted(
        name
        for name in storage.list_object_names(BUCKET, prefix)
        if name.endswith(f".{extension}") and not name.endswith(".meta.json")
    )


def _read_object(object_name: str) -> bytes:
    """MinIO'dan bir objeyi okur. Bağlantı havuzuna geri verme işini StorageClient yapar -
    eskiden burada get_object(...).read() çağrılıyor ve urllib3 yanıtı hiç kapatılmıyordu."""
    return storage.get_bytes(BUCKET, object_name)


def _provenance(object_name: str) -> dict:
    """category/{site}/{date}/{category_folder}/{hash}.ext yolundan izlenebilirlik alanları -
    ham tabloda hangi kategori/tarih/dosyadan geldiği kaybolmasın diye her satıra eklenir."""
    parts = object_name.split("/")
    return {
        "_source_object": object_name,
        "_fetch_date": parts[2] if len(parts) > 2 else None,
        "_category_folder": parts[3] if len(parts) > 3 else None,
    }


# --- Ham yanıt -> ürün listesi ------------------------------------------------------------


def _parse_structured(content: bytes, response_cfg: dict) -> object | None:
    """JSON, ya da config `json_or_xml` diyorsa XML olabilen bir yanıtı ayrıştırır.

    XML desteği ÖNEMLİ: watsons.yaml `format: json_or_xml` diyor (sunucu bazen aynı veriyi
    XML olarak dönüyor, doğrulanmış bir content-negotiation tutarsızlığı). Fetch katmanı bunu
    zaten arşivliyordu ama bu landing adımı sadece json.loads deneyip XML gelen dosyaları
    sessizce atlıyordu - yani arşivde duran veri DuckDB'ye hiç ulaşmıyordu."""
    fmt = response_cfg.get("format", "json")
    stripped = content.lstrip()
    if fmt == "xml" or (fmt == "json_or_xml" and stripped[:1] == b"<"):
        try:
            return _xml_to_obj(ElementTree.fromstring(content))
        except ElementTree.ParseError:
            return None
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        return None


def _xml_to_obj(element: ElementTree.Element) -> object:
    """Basit bir XML elemanını dict/list yapısına çevirir (attribute yok sayılır).
    core/parsers/base_parser.py'deki eşdeğeriyle aynı kurallar - iki modül birbirini import
    edemediği için (bkz. CLAUDE.md) mantık kopyalanmak zorunda, davranış bilinçli olarak
    aynı tutulmuştur."""
    children = list(element)
    if not children:
        return element.text.strip() if element.text else None

    result: dict[str, object] = {}
    for child in children:
        tag = child.tag.rsplit("}", 1)[-1]
        value = _xml_to_obj(child)
        if tag in result:
            if not isinstance(result[tag], list):
                result[tag] = [result[tag]]
            result[tag].append(value)
        else:
            result[tag] = value
    return result


def _json_string_unescape(value: str) -> str:
    """analytics_regex'in yakaladığı değerler JSON-string içinde JSON-string olarak escape
    edilmiş (\\/  -> /, \\u003e -> >, vb.) - value'yu çift tırnak arasına koyup json.loads
    etmek standart JSON kaçış kurallarının hepsini (unicode dahil) doğru çözer."""
    try:
        return json.loads('"' + value + '"')
    except json.JSONDecodeError:
        return value


def parse_html_embedded_json(html: str, extraction_cfg: dict, extract_regex: str) -> list[dict]:
    """HTML'e gömülü JSON bloklarını çıkarıp ortak bir join anahtarı üzerinden birleştirir.

    Site adı bilmez; hangi regex'in ne ürettiği tamamen configs/tr/{site}.yaml ->
    raw_html_extraction bloğundan okunur. Eveshop için yazıldı ama aynı desen (ürün kartı
    verisi bir attribute'ta, barkod ayrı bir <script> bloğunda, marka/görsel üçüncü bir
    analytics payload'ında) Shopify temalarının geneli için geçerli.

    Beklenen config anahtarları:
      variant_block_regex   barkod/sku taşıyan JSON dizisi
      variant_id_field      o dizideki kayıtların kimlik alanı (varsayılan 'id')
      variant_join_key      ana kayıtta bu kimliğe karşılık gelen alan
      variant_prefix        birleştirilen alanların önekі (varsayılan 'variant.')
      product_url_regex     (path, product_id) çiftlerini veren regex
      product_url_join_key  ana kayıtta ürün kimliği alanı
      analytics_regex       opsiyonel 3. kaynak
      analytics_groups      analytics_regex'in capture group'larının SIRALI adları;
                            '_' ile başlayanlar çıktıya yazılmaz (sadece join için)
      analytics_join_group  analytics_groups içinde join anahtarı olan grubun adı
      analytics_prefixes    {alan: önek} - protokolsüz URL'lere 'https:' eklemek gibi
    """
    base_records: list[dict] = []
    for match in re.finditer(extract_regex, html, re.DOTALL):
        try:
            parsed = json.loads(html_module.unescape(match.group(1)))
        except json.JSONDecodeError:
            continue
        base_records.extend(parsed if isinstance(parsed, list) else [parsed])

    variant_id_field = extraction_cfg.get("variant_id_field", "id")
    variant_join_key = extraction_cfg.get("variant_join_key", "variant_id")
    variant_prefix = extraction_cfg.get("variant_prefix", "variant.")

    variants_by_id: dict[object, dict] = {}
    if extraction_cfg.get("variant_block_regex"):
        for match in re.finditer(extraction_cfg["variant_block_regex"], html, re.DOTALL):
            try:
                parsed = json.loads(match.group(1))
            except json.JSONDecodeError:
                continue
            for variant in parsed if isinstance(parsed, list) else [parsed]:
                if isinstance(variant, dict) and variant_id_field in variant:
                    variants_by_id[variant[variant_id_field]] = variant

    url_join_key = extraction_cfg.get("product_url_join_key", "product_id")
    url_by_product_id: dict[int, str] = {}
    if extraction_cfg.get("product_url_regex"):
        for path, pid in re.findall(extraction_cfg["product_url_regex"], html, re.DOTALL):
            try:
                url_by_product_id[int(pid)] = path
            except ValueError:
                continue

    analytics_by_key: dict[object, dict] = {}
    analytics_regex = extraction_cfg.get("analytics_regex")
    group_names: list[str] = extraction_cfg.get("analytics_groups", [])
    join_group = extraction_cfg.get("analytics_join_group")
    prefixes: dict = extraction_cfg.get("analytics_prefixes", {})
    if analytics_regex and group_names and join_group:
        for match in re.finditer(analytics_regex, html, re.DOTALL):
            groups = match.groups()
            if len(groups) != len(group_names):
                continue
            values = {name: _json_string_unescape(v) for name, v in zip(group_names, groups)}
            raw_key = values.get(join_group)
            try:
                key = int(raw_key)
            except (TypeError, ValueError):
                continue
            payload = {
                name: prefixes.get(name, "") + value
                for name, value in values.items()
                if not name.startswith("_")
            }
            analytics_by_key[key] = payload

    merged = []
    for record in base_records:
        if not isinstance(record, dict):
            continue
        row = dict(record)
        variant = variants_by_id.get(record.get(variant_join_key))
        if variant:
            for key, value in variant.items():
                row[f"{variant_prefix}{key}"] = value
        url = url_by_product_id.get(record.get(url_join_key))
        if url:
            row["product_url"] = url
        analytics = analytics_by_key.get(record.get(variant_join_key))
        if analytics:
            for key, value in analytics.items():
                # product_url'i product_url_regex zaten doldurduysa üzerine yazma - o kaynak
                # (xVariantSelect çağrısı) doğrudan ürün sayfası yolu, analytics'inki türetilmiş
                if key == "product_url":
                    row.setdefault(key, value)
                else:
                    row[key] = value
        merged.append(row)
    return merged


def collect_rows(site: str, object_names: list[str]) -> list[dict]:
    """Bir sitenin verilen arşiv objelerinden düzleştirilmiş ürün satırlarını toplar.

    JSON mu HTML mi olduğu config'ten (`response.format`) belirlenir - site adına bakılmaz."""
    config = load_site_config(site)
    response_cfg = config["category_search_api"]["response"]
    fmt = response_cfg.get("format", "json")

    rows: list[dict] = []
    unparsed = 0

    for object_name in object_names:
        raw = _read_object(object_name)
        prov = _provenance(object_name)

        if fmt == "html_regex_json":
            items = parse_html_embedded_json(
                raw.decode("utf-8", errors="replace"),
                config.get("raw_html_extraction", {}),
                response_cfg["extract_regex"],
            )
        else:
            data = _parse_structured(raw, response_cfg)
            if data is None:
                unparsed += 1
                continue
            items_path = response_cfg.get("items_path")
            items = resolve_path(data, items_path) if items_path else data
            if not isinstance(items, list):
                continue

        for item in items:
            if isinstance(item, dict):
                rows.append({**flatten(item), **prov})

    if unparsed:
        logger.warning(f"{site}: {unparsed} arşiv dosyası ayrıştırılamadı (JSON da XML de değil), atlandı")

    return rows


# --- Tekrarlı grup ayrımı ------------------------------------------------------------------


def _slugify_prefix(prefix: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9]+", "_", prefix).strip("_").lower()
    return slug or "group"


def _protected_paths(site: str) -> set[str]:
    """field_mapping'in başvurduğu sütun adları - bunlar tekrarlı grup ayrımından MUAF.

    Eşik tabanlı ayrım (>=10 üyeli 'önek+sayı' ailesi) veri şekline bakarak çalışır ve
    config'i tanımaz; bir gün gerçekten kullanılan bir alan ailesi eşiği geçerse sütun ana
    tablodan child tabloya taşınır ve normalize.py 'column not found' ile kırılırdı.
    Config'te adı geçen path'ler burada korunur."""
    config = load_site_config(site)
    protected = {config.get("product_id_field")}
    for spec in (config.get("field_mapping") or {}).values():
        if isinstance(spec, dict) and spec.get("strategy") == "direct_path":
            protected.add(spec.get("path"))
    return {p for p in protected if p}


def _split_repeating_groups(rows: list[dict], protected: set[str]) -> tuple[list[dict], dict[str, list[dict]]]:
    """Aynı önek + sayısal sonekle biten (>= _REPEATING_GROUP_MIN_MEMBERS) key ailelerini
    ana satırlardan çıkarıp ayrı child kayıt listelerine taşır. Döner: (temizlenmiş ana
    satırlar, {aile_slug: [{_row_id, suffix, value}, ...]})."""
    all_keys: set[str] = set()
    for row in rows:
        all_keys.update(row.keys())

    families: dict[str, list[str]] = defaultdict(list)
    for key in all_keys:
        if key in protected:
            continue
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


def _prepare_main_rows(rows: list[dict], protected: set[str]) -> tuple[list[dict], dict[str, list[dict]]]:
    """_split_repeating_groups'u çalıştırır + hiç aile bulunamasa bile her satıra _row_id
    ekler. _row_id, satırın ham tablodaki kimliğidir: hem tekrarlı-grup child tablolarını
    ana tabloya bağlar hem de silver dedupe'ında (aynı ürün+tarih için hangi satır kalacak)
    deterministik sıralama anahtarı olarak kullanılır."""
    main_rows, child_groups = _split_repeating_groups(rows, protected)
    if not child_groups:
        main_rows = [{**row, "_row_id": i} for i, row in enumerate(rows)]
    return main_rows, child_groups


# --- DuckDB yazımı -------------------------------------------------------------------------


def _load_jsonl(con: duckdb.DuckDBPyConnection, table: str, rows: list[dict]) -> None:
    """Satırları geçici bir JSONL dosyası üzerinden DuckDB tablosuna yazar (tam yenileme)."""
    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False, encoding="utf-8") as f:
            for row in rows:
                f.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")
            tmp_path = f.name

        # sample_size=-1: şemayı ÖRNEKLEMEDEN, TÜM satırları tarayarak çıkar - ürüne özel/nadir
        # key'ler de sütun olarak kaçırılmasın (kullanıcı isteği), eksik olduğu satırlarda NULL.
        con.execute(f"DROP TABLE IF EXISTS {table}")
        con.execute(f"CREATE TABLE {table} AS SELECT * FROM read_json_auto(?, sample_size=-1)", [tmp_path])
    finally:
        # Eskiden unlink sadece başarı yolundaydı: bir hata durumunda geçici dosya sızıyordu.
        if tmp_path:
            Path(tmp_path).unlink(missing_ok=True)


def _append_jsonl(con: duckdb.DuckDBPyConnection, table: str, rows: list[dict]) -> bool:
    """Yeni satırları MEVCUT tabloya ekler; şema genişlemesini (yeni sütunlar) yönetir.

    Döner: True (eklendi) / False (uyumsuzluk - çağıran tam yeniden kurulum yapmalı).
    Tip çatışması (aynı sütunun eski tabloda BIGINT, yeni veride VARCHAR çıkması) inkremental
    yolla çözülemez; böyle bir durumda sessizce yanlış veri yazmak yerine tam yeniden kurulum
    tercih edilir."""
    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False, encoding="utf-8") as f:
            for row in rows:
                f.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")
            tmp_path = f.name

        # CREATE VIEW prepared parametre kabul etmiyor -> yol inline edilir. tmp_path
        # tempfile'dan geliyor (kullanıcı girdisi değil) ama yine de tırnak kaçışı yapılır.
        escaped_path = tmp_path.replace("'", "''")
        con.execute(
            "CREATE OR REPLACE TEMP VIEW _incoming AS "
            f"SELECT * FROM read_json_auto('{escaped_path}', sample_size=-1)"
        )
        existing = {r[0]: r[1] for r in con.execute(f"DESCRIBE {table}").fetchall()}
        incoming = {r[0]: r[1] for r in con.execute("DESCRIBE _incoming").fetchall()}

        for column, dtype in incoming.items():
            if column not in existing:
                con.execute(f'ALTER TABLE {table} ADD COLUMN "{column}" {dtype}')
                logger.info(f"{table}: yeni sütun eklendi -> {column} ({dtype})")

        try:
            con.execute(f"INSERT INTO {table} BY NAME SELECT * FROM _incoming")
        except duckdb.Error as exc:
            logger.warning(f"{table}: inkremental ekleme başarısız ({exc}) - tam yeniden kurulum yapılacak")
            return False
        return True
    finally:
        if tmp_path:
            Path(tmp_path).unlink(missing_ok=True)


def _table_exists(con: duckdb.DuckDBPyConnection, table: str) -> bool:
    return con.execute(
        "SELECT count(*) FROM information_schema.tables WHERE table_name = ?", [table]
    ).fetchone()[0] > 0


def _ensure_ingest_log(con: duckdb.DuckDBPyConnection) -> None:
    con.execute(f"""
        CREATE TABLE IF NOT EXISTS {INGEST_LOG_TABLE} (
            object_name VARCHAR PRIMARY KEY,
            site VARCHAR NOT NULL,
            ingested_at TIMESTAMP NOT NULL DEFAULT now()
        )
    """)


def _already_ingested(con: duckdb.DuckDBPyConnection, site: str) -> set[str]:
    return {
        row[0]
        for row in con.execute(
            f"SELECT object_name FROM {INGEST_LOG_TABLE} WHERE site = ?", [site]
        ).fetchall()
    }


def _record_ingested(con: duckdb.DuckDBPyConnection, site: str, object_names: list[str]) -> None:
    con.executemany(
        f"INSERT OR IGNORE INTO {INGEST_LOG_TABLE} (object_name, site) VALUES (?, ?)",
        [(name, site) for name in object_names],
    )


# NOT: clean_{site} tabloları KALDIRILDI (2026-08-02). Site başına leaf-only/dedupe edilmiş
# bir ara tablo, silver katmanı yokken her siteyi tek tek incelemek için geçici olarak
# üretilmişti. silver_products artık 4 siteyi tek şemada, temizlenmiş ve tarih geçmişli
# biçimde verdiği için bu ara katmanın tüketicisi kalmadı (kod denetimi: hiçbir modül
# clean_ tablolarından OKUMUYORDU - normalize.py raw_{site}, ean_match.py silver_products
# okuyor). Dedupe mantığı kaybolmadı: aynı (ürün, tarih) kuralı normalize.py'deki
# build_site_select'te uygulanıyor.


def load_site(con: duckdb.DuckDBPyConnection, site: str, full_refresh: bool = False) -> int:
    """Bir sitenin arşivini DuckDB'ye indirir (varsayılan: inkremental).

    İnkremental modda sadece daha önce işlenmemiş MinIO objeleri okunur. Eskiden her
    çalıştırma tüm tarihçeyi baştan indiriyordu - günlük crawl'da bu, arşiv büyüdükçe
    doğrusal olarak artan ve tamamen gereksiz bir yük demekti.
    """
    config = load_site_config(site)
    extension = archive_extension(config)
    all_objects = _list_archive_objects(site, extension)
    if not all_objects:
        logger.warning(f"{site}: arşivde hiç obje yok")
        return 0

    raw_table = f"raw_{site}"
    incremental = not full_refresh and _table_exists(con, raw_table)
    seen = _already_ingested(con, site) if incremental else set()
    pending = [name for name in all_objects if name not in seen]

    if incremental and not pending:
        logger.info(f"{site}: yeni arşiv objesi yok ({len(all_objects)} obje zaten işlenmiş)")
        return con.execute(f"SELECT COUNT(*) FROM {raw_table}").fetchone()[0]

    logger.info(
        f"{site}: {len(pending)}/{len(all_objects)} obje okunacak "
        f"({'inkremental' if incremental else 'tam yenileme'})"
    )
    rows = collect_rows(site, pending)
    if not rows:
        logger.warning(f"{site}: okunan objelerden hiç satır çıkmadı")
        return 0

    protected = _protected_paths(site)
    main_rows, child_groups = _prepare_main_rows(rows, protected)

    if incremental:
        # _row_id ham tabloda benzersiz olmalı (silver dedupe'ında sıralama anahtarı)
        offset = con.execute(f"SELECT COALESCE(MAX(_row_id), -1) + 1 FROM {raw_table}").fetchone()[0]
        main_rows = [{**row, "_row_id": row["_row_id"] + offset} for row in main_rows]
        child_groups = {
            slug: [{**c, "_row_id": c["_row_id"] + offset} for c in children]
            for slug, children in child_groups.items()
        }
        if not _append_jsonl(con, raw_table, main_rows):
            return load_site(con, site, full_refresh=True)
        for slug, children in child_groups.items():
            child_table = f"raw_{site}_{slug}"
            if _table_exists(con, child_table):
                _append_jsonl(con, child_table, children)
            else:
                _load_jsonl(con, child_table, children)
    else:
        con.execute(f"DELETE FROM {INGEST_LOG_TABLE} WHERE site = ?", [site])
        _load_jsonl(con, raw_table, main_rows)
        for slug, children in child_groups.items():
            _load_jsonl(con, f"raw_{site}_{slug}", children)

    _record_ingested(con, site, pending)

    count = con.execute(f"SELECT COUNT(*) FROM {raw_table}").fetchone()[0]
    col_count = len(con.execute(f"DESCRIBE {raw_table}").fetchall())
    logger.info(f"{site}: {count} satır, {col_count} sütun -> {raw_table}")
    return count


def load_all(sites: list[str] | None = None, full_refresh: bool = False) -> None:
    con = duckdb.connect(str(DB_PATH))
    try:
        _ensure_ingest_log(con)
        for site in sites or discover_sites():
            try:
                load_site(con, site, full_refresh=full_refresh)
            except Exception as exc:
                # Bir sitenin arşivi bozuksa diğerleri işlenmeye devam etsin (CLAUDE.md ilke #2)
                logger.error(f"{site}: landing başarısız - {exc}")
    finally:
        con.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="MinIO ham arşiv -> DuckDB raw_{site} tabloları")
    parser.add_argument("--sites", nargs="*", help="sadece bu siteler (varsayılan: config'teki hepsi)")
    parser.add_argument(
        "--full-refresh", action="store_true",
        help="inkremental yükleme yerine tüm arşivi baştan oku (şema değişikliği sonrası)",
    )
    args = parser.parse_args()
    load_all(args.sites, full_refresh=args.full_refresh)


if __name__ == "__main__":
    main()
