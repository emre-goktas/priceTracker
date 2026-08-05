from __future__ import annotations

import argparse
from pathlib import Path

import duckdb
import yaml

# raw_{site} -> clean_{site}: ilgili {site}/decisions.yaml'daki 'keep' + 'use' kovalarında
# onaylanmış kolonları seçip (ürün, tarih) bazında dedup uygulayan, TEKRAR ÇALIŞTIRILABİLİR
# script. Önceki oturumlarda bu adım elle ALTER TABLE DROP COLUMN ile yapılıyordu - raw_{site}
# bir --full-refresh'te MinIO'dan tam kolonlu geri geldiğinde o manuel silmeler kaybolurdu.
# Artık clean_{site} decisions.yaml'dan HER ÇALIŞTIRMADA yeniden türetiliyor, raw_{site}'a hiç
# dokunmuyor (raw her zaman MinIO'nun tam/ham yansıması kalmalı, bkz. CLAUDE.md ilke 4).
#
# Dedup mantığı matching/normalize.py -> build_site_select ile AYNI sözleşme: aynı
# (product_id_field, _fetch_date) ikilisinde _row_id'ye göre ilk kayıt tutulur - aynı günkü
# sayfalama tekrarları temizlenir, günler arası geçmiş KORUNUR.
#
# python -m matching.analysis.build_clean [site ...]  (site verilmezse configs/tr/*.yaml'daki
# field_mapping'i olan tüm siteler)

DB_PATH = Path(__file__).resolve().parent.parent / "pricebot.duckdb"
CONFIG_DIR = Path(__file__).resolve().parents[2] / "configs" / "tr"
ANALYSIS_DIR = Path(__file__).resolve().parent

# Ham kolonun DEĞERİNİ temizleyen, nadir/bilinçli istisnalar (bkz. ilgili decisions.yaml notu).
# clean_{site}'ın genel kuralı isim/tip DEĞİŞTİRMEMEK - bu sadece "ham hâliyle kullanılamaz"
# onaylanmış alanlar için (Gratis'in description_temiz/tedarikci_bilgisi kararıyla aynı istisna
# sınıfı). Site adı burada geçiyor ama bu core/ değil - matching/analysis/ zaten site başına
# decisions.yaml/columns.csv barındıran, insan onayı gerektiren bir inceleme alanı.
VALUE_CLEAN_OVERRIDES = {
    "watsons": {
        "drop": ["images"],
        "add_sql": [
            "CASE WHEN len(images) > 0 THEN 'https://www.watsons.com.tr' || images[1].url "
            "ELSE NULL END AS image_url",
        ],
    },
}


def _quote(col: str) -> str:
    return '"' + col.replace('"', '""') + '"'


def discover_sites() -> list[str]:
    return sorted(
        p.stem for p in ANALYSIS_DIR.iterdir()
        if p.is_dir() and (p / "decisions.yaml").exists()
    )


def load_selected_columns(site: str) -> list[str]:
    """decisions.yaml -> 'keep' + 'use' kovalarındaki path'ler, dosyadaki sıra korunarak."""
    decisions_path = ANALYSIS_DIR / site / "decisions.yaml"
    data = yaml.safe_load(decisions_path.read_text(encoding="utf-8"))
    cols = data["columns"]
    return [e["path"] for e in cols["keep"]] + [e["path"] for e in cols["use"]]


def build_clean(con: duckdb.DuckDBPyConnection, site: str) -> None:
    config = yaml.safe_load((CONFIG_DIR / f"{site}.yaml").read_text(encoding="utf-8"))
    dedupe_key = config["product_id_field"]

    columns = load_selected_columns(site)
    override = VALUE_CLEAN_OVERRIDES.get(site, {})
    for dropped in override.get("drop", []):
        if dropped in columns:
            columns.remove(dropped)

    select_list = ",\n    ".join([_quote(c) for c in columns] + override.get("add_sql", []))

    raw_total = con.execute(f"SELECT COUNT(*) FROM raw_{site}").fetchone()[0]

    con.execute(f"""
        CREATE OR REPLACE TABLE clean_{site} AS
        SELECT
            {select_list}
        FROM (
            SELECT * FROM raw_{site}
            QUALIFY ROW_NUMBER() OVER (PARTITION BY {_quote(dedupe_key)}, _fetch_date ORDER BY _row_id) = 1
        )
    """)

    clean_total = con.execute(f"SELECT COUNT(*) FROM clean_{site}").fetchone()[0]
    col_count = len(con.execute(f"DESCRIBE clean_{site}").fetchall())
    dup_removed = raw_total - clean_total
    print(
        f"{site}: raw_{site} {raw_total} satır -> clean_{site} {clean_total} satır "
        f"({dup_removed} dedup ile atıldı), {col_count} kolon"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="raw_{site} -> clean_{site} (decisions.yaml'dan, dedup'lı)")
    parser.add_argument("sites", nargs="*", help="sadece bu siteler (varsayılan: decisions.yaml'ı olan hepsi)")
    args = parser.parse_args()

    con = duckdb.connect(str(DB_PATH))
    try:
        for site in args.sites or discover_sites():
            build_clean(con, site)
    finally:
        con.close()


if __name__ == "__main__":
    main()
