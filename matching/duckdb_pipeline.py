from __future__ import annotations

import argparse
import html as html_module
import json
import re
import tempfile
from datetime import datetime, timedelta, timezone
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


def _list_archive_objects(site: str, extension: str) -> list[tuple[str, datetime]]:
    prefix = f"category/{site}/"
    entries = [
        (name, mtime)
        for name, mtime in storage.list_objects(BUCKET, prefix)
        if name.endswith(f".{extension}") and not name.endswith(".meta.json")
    ]
    return sorted(entries, key=lambda x: (x[1], x[0]))


def _cluster_entries(
    entries: list[tuple[str, datetime]], gap_minutes: int = 15
) -> list[list[tuple[str, datetime]]]:
    """Zaman damgalarına göre sıralı arşiv objelerini çalıştırma (run) pencerelerine kümeler.

    İki ardışık obje arasındaki süre gap_minutes'tan fazlaysa yeni bir çalıştırma kabul edilir.
    Böylece tam yenileme (full_refresh) durumunda bile geçmiş çalıştırmaların zaman damgaları
    korunur, tüm tarihçe tek bir çalıştırma damgasıyla ezilmez."""
    if not entries:
        return []
    clusters: list[list[tuple[str, datetime]]] = []
    current: list[tuple[str, datetime]] = [entries[0]]
    for item in entries[1:]:
        if (item[1] - current[-1][1]) > timedelta(minutes=gap_minutes):
            clusters.append(current)
            current = [item]
        else:
            current.append(item)
    if current:
        clusters.append(current)
    return clusters


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


def collect_rows(site: str, object_names: list[str], config: dict | None = None) -> list[dict]:
    """Bir sitenin verilen arşiv objelerinden düzleştirilmiş ürün satırlarını toplar.

    JSON mu HTML mi olduğu config'ten (`response.format`) belirlenir - site adına bakılmaz.

    config verilmezse configs/tr/{site}.yaml'daki AKTİF config okunur (normal akış). Verilirse
    (örn. matching/eveshop_html_supplement.py'nin dormant "_html_barkod_icin_gelecek" bloklarını
    birleştirdiği override) o kullanılır - siteye özel bir dal DEĞİL, herhangi bir site aynı
    şekilde alternatif bir arşiv kaynağını (farklı format/response şekli) bu parametreyle
    işleyebilir."""
    config = config or load_site_config(site)
    response_cfg = config["category_search_api"]["response"]
    fmt = response_cfg.get("format", "json")
    drop_prefixes = _drop_prefixes(config)

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
                rows.append({**_drop_ignored(flatten(item), drop_prefixes), **prov})

    if unparsed:
        logger.warning(f"{site}: {unparsed} arşiv dosyası ayrıştırılamadı (JSON da XML de değil), atlandı")

    return rows


def _drop_prefixes(config: dict) -> tuple[str, ...]:
    """configs/tr/{site}.yaml -> raw_columns.drop_prefixes: ham veriye HİÇ alınmayacak
    alan önekleri.

    Bazı API'ler ürün verisinin yanında kendi arama motorlarının iç ayarlarını da döndürüyor
    (örn. Gratis'in `attributes.boosts.*` alanları: kategori/kampanya-etiketi bazlı
    Elasticsearch sıralama ağırlıkları, 44 sütun). Bunlar ürün hakkında hiçbir şey söylemez,
    kampanya takvimiyle sürekli değişir ve her yeni etiket ham tabloya yeni bir sütun ekler -
    şemayı zamanla şişirirler. Burada, düzleştirmeden hemen sonra atılırlar; ne ana tabloya
    ne de tekrarlı-grup child tablosuna girerler.

    Site adı koda girmez: kod sadece bu config anahtarını yorumlar."""
    return tuple(config.get("raw_columns", {}).get("drop_prefixes", []))


def _drop_ignored(row: dict, prefixes: tuple[str, ...]) -> dict:
    if not prefixes:
        return row
    return {k: v for k, v in row.items() if not k.startswith(prefixes)}


# NOT (2026-08-03): Rossmann'ın cat_pos_* alanları (Magento'nun ürün-başına-100+ kategori
# sıralama pozisyonu) için burada eskiden genel/otomatik bir "tekrarlı grup tespiti" vardı
# (>=10 üyeli 'önek+sayı' key ailesini ayrı bir child tabloya taşıyan _split_repeating_groups).
# cat_pos_* artık configs/tr/rossmann.yaml -> raw_columns.drop_prefixes ile kaynakta
# filtrelendiği için (kullanıcı kararı: bu veri hiç işimize yaramıyor) bu mekanizmanın
# denetlediği 4 site raw tablosunun HİÇBİRİNDE artık >=10 üyeli tekrarlı aile kalmıyor
# (doğrulandı, 2026-08-03) - yani kod hiçbir zaman tetiklenmiyordu, çıkarıldı. Yeni bir site
# gerçekten böyle bir desen getirirse (Magento-tipi "poor man's array") o siteye özel
# raw_columns.drop_prefixes girdisi eklenmesi yeterli - genel bir otomatik mekanizma yerine
# görünür/kasıtlı bir config kararı tercih edildi.


def _add_row_id(rows: list[dict]) -> list[dict]:
    """Her satıra, ham tablodaki kimliği olan _row_id'yi ekler - silver dedupe'ında
    (aynı ürün+tarih için hangi satır kalacak) deterministik sıralama anahtarı olarak
    kullanılır."""
    return [{**row, "_row_id": i} for i, row in enumerate(rows)]


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


def load_site(
    con: duckdb.DuckDBPyConnection,
    site: str,
    full_refresh: bool = False,
    config_override: dict | None = None,
    raw_table_override: str | None = None,
    tracking_key_override: str | None = None,
    ingested_at: datetime | None = None,
) -> int:
    """Bir sitenin arşivini DuckDB'ye indirir (varsayılan: inkremental).

    İnkremental modda sadece daha önce işlenmemiş MinIO objeleri okunur. Eskiden her
    çalıştırma tüm tarihçeyi baştan indiriyordu - günlük crawl'da bu, arşiv büyüdükçe
    doğrusal olarak artan ve tamamen gereksiz bir yük demekti.

    3 opsiyonel override, hepsi None ise davranış TAMAMEN eskisi gibi (config diskten okunur,
    hedef tablo raw_{site}, ingest-log takibi site adıyla). Siteye özel bir dal DEĞİL - herhangi
    bir sitenin AYNI MinIO prefix'inden (category/{site}/) farklı bir arşiv formatını/config'ini
    AYRI bir tabloya indirmesi gerektiğinde kullanılır (örn. Eveshop'un products.json birincil +
    ara sıra HTML barkod-tazeleme kaynağı - bkz. matching/eveshop_html_supplement.py). Ayrı
    tracking_key şart: aynı 'site' anahtarını paylaşmak, bir kaynağın --full-refresh'i diğer
    kaynağın ingest-log kaydını da silerdi (INGEST_LOG_TABLE 'site' bazlı, tabloya göre değil).

    ingested_at: bu çalıştırmayı (run) damgalayan zaman - verilmezse `datetime.now()` kullanılır.
    load_all() TÜM siteler için TEK bir değer üretip geçirir (aynı pipeline çalıştırması aynı
    damgayı paylaşsın diye) - matching/analysis/build_clean.py ve normalize.py bunu (_fetch_date
    DEĞİL) dedupe/geçmiş partition anahtarı olarak kullanır, gün-içi (intraday) fiyat takibinin
    temeli budur (bkz. CLAUDE.md "Zamanlanmış Çalıştırma").
    """
    ingested_at = ingested_at or datetime.now(timezone.utc)
    config = config_override or load_site_config(site)
    extension = archive_extension(config)
    all_entries = _list_archive_objects(site, extension)
    if not all_entries:
        logger.warning(f"{site}: arşivde hiç obje yok")
        return 0

    all_objects = [name for name, _ in all_entries]
    raw_table = raw_table_override or f"raw_{site}"
    tracking_key = tracking_key_override or site
    incremental = not full_refresh and _table_exists(con, raw_table)
    seen = _already_ingested(con, tracking_key) if incremental else set()
    pending_entries = [e for e in all_entries if e[0] not in seen]
    pending = [name for name, _ in pending_entries]

    if incremental and not pending:
        logger.info(f"{site}: yeni arşiv objesi yok ({len(all_objects)} obje zaten işlenmiş)")
        return con.execute(f"SELECT COUNT(*) FROM {raw_table}").fetchone()[0]

    logger.info(
        f"{site}: {len(pending)}/{len(all_objects)} obje okunacak "
        f"({'inkremental' if incremental else 'tam yenileme'})"
    )
    clusters = _cluster_entries(pending_entries)
    rows: list[dict] = []
    for cluster in clusters:
        cluster_names = [name for name, _ in cluster]
        cluster_rows = collect_rows(site, cluster_names, config=config)
        if not cluster_rows:
            continue
        if len(clusters) == 1 and ingested_at:
            ts = ingested_at.isoformat()
        else:
            ts = cluster[-1][1].isoformat()
        rows.extend([{**row, "_ingested_at": ts} for row in cluster_rows])

    if not rows:
        logger.warning(f"{site}: okunan objelerden hiç satır çıkmadı")
        return 0

    main_rows = _add_row_id(rows)

    if incremental:
        # _row_id ham tabloda benzersiz olmalı (silver dedupe'ında sıralama anahtarı)
        offset = con.execute(f"SELECT COALESCE(MAX(_row_id), -1) + 1 FROM {raw_table}").fetchone()[0]
        main_rows = [{**row, "_row_id": row["_row_id"] + offset} for row in main_rows]
        if not _append_jsonl(con, raw_table, main_rows):
            return load_site(
                con, site, full_refresh=True, config_override=config_override,
                raw_table_override=raw_table_override, tracking_key_override=tracking_key_override,
                ingested_at=ingested_at,
            )
    else:
        con.execute(f"DELETE FROM {INGEST_LOG_TABLE} WHERE site = ?", [tracking_key])
        _load_jsonl(con, raw_table, main_rows)

    _record_ingested(con, tracking_key, pending)

    count = con.execute(f"SELECT COUNT(*) FROM {raw_table}").fetchone()[0]
    col_count = len(con.execute(f"DESCRIBE {raw_table}").fetchall())
    logger.info(f"{site}: {count} satır, {col_count} sütun -> {raw_table}")
    return count


def load_all(sites: list[str] | None = None, full_refresh: bool = False) -> None:
    con = duckdb.connect(str(DB_PATH))
    # Tek bir pipeline çalıştırmasındaki TÜM siteler aynı "run" damgasını paylaşsın diye
    # döngü öncesinde bir kez üretilir (bkz. load_site()'ın ingested_at notu).
    ingested_at = datetime.now(timezone.utc)
    try:
        _ensure_ingest_log(con)
        for site in sites or discover_sites():
            try:
                load_site(con, site, full_refresh=full_refresh, ingested_at=ingested_at)
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
