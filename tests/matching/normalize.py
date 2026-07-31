from __future__ import annotations

from pathlib import Path

import duckdb

# Bu script tests/ altında (matching/normalize.py DEĞİL) - kullanıcının isteğiyle önce burada
# denenip doğrulanıyor, sonuç iyiyse gerçek matching/normalize.py'ye taşınacak. Sadece Gratis
# kapsıyor (diğer 3 site için field_mapping henüz yazılmadı, bkz. configs/tr/{site}.yaml).
#
# Kaynak: matching/pricebot.duckdb -> clean_gratis (zaten leaf-only + id bazında dedupe'lu).
# category_path (attributes.categories, bir LİSTE) clean_gratis'te YOK çünkü clean_ tabloları
# bilerek liste-tipi sütunları eliyor (bkz. matching/duckdb_pipeline.py) - bu yüzden ayrıca
# raw_gratis'ten (id bazında tek satıra indirgenerek) çekilip join edilir.
#
# Eşleme kaynağı: configs/tr/gratis.yaml -> field_mapping (confidence etiketli, 2026-08-01'de
# gerçek sepet/checkout testiyle doğrulandı) - aşağıdaki SQL o eşlemeyi birebir uygular.

DB_PATH = Path(__file__).resolve().parents[2] / "matching" / "pricebot.duckdb"

CREATE_SILVER_GRATIS = """
CREATE OR REPLACE TABLE silver_gratis AS
WITH category_path AS (
    -- raw_gratis'te ayni id sayfalama kaymasi yuzunden birden fazla kez gecebiliyor
    -- (bkz. clean_ tablolarinin neden var oldugu) - id basina tek satir garanti edilir.
    SELECT id, "attributes.categories" AS category_path
    FROM raw_gratis
    QUALIFY ROW_NUMBER() OVER (PARTITION BY id ORDER BY _row_id) = 1
)
SELECT
    g.id                                      AS source_product_id,
    'gratis'                                   AS site_code,
    g."attributes.eanUpc"                       AS ean,
    g."attributes.displayName"                   AS name,
    g."attributes.brand"                           AS brand,
    g."shareLink"                                   AS url,
    g."prices.currency"                              AS currency,
    ROUND(g."prices.normalPrice" / 100.0, 2)           AS list_price_try,
    ROUND(g."prices.discountedPrice" / 100.0, 2)         AS sale_price_try,
    ROUND(g."prices.promotionPrice" / 100.0, 2)            AS conditional_promo_price_try,
    g."prices.discountRate"                                  AS discount_rate,
    g."stockStatus"                                          AS stock_status_raw,
    (g."stockStatus" != 'NONE')                                AS is_in_stock,
    c.category_path                                              AS category_path,
    g."_fetch_date"                                                AS fetch_date
FROM clean_gratis g
LEFT JOIN category_path c ON c.id = g.id
"""

DEDUPE_SAFETY_NET = """
CREATE OR REPLACE TABLE silver_gratis AS
SELECT * FROM silver_gratis
QUALIFY ROW_NUMBER() OVER (PARTITION BY source_product_id ORDER BY fetch_date DESC) = 1
"""


def main() -> None:
    con = duckdb.connect(str(DB_PATH))
    try:
        con.execute(CREATE_SILVER_GRATIS)

        total = con.execute("SELECT COUNT(*) FROM silver_gratis").fetchone()[0]
        distinct_ids = con.execute("SELECT COUNT(DISTINCT source_product_id) FROM silver_gratis").fetchone()[0]
        print(f"silver_gratis oluşturuldu: {total} satır, {distinct_ids} benzersiz source_product_id")

        if total != distinct_ids:
            print(f"UYARI: {total - distinct_ids} duplicate satır bulundu, temizleniyor...")
            con.execute(DEDUPE_SAFETY_NET)
            total = con.execute("SELECT COUNT(*) FROM silver_gratis").fetchone()[0]
            print(f"temizlendi -> {total} satır kaldı")
        else:
            print("duplicate yok, tablo zaten temiz.")

        cols = con.execute("DESCRIBE silver_gratis").fetchall()
        print(f"\nşema ({len(cols)} sütun):")
        for name, dtype, *_ in cols:
            print(f"  {name}: {dtype}")

        print("\nörnek 3 satır:")
        sample = con.execute(
            "SELECT source_product_id, name, list_price_try, sale_price_try, "
            "conditional_promo_price_try, is_in_stock FROM silver_gratis LIMIT 3"
        ).fetchall()
        for row in sample:
            print(" ", row)

        null_ean = con.execute("SELECT COUNT(*) FROM silver_gratis WHERE ean IS NULL").fetchone()[0]
        null_price = con.execute("SELECT COUNT(*) FROM silver_gratis WHERE sale_price_try IS NULL").fetchone()[0]
        print(f"\nkalite kontrol: ean NULL={null_ean}, sale_price_try NULL={null_price}")
    finally:
        con.close()


if __name__ == "__main__":
    main()
