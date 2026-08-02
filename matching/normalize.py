from __future__ import annotations

import argparse
from pathlib import Path

import duckdb
import yaml
from duckdb.sqltypes import DOUBLE, VARCHAR

from shared.logging_config import get_logger
from shared.text import clean_text, normalize_key, parse_size

# Bu modül site adı BİLMEZ: configs/tr/{site}.yaml -> field_mapping'i okuyup SQL'i otomatik
# üretir. Yeni bir site eklemek = configs/tr/{site}.yaml'a field_mapping yazmak - buradaki kod
# değişmez (bkz. CLAUDE.md "plugin mimarisi" ilkesi). field_mapping'teki "strategy" alanı
# yorumlanır (direct_path/computed/static/none), hiçbir strategy site'a özel davranmaz.
#
# Kaynak tablo raw_{site} (GEÇİCİ - bkz. not): tüm sütunları (liste/struct dahil) koruyan
# tek katman odur ve bazı field_mapping formülleri buna ihtiyaç duyar (örn. Watsons'ın
# otherPrices struct-list erişimi, Gratis'in attributes.categories liste alanı).
#
# NOT (2026-08-03): hedef mimari raw_{site} -> clean_{site} -> silver_products'tır.
# clean_{site} şu an YOK (her site kendi içinde denetlenip kurulacak, bkz. matching/analysis/)
# - o iş bitene kadar bu modül GEÇİCİ olarak doğrudan raw_{site}'tan okur. clean_{site}
# hazır olduğunda build_site_select buradan değil clean_{site}'tan okuyacak şekilde
# güncellenecek; CANONICAL_FIELDS/normalizasyon mantığı (name/brand temizliği, fiyat
# NULL kuralı, name_full_normalized) aynı kalacak, sadece kaynak tablo değişecek.
# Eskiden burada "clean_{site}" adında bir VIEW oluşturuluyordu (2026-08-02 tarihli bir
# ara sürümde) - bu, gerçek/denetlenmiş clean katmanıyla AYNI İSMİ taşıyan ama hiçbir
# denetim içermeyen (sadece bu SELECT'i saran) bir view'dı, isim çakışması yaratıyordu.
# Kaldırıldı - clean_{site} adı SADECE gerçek denetlenmiş tablo için ayrılmıştır.

DB_PATH = Path(__file__).resolve().parent / "pricebot.duckdb"
CONFIG_DIR = Path(__file__).resolve().parent.parent / "configs" / "tr"
TABLE = "silver_products"

logger = get_logger(__name__)

# Silver katmanının kanonik şeması - hangi alanların var olacağının TEK listesi. Bir site
# field_mapping'inde bu alanlardan birini tanımlamazsa NULL olarak üretilir (hata değil).
#
# SIRA ÖNEMLİ: field_mapping formülleri kendinden ÖNCE gelen kanonik alanlara ad ile
# başvurabiliyor (DuckDB lateral alias; örn. Gratis'in is_in_stock'u stock_status_raw'a,
# Rossmann'ın discount_rate'i conditional_promo_price_try'a bakıyor). Bu listeyi yeniden
# sıralamak o formülleri "column not found" ile kırar - bağımlılık sırası korunmalı.
CANONICAL_FIELDS = [
    "source_sku", "ean", "name", "brand", "url", "image_url", "currency",
    "list_price_try", "sale_price_try", "conditional_promo_price_try", "discount_rate",
    "stock_status_raw", "is_in_stock", "stock_qty", "category_path",
]

# Kanonik alanların üzerine eklenen türetilmiş normalizasyon sütunları.
DERIVED_FIELDS = [
    "name_raw", "name_normalized", "name_full_normalized",
    "brand_raw", "brand_normalized", "size_value", "size_unit",
]

# Fiyat alanları: 0 veya negatif değer "fiyat" değildir, "fiyat yok" demektir - silver'da
# NULL'a çevrilir. Site-agnostik bir temizlik kuralı; hangi sitenin neden 0 yazdığını
# bilmeye gerek yok. Gerçek veride 9 satırda görüldü (hepsi Watsons ve HEPSİ stokta değil,
# yani satın alınabilir bir fiyat zaten yok). NULL yerine 0 bırakmak downstream'de sessiz
# hatalara yol açar: sıfıra bölme, "%100 indirim", "en ucuz site" hesaplarında yanlış kazanan.
PRICE_FIELDS = ["list_price_try", "sale_price_try", "conditional_promo_price_try"]


def load_site_config(site: str) -> dict:
    return yaml.safe_load((CONFIG_DIR / f"{site}.yaml").read_text(encoding="utf-8"))


def discover_sites() -> list[str]:
    """configs/tr/*.yaml içinde field_mapping bloğu olan siteleri bulur - site listesi
    kodda hardcode edilmez, config dizininden türetilir."""
    sites = []
    for path in sorted(CONFIG_DIR.glob("*.yaml")):
        config = yaml.safe_load(path.read_text(encoding="utf-8"))
        if config.get("field_mapping"):
            sites.append(path.stem)
    return sites


def register_udfs(con: duckdb.DuckDBPyConnection) -> None:
    """Python metin normalizasyonunu SQL'e açar.

    Bu kurallar (HTML entity çözme, BOM/görünmez karakter atma, Türkçe-duyarlı küçük harf,
    ml/gr birim standardizasyonu) saf SQL'de ya imkânsız ya da onlarca replace() zinciri
    demekti; shared/text.py'de tek yerde yaşıyorlar ve buradan çağrılıyorlar.
    null_handling="special" ZORUNLU: bu fonksiyonlar NULL DÖNDÜREBİLİR (boşa inen bir isim,
    birim içermeyen bir ürün adı). DuckDB'nin varsayılan DEFAULT modunda UDF'in NULL
    dönmesi hata sayılır; SPECIAL modda hem NULL girdi fonksiyona ulaşır hem NULL çıktı
    kabul edilir - shared/text.py'deki fonksiyonların hepsi None girdiyi zaten karşılıyor."""
    kwargs = {"null_handling": "special"}
    con.create_function("tr_clean_text", clean_text, [VARCHAR], VARCHAR, **kwargs)
    con.create_function("tr_normalize_key", normalize_key, [VARCHAR], VARCHAR, **kwargs)
    con.create_function("tr_size_value", lambda s: parse_size(s)[0], [VARCHAR], DOUBLE, **kwargs)
    con.create_function("tr_size_unit", lambda s: parse_size(s)[1], [VARCHAR], VARCHAR, **kwargs)


def _quote_col(path: str) -> str:
    """DuckDB'de flatten() sonucu sütun adları nokta içerebilir (örn. 'prices.normalPrice') -
    bu tek bir sütun adıdır, tablo.sütun DEĞİLDİR - çift tırnakla quote edilmeden kullanılırsa
    DuckDB bunu şema/struct erişimi sanıp yanlış yorumlar. Her zaman quote edilir."""
    return '"' + path.replace('"', '""') + '"'


def _field_expr(spec: dict) -> str:
    """Bir field_mapping girdisini (tek bir canonical alan için) SQL ifadesine çevirir.
    strategy dışında hiçbir şey site'a özel değildir - formula/path zaten config'te SQL
    olarak yazılmış durumda, burada sadece sarmalanır. unit:kurus dönüşümü strategy'den
    BAĞIMSIZ uygulanır (direct_path VE computed ikisinde de olabilir - örn. Eveshop'un
    conditional_promo_price_try'ı computed ama yine kuruş cinsinden)."""
    strategy = spec["strategy"]
    if strategy == "direct_path":
        expr = _quote_col(spec["path"])
    elif strategy == "computed":
        expr = f'({spec["formula"]})'
    elif strategy == "static":
        value = spec["value"].replace("'", "''")
        return f"'{value}'"
    elif strategy == "none":
        return "NULL"
    else:
        raise ValueError(f"bilinmeyen strategy: {strategy!r}")

    if spec.get("unit") == "kurus":
        expr = f"ROUND(({expr}) / 100.0, 2)"
    return expr


def build_site_select(site: str, config: dict) -> str:
    """Bir sitenin raw_ tablosundan kanonik alanları çıkaran SELECT'i üretir.

    Dedupe (ürün, TARİH) ikilisinde yapılır - sadece (ürün) değil. Bu, fiyat takibinin can
    damarı: eskiden PARTITION BY sadece ürün id'siydi ve ORDER BY _fetch_date DESC ile HER
    ÜRÜNÜN SADECE EN SON snapshot'ı kalıyordu, yani dünkü fiyat silver'a hiç ulaşmıyordu
    (ham arşivde durmasına rağmen). Tarih partition'a girince aynı gün içindeki sayfalama
    tekrarları yine temizlenir ama günler arası geçmiş korunur.

    Aynı gün içindeki eşitliklerde _row_id ile deterministik seçim yapılır (arşiv okuma
    sırasındaki ilk kayıt) - böylece aynı veriyle iki çalıştırma aynı sonucu verir.
    """
    field_mapping = config["field_mapping"]
    id_field_spec = field_mapping.get("id")
    if id_field_spec is None:
        raise ValueError(f"{site}: field_mapping'te 'id' yok, source_product_id üretilemez")
    id_expr = _field_expr(id_field_spec)
    dedupe_key = _quote_col(config["product_id_field"])

    field_exprs = []
    for canonical in CANONICAL_FIELDS:
        spec = field_mapping.get(canonical)
        expr = _field_expr(spec) if spec else "NULL"
        field_exprs.append(f"{expr} AS {canonical}")

    joined_fields = ",\n    ".join(field_exprs)

    return f"""
SELECT
    '{site}' AS site_code,
    CAST({id_expr} AS VARCHAR) AS source_product_id,
    _fetch_date AS fetch_date,
    {joined_fields}
FROM (
    SELECT * FROM raw_{site}
    QUALIFY ROW_NUMBER() OVER (PARTITION BY {dedupe_key}, _fetch_date ORDER BY _row_id) = 1
)
"""


def build_silver_select(union_sql: str) -> str:
    """Kanonik alanların üzerine normalizasyon katmanını ekler.

    Silver = TEMİZ katman: `name`/`brand` burada temizlenmiş halleriyle durur (HTML entity
    çözülmüş, BOM/görünmez karakter atılmış, boşluk tekilleştirilmiş), ham hali `*_raw`
    sütunlarında izlenebilirlik için saklanır. `*_normalized` sütunları ise EŞLEŞTİRME
    anahtarıdır (ASCII'ye indirilmiş, küçük harfli, noktalamasız) - okunmak için değil
    JOIN/similarity için var: 'FLORMAR' (Watsons/Eveshop) ile 'Flormar' (Gratis/Rossmann)
    ancak burada aynı değere iner.

    Alt sorgu `m` takma adıyla nitelendirilir: dış SELECT'te `name` adında YENİ bir sütun
    tanımlıyoruz ve aynı ifadede `m.name` (ham) okuyoruz - nitelenmemiş bırakılırsa DuckDB
    lateral alias çözümlemesi hangisini kastettiğimizi bilemez.
    """
    def passthrough(field: str) -> str:
        if field in PRICE_FIELDS:
            return f"CASE WHEN m.{field} > 0 THEN m.{field} END AS {field}"
        return f"m.{field}"

    canonical_passthrough = ",\n    ".join(
        passthrough(field) for field in CANONICAL_FIELDS if field not in ("name", "brand")
    )
    return f"""
SELECT
    md5(m.site_code || '_' || m.source_product_id || '_' || CAST(m.fetch_date AS VARCHAR)) AS silver_id,
    m.site_code,
    m.source_product_id,
    m.fetch_date,
    {canonical_passthrough},
    tr_clean_text(m.name) AS name,
    m.name AS name_raw,
    tr_normalize_key(m.name) AS name_normalized,
    tr_clean_text(m.brand) AS brand,
    m.brand AS brand_raw,
    tr_normalize_key(m.brand) AS brand_normalized,
    tr_size_value(m.name) AS size_value,
    tr_size_unit(m.name) AS size_unit,
    -- Marka adı ürün adında GEÇMİYORSA başa eklenir. Siteler ürün adını farklı kuruyor:
    -- Gratis/Rossmann/Watsons markayı isme yazıyor (%94-99), Eveshop yazmıyor (%9.9) -
    -- "Vücut Sütü 400 ml" vs "Nivea Vücut Sütü 400 ml". Bu fark ölçüldüğünde isim
    -- benzerliğini sistematik olarak bozuyordu: EAN ile eşleştiği KESİN olan çiftlerde
    -- jaro-winkler <0.60 olan 1511 çift vardı, marka eklendiğinde 70'e düştü (Eveshop
    -- içeren çiftlerde 1480 -> 40, ortalama benzerlik 0.71 -> 0.87). fuzzy_match.py bu
    -- kolonu kullanmalı, name_normalized'ı değil - aksi halde Eveshop haksız yere elenir.
    CASE
        WHEN tr_normalize_key(m.brand) IS NULL THEN tr_normalize_key(m.name)
        WHEN tr_normalize_key(m.name) IS NULL THEN tr_normalize_key(m.brand)
        WHEN contains(tr_normalize_key(m.name), tr_normalize_key(m.brand)) THEN tr_normalize_key(m.name)
        ELSE tr_normalize_key(m.brand) || ' ' || tr_normalize_key(m.name)
    END AS name_full_normalized
FROM ({union_sql}) m
WHERE m.source_product_id IS NOT NULL
"""


def _expected_columns() -> set[str]:
    base = {"silver_id", "site_code", "source_product_id", "fetch_date"}
    return base | set(CANONICAL_FIELDS) | set(DERIVED_FIELDS)


def _table_exists(con: duckdb.DuckDBPyConnection, table: str) -> bool:
    return con.execute(
        "SELECT count(*) FROM information_schema.tables WHERE table_name = ?", [table]
    ).fetchone()[0] > 0


def write_silver(con: duckdb.DuckDBPyConnection, select_sql: str) -> None:
    """silver_products'ı APPEND semantiğiyle günceller.

    Eskiden CREATE OR REPLACE idi: her çalıştırma tabloyu sıfırlıyordu. Artık sadece bu
    çalıştırmada üretilen (site, fetch_date) çiftleri silinip yeniden yazılır - aynı gün
    tekrar çalıştırmak idempotenttir, geçmiş günlere dokunulmaz.

    Şema değişirse (CANONICAL_FIELDS'a alan eklenir/çıkarılırsa) INSERT BY NAME kırılacağı
    için tablo bilinçli olarak baştan kurulur ve bu durum loglanır - geçmiş satırlar ham
    arşivden (raw_{site}) yeniden üretilebilir olduğu için veri kaybı değildir."""
    con.execute("CREATE OR REPLACE TEMP VIEW _silver_new AS " + select_sql)

    if _table_exists(con, TABLE):
        existing = {row[0] for row in con.execute(f"DESCRIBE {TABLE}").fetchall()}
        if existing != _expected_columns():
            logger.warning(
                f"{TABLE} şeması değişmiş (CANONICAL_FIELDS/DERIVED_FIELDS güncellendi) - "
                "tablo baştan kuruluyor, geçmiş satırlar raw_ tablolarından yeniden üretiliyor"
            )
            con.execute(f"DROP TABLE {TABLE}")

    if not _table_exists(con, TABLE):
        con.execute(f"CREATE TABLE {TABLE} AS SELECT * FROM _silver_new")
        return

    con.execute(f"""
        DELETE FROM {TABLE}
        WHERE (site_code, fetch_date) IN (SELECT DISTINCT site_code, fetch_date FROM _silver_new)
    """)
    con.execute(f"INSERT INTO {TABLE} BY NAME SELECT * FROM _silver_new")


def build(con: duckdb.DuckDBPyConnection, sites: list[str] | None = None) -> int:
    register_udfs(con)
    sites = sites or discover_sites()
    logger.info(f"field_mapping bulunan siteler: {sites}")

    selects = []
    for site in sites:
        config = load_site_config(site)
        sql = build_site_select(site, config)
        try:
            count = con.execute(f"SELECT COUNT(*) FROM ({sql})").fetchone()[0]
        except Exception as exc:
            logger.error(f"{site}: field_mapping SQL'i çalıştırılamadı - {exc}")
            raise
        logger.info(f"  {site}: {count} satır")
        selects.append(sql)

    union_sql = "\nUNION ALL\n".join(selects)
    write_silver(con, build_silver_select(union_sql))

    total = con.execute(f"SELECT COUNT(*) FROM {TABLE}").fetchone()[0]
    logger.info(f"{TABLE}: toplam {total} satır")
    return total


def report(con: duckdb.DuckDBPyConnection) -> None:
    """Kalite kontrol özeti. Tablo boş olabileceği için oran hesapları korumalı - eskiden
    total=0 durumunda ZeroDivisionError ile patlıyordu."""
    total = con.execute(f"SELECT COUNT(*) FROM {TABLE}").fetchone()[0]
    if total == 0:
        logger.warning(f"{TABLE} boş - kalite raporu atlandı")
        return

    logger.info("site x tarih satır sayısı:")
    for site, day, count in con.execute(
        f"SELECT site_code, fetch_date, COUNT(*) FROM {TABLE} GROUP BY 1, 2 ORDER BY 1, 2"
    ).fetchall():
        logger.info(f"  {site:10s} {day} {count:>7d}")

    dup = con.execute(f"""
        SELECT COUNT(*) FROM (
            SELECT site_code, source_product_id, fetch_date
            FROM {TABLE} GROUP BY 1, 2, 3 HAVING COUNT(*) > 1
        )
    """).fetchone()[0]
    logger.info(f"tekillik ihlali (site, ürün, tarih) = {dup} (0 olmalı)")

    logger.info("alan bazında NULL oranı:")
    for field in ["ean", "sale_price_try", "is_in_stock", "category_path", "brand", "size_value"]:
        nulls = con.execute(f"SELECT COUNT(*) FROM {TABLE} WHERE {field} IS NULL").fetchone()[0]
        logger.info(f"  {field:20s} NULL: {nulls}/{total} (%{nulls / total * 100:.1f})")


def main() -> None:
    parser = argparse.ArgumentParser(description="raw_{site} -> silver_products normalizasyonu")
    parser.add_argument("--sites", nargs="*", help="sadece bu siteler (varsayılan: field_mapping'i olan hepsi)")
    parser.add_argument("--no-report", action="store_true", help="kalite kontrol özetini atla")
    args = parser.parse_args()

    con = duckdb.connect(str(DB_PATH))
    try:
        build(con, args.sites)
        if not args.no_report:
            report(con)
    finally:
        con.close()


if __name__ == "__main__":
    main()
