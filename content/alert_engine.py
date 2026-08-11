from __future__ import annotations

import argparse
import asyncio
from html import escape
from pathlib import Path

import asyncpg

from content.publishers.telegram import send_message, send_photo
from shared.logging_config import get_logger
from shared.pg_client import get_pg_pool

logger = get_logger(__name__)

SCHEMA_PATH = Path(__file__).resolve().parent.parent / "db" / "postgres_schema.sql"

DEFAULT_THRESHOLD_PCT = 10.0
MAX_ITEMS_TO_SEND = 30
SEND_DELAY_SECONDS = 0.3
# Bir sitede aynı anda bu sayıdan fazla düşüş varsa muhtemelen site-çapında bir kampanya
# penceresi açılmıştır (bkz. Watsons'ın otherPrices'ı toplu açılıp kapanıyor - proje hafızası),
# tek tek fotoğraflı ürün spam'ı yerine tek özet mesaja indirilir.
MASS_EVENT_THRESHOLD = 50

CONDITIONAL_LABELS = {
    "gratis": "🎫 Gratis Kart Fiyatı",
    "rossmann": "🎫 Rossmann Card Fiyatı",
    "eveshop": "🎫 EVE Kart+'a Özel",
    "watsons": "🎫 Üye Fiyatı",
}

# Her (site_code, source_product_id) için en son 2 ÇALIŞTIRMA (fetch_at) karşılaştırılır
# (site-bazlı, siteler arası eşleştirme YOK - bkz. CLAUDE.md "Şu An Kapsam Dışı"). DÜZELTME
# (2026-08-11): eskiden fetch_date (günlük) idi - artık fetch_at (çalıştırma-bazlı) olduğu için
# gün-içi/intraday fiyat değişiklikleri de yakalanıyor, günler arası karşılaştırma da aynı
# mekanizmayla otomatik çalışıyor (rn=1/rn=2 "recency"nin ne anlama geldiğinden bağımsız).
# is_in_stock filtresi şart: stokta olmayan ürünlerin fiyatı kayan/güvenilmez artık değer
# olabiliyor (bkz. proje hafızası, Watsons örneği) - yoksa yanlış-pozitif alarm üretir.
#
# BUG + DÜZELTME (2026-08-11, canlı yakalandı): is_in_stock filtresi eskiden ROW_NUMBER'dan
# ÖNCE uygulanıyordu - bir ürünün EN GÜNCEL satırı stok dışıysa filtre onu tamamen eleyip
# rn=1'i DAHA ESKİ bir "stoktaymış gibi görünen" satıra kaydırıyordu, yani "güncel" diye
# gösterilen veri aslında güncel değildi (kullanıcı canlı sitede "stokta yok" görüp yakaladı).
# Çözüm: `latest_overall` ile l'nin (rn=1, stok-filtreli) GERÇEKTEN o ürünün mutlak en son
# satırı olduğu ayrıca doğrulanıyor - aradaki bir yerde stoktan düşmüşse alarm hiç üretilmez.
#
# "Koşulsuz" (sale_price_try) ve "koşullu/kart" (conditional_promo_price_try) fiyatlar AYRI
# alarmlar DEĞİL - hangisi düşükse (LEAST) MÜŞTERİNİN GERÇEKTE ÖDEYECEĞİ fiyat odur, alarm
# bu "efektif fiyat"ın düşüşüne göre tetiklenir. Postgres'in LEAST()'i NULL'ı YOK SAYAR (ikisi
# de NULL değilse), yani conditional_promo_price_try boşsa efektif fiyat otomatik sale_price_try
# olur - kullanıcının Eveshop örneği (799 TL normal / 319.50 TL EVE Kart+) bunun içindir: kart
# fiyatı varken normal fiyata bakmak yanıltıcı, gerçek karar noktası ikisinin ucuz olanı.
#
# pricing.alerted_drops LEFT JOIN'i: silver_id artık (site, ürün, ÇALIŞTIRMA) üçlüsünü
# kodluyor - aynı çalıştırmanın sonucu tekrar işlense bile aynı düşüş SADECE 1 kez bildirilir,
# daha önce alerted_drops'a yazılmışsa bu sorgu bir daha döndürmez.
DROP_QUERY = """
WITH ranked AS (
    SELECT
        silver_id, site_code, source_product_id, name, brand, url, image_url,
        sale_price_try, conditional_promo_price_try,
        LEAST(sale_price_try, conditional_promo_price_try) AS effective_price,
        fetch_at,
        is_in_stock,
        ROW_NUMBER() OVER (PARTITION BY site_code, source_product_id ORDER BY fetch_at DESC) AS rn,
        LEAD(sale_price_try) OVER (PARTITION BY site_code, source_product_id ORDER BY fetch_at DESC) AS old_sale,
        LEAD(conditional_promo_price_try) OVER (PARTITION BY site_code, source_product_id ORDER BY fetch_at DESC) AS old_conditional,
        LEAD(LEAST(sale_price_try, conditional_promo_price_try)) OVER (PARTITION BY site_code, source_product_id ORDER BY fetch_at DESC) AS old_effective
    FROM pricing.silver_products
)
SELECT
    r.silver_id, r.site_code, r.source_product_id, r.name, r.brand, r.url, r.image_url,
    r.old_sale, r.sale_price_try AS new_sale,
    r.old_conditional, r.conditional_promo_price_try AS new_conditional,
    r.old_effective, r.effective_price AS new_effective,
    ROUND((100 * (r.old_effective - r.effective_price) / r.old_effective)::numeric, 1) AS pct_drop
FROM ranked r
LEFT JOIN pricing.alerted_drops a ON a.silver_id = r.silver_id
WHERE r.rn = 1
  AND r.is_in_stock IS TRUE
  AND r.sale_price_try IS NOT NULL
  AND r.old_effective IS NOT NULL
  AND r.effective_price < r.old_effective
  AND (r.old_effective - r.effective_price) / r.old_effective * 100 >= $1
  AND a.silver_id IS NULL
ORDER BY pct_drop DESC
"""


def format_caption(row: asyncpg.Record) -> str:
    site_label = row["site_code"].capitalize()
    name = escape(row["name"] or "")
    brand = escape(row["brand"] or "")

    lines = [f"🛍 <b>{site_label}</b> — {name}"]
    if brand:
        lines.append(brand)
    lines.append("")

    old_sale, new_sale = row["old_sale"], row["new_sale"]
    if old_sale != new_sale:
        lines.append(f"Normal Fiyat: <s>{old_sale:.2f} TL</s> → <b>{new_sale:.2f} TL</b>")
    else:
        lines.append(f"Normal Fiyat: {new_sale:.2f} TL")

    # BUG + DÜZELTME (2026-08-11, canlı yakalandı): koşullu/kart fiyatı önceden new_sale'den
    # PAHALI olsa bile gösteriliyordu (Gratis'in promotionPrice'ı bazı ürünlerde - özellikle
    # sale_price_try <= 250 TL iken - "önizleme"/güvenilmez bir değer taşıyor, field_mapping'in
    # kendi notu bunu zaten söylüyordu). Kart fiyatı gerçekten daha ucuz DEĞİLSE göstermenin
    # hiçbir anlamı yok - kullanıcı "kart fiyatı yanlış" diye bildirdi. Artık SADECE gerçekten
    # indirim ise (new_conditional < new_sale) gösteriliyor.
    new_conditional = row["new_conditional"]
    if new_conditional is not None and new_conditional < new_sale:
        label = CONDITIONAL_LABELS.get(row["site_code"], "🎫 Üye Fiyatı")
        old_conditional = row["old_conditional"]
        if old_conditional is not None and old_conditional != new_conditional:
            lines.append(f"{label}: <s>{old_conditional:.2f} TL</s> → <b>{new_conditional:.2f} TL</b>")
        else:
            lines.append(f"{label}: <b>{new_conditional:.2f} TL</b>")

        gap_pct = round((new_sale - new_conditional) / new_sale * 100)
        lines.append(f"💡 Kart ile normal fiyattan %{gap_pct} daha ucuz")

    lines.append("")
    lines.append(f"📉 <b>%{row['pct_drop']}</b> düştü")
    # Görünen metin gerçek URL'in AYNISI olmalı - Telegram, <a href> ile farklı bir görünen
    # metin (örn. "Ürüne git") kullanılırsa anti-phishing "Bağlantıyı Aç?" onay penceresi
    # gösteriyor. Ham URL'i olduğu gibi yazmak Telegram'ın otomatik link algılamasına
    # bırakır - tıklanınca direkt açılır, ekstra onay adımı olmaz.
    lines.append(escape(row["url"]))
    return "\n".join(lines)


async def ensure_schema(pool: asyncpg.Pool) -> None:
    schema_sql = SCHEMA_PATH.read_text(encoding="utf-8")
    async with pool.acquire() as conn:
        await conn.execute(schema_sql)


async def find_price_drops(pool: asyncpg.Pool, threshold_pct: float) -> list[asyncpg.Record]:
    async with pool.acquire() as conn:
        return await conn.fetch(DROP_QUERY, threshold_pct)


async def record_alerted(pool: asyncpg.Pool, rows: list[asyncpg.Record]) -> None:
    payload = [(r["silver_id"], r["old_effective"], r["new_effective"]) for r in rows]
    async with pool.acquire() as conn:
        await conn.executemany(
            """
            INSERT INTO pricing.alerted_drops (silver_id, old_effective_price, new_effective_price)
            VALUES ($1, $2, $3)
            ON CONFLICT (silver_id) DO NOTHING
            """,
            payload,
        )


async def send_drops(rows: list[asyncpg.Record]) -> None:
    # Bir sitede MASS_EVENT_THRESHOLD'u aşan düşüş, muhtemelen site-çapında tek bir kampanya
    # olayı (tek tek ürün "fırsatı" değil) - fotoğraflı spam yerine tek özet mesaja indirilir.
    # Kalan (küçük gruplu) siteler eskisi gibi bireysel, görselli mesaj alır.
    by_site: dict[str, list[asyncpg.Record]] = {}
    for row in rows:
        by_site.setdefault(row["site_code"], []).append(row)

    individual: list[asyncpg.Record] = []
    mass_summaries: list[str] = []
    for site_code, site_rows in by_site.items():
        if len(site_rows) > MASS_EVENT_THRESHOLD:
            avg_drop = sum(r["pct_drop"] for r in site_rows) / len(site_rows)
            max_drop = max(r["pct_drop"] for r in site_rows)
            mass_summaries.append(
                f"📢 <b>{site_code.capitalize()}</b>'ta toplu kampanya tespit edildi: "
                f"<b>{len(site_rows)}</b> üründe fiyat düştü "
                f"(ortalama %{avg_drop:.1f}, en yüksek %{max_drop:.1f})"
            )
        else:
            individual.extend(site_rows)

    await send_message(f"📉 <b>{len(rows)}</b> üründe fiyat fırsatı bulundu:")

    for summary in mass_summaries:
        await send_message(summary)
        await asyncio.sleep(SEND_DELAY_SECONDS)

    to_send = individual[:MAX_ITEMS_TO_SEND]
    for row in to_send:
        caption = format_caption(row)
        if row["image_url"]:
            await send_photo(row["image_url"], caption)
        else:
            await send_message(caption)
        await asyncio.sleep(SEND_DELAY_SECONDS)

    if len(individual) > MAX_ITEMS_TO_SEND:
        await send_message(f"... ve {len(individual) - MAX_ITEMS_TO_SEND} ürün daha")


async def run_alerts(threshold_pct: float = DEFAULT_THRESHOLD_PCT) -> int:
    pool = await get_pg_pool()
    await ensure_schema(pool)
    rows = await find_price_drops(pool, threshold_pct)
    if not rows:
        logger.info(f"Eşik (%{threshold_pct}) üstü, daha önce bildirilmemiş fiyat düşüşü yok")
        return 0

    await send_drops(rows)
    await record_alerted(pool, rows)
    logger.info(f"{len(rows)} fiyat düşüşü Telegram'a gönderildi (eşik: %{threshold_pct})")
    return len(rows)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="pricing.silver_products'ta efektif fiyat düşüşü tespit edip Telegram'a gönderir"
    )
    parser.add_argument(
        "--threshold", type=float, default=DEFAULT_THRESHOLD_PCT,
        help="alarm eşiği yüzde olarak (varsayılan: 10)",
    )
    args = parser.parse_args()
    asyncio.run(run_alerts(args.threshold))


if __name__ == "__main__":
    main()
