from __future__ import annotations

# core/ ve matching/ birbirini import etmez (bkz. CLAUDE.md) ama ikisi de aynı basit
# "nokta ile ayrılmış path çöz" ihtiyacını paylaşıyor - tek doğruluk kaynağı olarak burada,
# shared/ altında yaşar.


def resolve_path(obj: object, path: str) -> object:
    """Nokta ile ayrılmış path'i (örn. 'prices.discountedPrice') sırayla çözer."""
    current = obj
    for part in path.split("."):
        if current is None:
            return None
        if isinstance(current, list):
            try:
                current = current[int(part)]
            except (ValueError, IndexError):
                return None
        elif isinstance(current, dict):
            current = current.get(part)
        else:
            return None
    return current
