from __future__ import annotations

import argparse
from pathlib import Path

import duckdb
import yaml

from matching.duckdb_pipeline import load_site
from shared.logging_config import get_logger

# Eveshop'un ARA SIRA çalıştırılan HTML-tazeleme görevi: configs/tr/eveshop.yaml'daki aktif
# kaynak (products.json) barkod/eve_price/stock_qty vermiyor (bkz. field_mapping'teki
# strategy:none notları) - bu 3 alan SADECE HTML kategori sayfalarında var (eski, artık dormant
# category_search_api_html_barkod_icin_gelecek bloğu). Bu script:
#   1. Dormant "_html_barkod_icin_gelecek" bloklarını birleştirip raw_eveshop_html'i kurar
#      (raw_eveshop'a HİÇ dokunmadan - ayrı tablo, ayrı ingest-log tracking_key, bkz.
#      matching/duckdb_pipeline.py::load_site).
#   2. raw_eveshop_html'den (product_id, barkod, eve_price, qty) çıkaran küçük bir lookup
#      tablosu (eveshop_html_supplement) üretir - matching/analysis/build_clean.py bunu
#      clean_eveshop kurulurken LEFT JOIN ile Eveshop'a özel bir supplement olarak kullanır.
#
# raw_eveshop_html TAM eski HTML şemasını taşır (variant.*, eve_price, qty, brand, vb. - ~91
# kolon) - traceability için tutuluyor, ama field_mapping/clean_eveshop SADECE
# eveshop_html_supplement'ı okur, raw_eveshop_html'i doğrudan değil.

CONFIG_PATH = Path(__file__).resolve().parent.parent / "configs" / "tr" / "eveshop.yaml"
DB_PATH = Path(__file__).resolve().parent / "pricebot.duckdb"
RAW_TABLE = "raw_eveshop_html"
TRACKING_KEY = "eveshop_html"
SUPPLEMENT_TABLE = "eveshop_html_supplement"

logger = get_logger(__name__)


def build_html_config() -> dict:
    """configs/tr/eveshop.yaml'daki aktif config + dormant "_html_barkod_icin_gelecek"
    bloklarını birleştirip HTML kaynağı için geçerli bir config üretir. product_id_field
    override edilir - HTML şeması eski 'product_id' alanını kullanıyor, aktif config'in
    products.json'a özgü 'id'sini değil."""
    base = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))
    return {
        **base,
        "archive": base["archive_html_barkod_icin_gelecek"],
        "category_search_api": base["category_search_api_html_barkod_icin_gelecek"],
        "rate_limit": base["rate_limit_html_barkod_icin_gelecek"],
        "main_categories": base["main_categories_html_barkod_icin_gelecek"],
        "product_id_field": "product_id",
    }


def build_supplement(con: duckdb.DuckDBPyConnection) -> int:
    """raw_eveshop_html -> eveshop_html_supplement (product_id başına 1 satır).

    Aynı ürün birden fazla kategoride tarandığı için (cok-satan-urunler diğer 8 kategoriyle
    örtüşüyor, bkz. configs/tr/eveshop.md) VE arşivde birden fazla _fetch_date'in objeleri
    aynı anda durabildiği için (ara sıra çalıştırılan bir görev, eski objeler silinmiyor)
    raw_eveshop_html'de product_id tekil DEĞİL. Dedup normalize.py'nin silver dedup'ından FARKLI
    olarak burada TARİH de korunmuyor (tek bir "şu an bilinen en güncel" satır isteniyor, fiyat
    geçmişi değil) - önce EN GÜNCEL _fetch_date, eşitlikte _row_id ile deterministik seçim."""
    con.execute(f"""
        CREATE OR REPLACE TABLE {SUPPLEMENT_TABLE} AS
        SELECT
            product_id,
            list_value("variant.barcode") AS ean_html,
            CASE WHEN eve_price > 0 THEN eve_price ELSE NULL END AS eve_price_html,
            qty AS qty_html,
            _fetch_date AS html_fetch_date
        FROM (
            SELECT * FROM {RAW_TABLE}
            QUALIFY ROW_NUMBER() OVER (PARTITION BY product_id ORDER BY _fetch_date DESC, _row_id) = 1
        )
    """)
    return con.execute(f"SELECT COUNT(*) FROM {SUPPLEMENT_TABLE}").fetchone()[0]


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Eveshop HTML barkod/eve_price/qty tazeleme - ara sıra çalıştırılır (bkz. modül başı notu)"
    )
    parser.add_argument(
        "--full-refresh", action="store_true", default=True,
        help="varsayılan zaten tam yenileme - HTML arşivi büyük/nadir olduğu için inkremental takip şu an gereksiz",
    )
    args = parser.parse_args()

    html_config = build_html_config()
    con = duckdb.connect(str(DB_PATH))
    try:
        raw_count = load_site(
            con, "eveshop", full_refresh=args.full_refresh,
            config_override=html_config, raw_table_override=RAW_TABLE,
            tracking_key_override=TRACKING_KEY,
        )
        logger.info(f"{RAW_TABLE}: {raw_count} satır")
        supplement_count = build_supplement(con)
        logger.info(f"{SUPPLEMENT_TABLE}: {supplement_count} ürün (product_id başına 1 satır)")
    finally:
        con.close()


if __name__ == "__main__":
    main()
