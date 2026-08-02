from __future__ import annotations

from pathlib import Path

import duckdb
import yaml

# Bu modül site adı BİLMEZ: configs/tr/{site}.yaml -> field_mapping'i okuyup SQL'i otomatik
# üretir. Yeni bir site eklemek = configs/tr/{site}.yaml'a field_mapping yazmak - buradaki kod
# değişmez (bkz. CLAUDE.md "plugin mimarisi" ilkesi). field_mapping'teki "strategy" alanı
# yorumlanır (direct_path/computed/static/none), hiçbir strategy site'a özel davranmaz.
#
# Kaynak tablo clean_{site} DEĞİL, raw_{site}: bazı formüller (örn. Watsons'ın
# "otherPrices[priceSource='MEMBER'].value" struct-list erişimi, Gratis'in
# "attributes.categories" liste alanı) clean_ tablolarında YOK çünkü clean_ leaf-only filtre
# uyguluyor (bkz. matching/duckdb_pipeline.py). raw_{site} tüm sütunları (liste/struct dahil)
# koruduğu için tek/tutarlı kaynak olarak seçildi - kendi dedupe'ini burada, generic olarak
# (product_id_field + en yeni _fetch_date) uyguluyoruz, clean_ tablolarının yaptığıyla aynı
# mantık, ayrıca tekrar yazılmadı.

DB_PATH = Path(__file__).resolve().parent / "pricebot.duckdb"
CONFIG_DIR = Path(__file__).resolve().parent.parent / "configs" / "tr"

# Silver katmanının kanonik şeması - hangi alanların var olacağının TEK listesi. Bir site
# field_mapping'inde bu alanlardan birini tanımlamazsa NULL olarak üretilir (hata değil).
CANONICAL_FIELDS = [
    "source_sku", "ean", "name", "brand", "url", "image_url", "currency",
    "list_price_try", "sale_price_try", "conditional_promo_price_try", "discount_rate",
    "stock_status_raw", "is_in_stock", "stock_qty", "category_path",
]


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
    QUALIFY ROW_NUMBER() OVER (PARTITION BY {dedupe_key} ORDER BY _fetch_date DESC) = 1
)
"""


def main() -> None:
    con = duckdb.connect(str(DB_PATH))
    try:
        sites = discover_sites()
        print(f"field_mapping bulunan siteler: {sites}")

        selects = []
        for site in sites:
            config = load_site_config(site)
            sql = build_site_select(site, config)
            try:
                count = con.execute(f"SELECT COUNT(*) FROM ({sql})").fetchone()[0]
                print(f"  {site}: {count} satır (deneme sorgusu başarılı)")
            except Exception as exc:
                print(f"  {site}: HATA - {exc}")
                raise
            selects.append(sql)

        union_sql = "\nUNION ALL\n".join(selects)
        con.execute(f"""
            CREATE OR REPLACE TABLE silver_products AS
            SELECT
                md5(site_code || '_' || source_product_id || '_' || CAST(fetch_date AS VARCHAR)) AS silver_id,
                *
            FROM ({union_sql})
        """)

        total = con.execute("SELECT COUNT(*) FROM silver_products").fetchone()[0]
        print(f"\nsilver_products oluşturuldu: {total} satır")

        print("\nsite başına satır sayısı:")
        for row in con.execute("SELECT site_code, COUNT(*) FROM silver_products GROUP BY 1 ORDER BY 1").fetchall():
            print(" ", row)

        dup = con.execute("""
            SELECT COUNT(*) FROM (
                SELECT site_code, source_product_id, COUNT(*) c
                FROM silver_products GROUP BY 1,2 HAVING c > 1
            )
        """).fetchone()[0]
        print(f"\nkalite kontrol: (site_code, source_product_id) tekillik ihlali = {dup} (0 olmalı)")

        print("\nalan bazında NULL oranı:")
        for field in ["ean", "sale_price_try", "is_in_stock", "category_path", "brand"]:
            n = con.execute(f"SELECT COUNT(*) FROM silver_products WHERE {field} IS NULL").fetchone()[0]
            print(f"  {field:20s} NULL: {n}/{total} (%{n/total*100:.1f})")
    finally:
        con.close()


if __name__ == "__main__":
    main()
