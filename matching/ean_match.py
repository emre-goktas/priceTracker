from __future__ import annotations

import argparse
from pathlib import Path

import duckdb
from duckdb.sqltypes import BOOLEAN, VARCHAR

from shared.logging_config import get_logger
from shared.text import normalize_gtin, normalize_gtin_lenient

# SILVER'IN SON KATMANI: normalize.py'nin ürettiği silver_products üzerine barkod
# normalizasyonu + siteler arası EAN eşleştirmesi ekler. Golden/canonical katman bunun
# çıktısından beslenir.
#
# Bu modül site adı BİLMEZ - tüm siteler aynı kurallardan geçer. Site-özel barkod kirliliği
# (Watsons'ın virgülle zincirlediği çoklu değerler, Gratis'in baştaki sıfırı kırpılmış
# kodları) configs/tr/{site}.yaml'da ZATEN VARCHAR[] listeye çevrilmiş halde geliyor;
# "hangi parça gerçek barkod" kararı bilinçli olarak buraya ertelenmişti (bkz. watsons.yaml
# ve gratis.yaml'daki ean notları) - o karar burada, tek yerde veriliyor.
#
# Üretilen tablolar:
#   silver_product_eans      ham barkod -> kanonik GTIN-14 (patlatılmış, ürün başına N satır)
#   silver_ean_matches       GTIN -> kaç sitede/kaç üründe bulundu + fiyat tutarlılık kontrolü
#   silver_products_matched  silver_products + match_id/ean_primary (GOLDEN'in girdisi)

DB_PATH = Path(__file__).resolve().parent / "pricebot.duckdb"
SOURCE_TABLE = "silver_products"

logger = get_logger(__name__)

# Aynı EAN'e sahip iki ürün arasındaki fiyat oranı bu eşiği aşarsa barkod hatası şüphesi
# doğar (CLAUDE.md: "5-10x fark -> EAN hatası şüphesi, otomatik onaylama, review_queue").
# Eşik alt sınırdan (5x) seçildi: kozmetikte aynı üründe siteler arası bu kadar fark
# gerçekçi değil, bu noktadan sonrası büyük olasılıkla farklı boy/varyantın aynı barkodu
# taşıması ya da barkod alanının kirliliğidir.
PRICE_ANOMALY_RATIO = 5.0

# Checksum'ı tutan barkod tam güven; sadece gevşek normalizasyonla elde edilen barkod
# eşleşmesi insan onayına yakın durur. Bu skor provider'lar arası kalibre bir değer değil,
# sadece "otomatik onaylanır mı" eşiği (bkz. CLAUDE.md).
CONFIDENCE_CHECKSUM_OK = 100
CONFIDENCE_CHECKSUM_FAIL = 70
MIN_AUTO_APPROVE_CONFIDENCE = 100


def register_udfs(con: duckdb.DuckDBPyConnection) -> None:
    kwargs = {"null_handling": "special"}
    con.create_function("gtin_strict", normalize_gtin, [VARCHAR], VARCHAR, **kwargs)
    con.create_function("gtin_lenient", normalize_gtin_lenient, [VARCHAR], VARCHAR, **kwargs)
    con.create_function(
        "gtin_checksum_ok", lambda s: normalize_gtin(s) is not None, [VARCHAR], BOOLEAN, **kwargs
    )


def build_product_eans(con: duckdb.DuckDBPyConnection) -> int:
    """silver_products.ean (VARCHAR[]) -> ürün başına N satırlık kanonik GTIN tablosu.

    Ürün kimliği tarihten BAĞIMSIZ olduğu için her (site, ürün) çiftinin SADECE EN GÜNCEL
    snapshot'ı kullanılır. Bu bilinçli: Eveshop'ta aynı Shopify ID'sinin yeniden markalanmış
    bir ürüne devredildiği ve barkodun değiştiği doğrulanmış örnekler var (bkz.
    configs/tr/eveshop.yaml -> ean notu) - eski barkodla eşleştirmek yanlış ürünü bağlar.

    Sola sıfır doldurma (GTIN-14) burada kritik: UPC-A (12 hane), EAN-8 ve Gratis'in
    baştaki sıfırı KIRPILMIŞ 11 haneli kodları ancak bu sayede EAN-13 karşılıklarıyla aynı
    anahtara iner. Kontrol hanesi sola eklenen sıfırlardan etkilenmez, o yüzden checksum
    doğrulaması doldurma öncesi/sonrası aynı sonucu verir.

    gtin_lenient kullanılır (uzunluk filtresi uygular ama checksum'ı zorunlu tutmaz) ve
    checksum sonucu AYRI sütunda tutulur: 4-7 hanelik çöp değerler (Watsons'ta 94 satır)
    böylece elenirken, checksum'ı tutmayan ama uzunluğu makul kodlar eşleştirmeye girme
    şansını kaybetmez - sadece daha düşük confidence ile.
    """
    con.execute(f"""
        CREATE OR REPLACE TABLE silver_product_eans AS
        WITH latest AS (
            SELECT * FROM {SOURCE_TABLE}
            QUALIFY ROW_NUMBER() OVER (
                PARTITION BY site_code, source_product_id ORDER BY fetch_date DESC
            ) = 1
        ),
        exploded AS (
            SELECT
                l.site_code,
                l.source_product_id,
                l.fetch_date,
                l.name,
                l.brand_normalized,
                l.sale_price_try,
                TRIM(e) AS ean_raw
            FROM latest l, UNNEST(l.ean) AS t(e)
            WHERE e IS NOT NULL AND TRIM(e) <> ''
        )
        SELECT
            site_code,
            source_product_id,
            fetch_date,
            name,
            brand_normalized,
            sale_price_try,
            ean_raw,
            gtin_lenient(ean_raw) AS gtin,
            gtin_checksum_ok(ean_raw) AS checksum_valid
        FROM exploded
        WHERE gtin_lenient(ean_raw) IS NOT NULL
    """)
    return con.execute("SELECT COUNT(*) FROM silver_product_eans").fetchone()[0]


def build_matches(con: duckdb.DuckDBPyConnection) -> int:
    """GTIN bazında siteler arası eşleştirme + fiyat tutarlılık kontrolü.

    Bir GTIN en az 2 FARKLI sitede görülüyorsa eşleşme adayıdır. Fiyat kontrolü CLAUDE.md'nin
    sanity check kuralını uygular: aynı EAN'de uçuk fiyat farkı (>PRICE_ANOMALY_RATIO) EAN
    hatası şüphesidir - eşleşme silinmez ama needs_review işaretlenir ve confidence'tan
    bağımsız olarak otomatik onaya girmez.
    """
    con.execute(f"""
        CREATE OR REPLACE TABLE silver_ean_matches AS
        WITH grouped AS (
            SELECT
                gtin,
                COUNT(DISTINCT site_code) AS site_count,
                COUNT(*) AS product_count,
                BOOL_AND(checksum_valid) AS all_checksum_valid,
                LIST(DISTINCT site_code) AS sites,
                MIN(sale_price_try) FILTER (WHERE sale_price_try > 0) AS price_min,
                MAX(sale_price_try) FILTER (WHERE sale_price_try > 0) AS price_max,
                COUNT(DISTINCT brand_normalized) AS brand_variants
            FROM silver_product_eans
            GROUP BY gtin
        )
        SELECT
            gtin AS match_id,
            gtin,
            list_sort(sites) AS sites,
            site_count,
            product_count,
            all_checksum_valid,
            price_min,
            price_max,
            ROUND(price_max / NULLIF(price_min, 0), 2) AS price_ratio,
            COALESCE(price_max / NULLIF(price_min, 0) > {PRICE_ANOMALY_RATIO}, false) AS price_anomaly,
            brand_variants,
            CASE WHEN all_checksum_valid
                 THEN {CONFIDENCE_CHECKSUM_OK}
                 ELSE {CONFIDENCE_CHECKSUM_FAIL} END AS confidence,
            (COALESCE(price_max / NULLIF(price_min, 0) > {PRICE_ANOMALY_RATIO}, false)
             OR NOT all_checksum_valid) AS needs_review
        FROM grouped
        WHERE site_count > 1
    """)
    return con.execute("SELECT COUNT(*) FROM silver_ean_matches").fetchone()[0]


def build_matched_products(con: duckdb.DuckDBPyConnection) -> int:
    """silver_products + match_id/ean_primary — silver'ın son hali, golden katmanın girdisi.

    Bir ürünün birden fazla barkodu olabilir (Watsons'ta 9'a kadar). "Hangisi gerçek EAN"
    sorusu burada, veriye bakılarak çözülür - hardcoded bir kural değil:
      1. checksum'ı tutan barkod önce gelir
      2. eşitlikte, daha çok sitede karşılığı olan barkod (gerçek ortak ürün kimliği olma
         olasılığı yüksek) önce gelir
      3. yine eşitse GTIN'in kendisiyle deterministik sıralama (tekrarlanabilirlik)

    Eşleşmeyen ürünler de tabloda KALIR (match_id NULL) - fuzzy matching fallback'inin
    girdisi tam olarak onlar olacak (bkz. CLAUDE.md eşleştirme öncelik sırası).
    Satır granülaritesi silver_products ile aynıdır (site, ürün, TARİH) - yani fiyat
    geçmişinin tamamı korunur, eşleşme bilgisi her güne aynı şekilde iliştirilir.
    """
    con.execute(f"""
        CREATE OR REPLACE TABLE silver_products_matched AS
        WITH ranked AS (
            SELECT
                pe.site_code,
                pe.source_product_id,
                pe.gtin,
                m.match_id,
                m.confidence,
                m.needs_review,
                ROW_NUMBER() OVER (
                    PARTITION BY pe.site_code, pe.source_product_id
                    ORDER BY pe.checksum_valid DESC,
                             COALESCE(m.site_count, 0) DESC,
                             pe.gtin
                ) AS rn
            FROM silver_product_eans pe
            LEFT JOIN silver_ean_matches m ON m.gtin = pe.gtin
        ),
        chosen AS (
            SELECT site_code, source_product_id, gtin AS ean_primary,
                   match_id, confidence AS match_confidence, needs_review AS match_needs_review
            FROM ranked WHERE rn = 1
        )
        SELECT s.*,
               c.ean_primary,
               c.match_id,
               c.match_confidence,
               c.match_needs_review
        FROM {SOURCE_TABLE} s
        LEFT JOIN chosen c
               ON c.site_code = s.site_code
              AND c.source_product_id = s.source_product_id
    """)
    return con.execute("SELECT COUNT(*) FROM silver_products_matched").fetchone()[0]


def report(con: duckdb.DuckDBPyConnection) -> None:
    total_eans = con.execute("SELECT COUNT(*) FROM silver_product_eans").fetchone()[0]
    if total_eans == 0:
        logger.warning("silver_product_eans boş - rapor atlandı")
        return

    invalid = con.execute(
        "SELECT COUNT(*) FROM silver_product_eans WHERE NOT checksum_valid"
    ).fetchone()[0]
    logger.info(
        f"barkod satırı: {total_eans}, checksum tutmayan: {invalid} (%{invalid / total_eans * 100:.1f})"
    )

    logger.info("site başına barkodu olan ürün / toplam ürün:")
    for site, urun, barkodlu in con.execute("""
        SELECT s.site_code,
               COUNT(DISTINCT s.source_product_id) AS urun,
               COUNT(DISTINCT pe.source_product_id) AS barkodlu
        FROM silver_products s
        LEFT JOIN silver_product_eans pe
               ON pe.site_code = s.site_code AND pe.source_product_id = s.source_product_id
        GROUP BY 1 ORDER BY 1
    """).fetchall():
        logger.info(f"  {site:10s} {barkodlu:>6d}/{urun:<6d}")

    matches = con.execute("SELECT COUNT(*) FROM silver_ean_matches").fetchone()[0]
    logger.info(f"siteler arası eşleşen benzersiz GTIN: {matches}")
    for site_count, count in con.execute(
        "SELECT site_count, COUNT(*) FROM silver_ean_matches GROUP BY 1 ORDER BY 1"
    ).fetchall():
        logger.info(f"  {site_count} farklı sitede: {count} GTIN")

    review = con.execute("SELECT COUNT(*) FROM silver_ean_matches WHERE needs_review").fetchone()[0]
    anomaly = con.execute("SELECT COUNT(*) FROM silver_ean_matches WHERE price_anomaly").fetchone()[0]
    logger.info(f"insan onayına düşen eşleşme: {review} (bunların {anomaly} tanesi fiyat anomalisi)")

    matched, all_products = con.execute("""
        SELECT COUNT(*) FILTER (WHERE match_id IS NOT NULL), COUNT(*)
        FROM (SELECT DISTINCT site_code, source_product_id, match_id FROM silver_products_matched)
    """).fetchone()
    logger.info(
        f"EAN ile eşleşen ürün: {matched}/{all_products} (%{matched / all_products * 100:.1f}) "
        "- kalanı fuzzy matching'e kalıyor"
    )


def build(con: duckdb.DuckDBPyConnection) -> None:
    register_udfs(con)
    logger.info(f"barkod normalizasyonu: {build_product_eans(con)} satır -> silver_product_eans")
    logger.info(f"eşleştirme: {build_matches(con)} GTIN -> silver_ean_matches")
    logger.info(f"son silver katmanı: {build_matched_products(con)} satır -> silver_products_matched")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="silver_products -> EAN eşleştirme (silver'ın son katmanı, golden'ın girdisi)"
    )
    parser.add_argument("--no-report", action="store_true")
    args = parser.parse_args()

    con = duckdb.connect(str(DB_PATH))
    try:
        build(con)
        if not args.no_report:
            report(con)
    finally:
        con.close()


if __name__ == "__main__":
    main()
