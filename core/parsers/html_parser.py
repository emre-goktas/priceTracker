from __future__ import annotations

import html
import json
import re

# Bu modül site adı bilmez: HTML içine gömülü JSON'u regex ile çıkaran genel amaçlı
# yardımcılar barındırır (Eveshop için yazıldı, ama Shopify/benzeri temaları kullanan
# herhangi bir site — örn. ileride Sephora — bunu yeniden kullanabilir). Site-özel regex
# pattern'leri burada değil, configs/tr/{site}.yaml içinde yaşar (bkz. CLAUDE.md).


def extract_all_json_blocks(text: str, pattern: str) -> list[object]:
    """HTML içindeki tüm eşleşmeleri (attribute veya <script> içeriği) bulur, her birini
    HTML-unescape edip JSON olarak parse eder. Parse edilemeyen eşleşmeler atlanır (loglanmaz,
    çağıran taraf boş/eksik sonucu MinIO'daki ham veriden zaten görebilir)."""
    results: list[object] = []
    for match in re.finditer(pattern, text, re.DOTALL):
        raw = html.unescape(match.group(1))
        try:
            results.append(json.loads(raw))
        except json.JSONDecodeError:
            continue
    return results


def extract_first_group(text: str, pattern: str) -> str | None:
    """Düz regex ile ilk capture group'u döner (örn. bir URL path'i) - eşleşme yoksa None."""
    match = re.search(pattern, text, re.DOTALL)
    return match.group(1) if match else None
