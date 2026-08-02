from __future__ import annotations

import argparse
import csv
from pathlib import Path

import duckdb

# Bu script matching/analysis/{site}/columns.csv'yi ÜRETİR - elle düzenlenmez, tekrar
# çalıştırılabilir bir profildir (raw_{site} değiştikçe yeniden koşulur). decisions.yaml'daki
# gerçek KARARLAR (kullan/at/sakla_ama_kullanma + analiz notu) buradan beslenir ama insan
# tarafından yazılır - bu ayrım bilinçli: mekanik/tekrarlanabilir olan (doluluk oranı, örnek
# değer) ile insan yargısı gerektiren (bu alan neden var, işimize yarar mı) karışmasın diye.

DB_PATH = Path(__file__).resolve().parent.parent / "pricebot.duckdb"


def profile_site(con: duckdb.DuckDBPyConnection, site: str) -> list[dict]:
    table = f"raw_{site}"
    total = con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
    rows = []
    for name, dtype, *_ in con.execute(f"DESCRIBE {table}").fetchall():
        quoted = '"' + name.replace('"', '""') + '"'
        # Liste/struct tipli kolonlarda distinct/sample farklı davranır - value'yu VARCHAR'a
        # çevirip öyle karşılaştırıyoruz (DuckDB LIST/STRUCT üzerinde native DISTINCT/=
        # bazı durumlarda desteklenmiyor ya da yanıltıcı sonuç veriyor).
        stats = con.execute(f"""
            SELECT
                COUNT({quoted}) AS filled,
                COUNT(DISTINCT {quoted}::VARCHAR) AS distinct_count,
                ANY_VALUE({quoted}::VARCHAR) FILTER (WHERE {quoted} IS NOT NULL) AS sample
            FROM {table}
        """).fetchone()
        filled, distinct_count, sample = stats
        sample_text = (sample or "")[:120].replace("\n", " ").replace("\r", " ")
        rows.append({
            "path": name,
            "dtype": dtype,
            "fill_pct": round(filled * 100 / total, 1) if total else 0.0,
            "distinct_count": distinct_count,
            "sample_value": sample_text,
        })
    rows.sort(key=lambda r: r["path"])
    return rows


def write_csv(site: str, rows: list[dict]) -> Path:
    out_dir = Path(__file__).resolve().parent / site
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "columns.csv"
    with out_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["path", "dtype", "fill_pct", "distinct_count", "sample_value"])
        writer.writeheader()
        writer.writerows(rows)
    return out_path


def main() -> None:
    parser = argparse.ArgumentParser(description="raw_{site} kolon profili üretir (matching/analysis/{site}/columns.csv)")
    parser.add_argument("site")
    args = parser.parse_args()

    con = duckdb.connect(str(DB_PATH), read_only=True)
    try:
        rows = profile_site(con, args.site)
    finally:
        con.close()

    out_path = write_csv(args.site, rows)
    print(f"{args.site}: {len(rows)} kolon -> {out_path}")


if __name__ == "__main__":
    main()
