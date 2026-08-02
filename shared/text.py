from __future__ import annotations

import html
import re
import unicodedata

# Genel amaçlı metin normalizasyonu. Site adı BİLMEZ — burada yaşayan her kural tüm siteler
# için aynı şekilde uygulanır (site-özel istisna gerekiyorsa yeri configs/tr/{site}.yaml'daki
# field_mapping formülüdür, burası değil).
#
# core/storage.py (MinIO klasör slug'ı) ve matching/normalize.py (silver metin temizliği)
# aynı Türkçe karakter tablosunu paylaşıyordu — tek doğruluk kaynağı olarak buraya toplandı.

_TR_ASCII = str.maketrans({
    "İ": "i", "I": "i", "ı": "i",
    "Ğ": "g", "ğ": "g",
    "Ü": "u", "ü": "u",
    "Ş": "s", "ş": "s",
    "Ö": "o", "ö": "o",
    "Ç": "c", "ç": "c",
})

# Görünmez karakterler: BOM (﻿) Rossmann'ın ürün adlarında gerçekten görüldü (34 satır),
# zero-width space/joiner'lar HTML'den kazınan metinlerde tipik kirlilik.
_INVISIBLE = dict.fromkeys(map(ord, "﻿​‌‍⁠\xa0"), " ")

_WS_RE = re.compile(r"\s+")
_PUNCT_RE = re.compile(r"[^a-z0-9]+")


def clean_text(value: str | None) -> str | None:
    """Görüntülenebilir metni temizler: HTML entity çözer, görünmez karakterleri atar,
    boşlukları tekilleştirir. Büyük/küçük harfe DOKUNMAZ — orijinal yazım korunur.

    HTML entity: Eveshop'un ürün adlarında `&#39;` gibi kaçışlar ham veride duruyor
    (510 satırda doğrulandı). unescape iki kez uygulanır çünkü bazı kaynaklarda çift
    kaçış var (`&amp;#39;` -> `&#39;` -> `'`)."""
    if value is None:
        return None
    text = html.unescape(html.unescape(value))
    text = text.translate(_INVISIBLE)
    # Unicode NFKC: aynı karakterin farklı kodlamalarını (örn. ligatür, tam-genişlik) birleştirir
    text = unicodedata.normalize("NFKC", text)
    return _WS_RE.sub(" ", text).strip() or None


def ascii_fold(value: str) -> str:
    """Latin aksanlarını ASCII karşılıklarına indirger ('Şampuan' -> 'sampuan',
    "L'Oréal" -> "L'Oreal").

    Türkçe tablosu ÖNCE uygulanır çünkü 'ı'/'İ' Unicode ayrıştırmasıyla doğru çözülmez
    ('ı' noktasız i'dir, aksan değil). Kalan aksanlı harfler (é, ñ, ü-Almanca vb.) NFKD ile
    ayrıştırılıp birleştirici işaretler atılır — aksi halde markadaki 'é' noktalama sayılıp
    kelimeyi ikiye böler ('l or al paris' gibi)."""
    folded = value.translate(_TR_ASCII)
    decomposed = unicodedata.normalize("NFKD", folded)
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch))


def normalize_key(value: str | None) -> str | None:
    """Karşılaştırma/eşleştirme için agresif normalizasyon: temizle -> ASCII'ye indir ->
    küçük harf -> noktalama yerine boşluk -> tekille.

    Marka eşleştirmesinde asıl derdi bu çözüyor: 'FLORMAR' (Watsons/Eveshop BÜYÜK HARF)
    ile 'Flormar' (Gratis/Rossmann Title Case) aynı anahtara iner. Türkçe'de str.lower()
    tek başına yetmez ('I' -> 'i' olur ama 'İ' -> 'i̇' birleşik noktalı üretir), bu yüzden
    önce ASCII'ye indiriliyor."""
    cleaned = clean_text(value)
    if cleaned is None:
        return None
    folded = ascii_fold(cleaned).lower()
    return _PUNCT_RE.sub(" ", folded).strip() or None


def slugify(name: str) -> str:
    """Dosya/klasör adında güvenle kullanılabilecek slug: 'Ev & Yaşam' -> 'ev_yasam'."""
    return _PUNCT_RE.sub("_", ascii_fold(name).lower()).strip("_")


# --- Birim/miktar standardizasyonu (CLAUDE.md: "ml/gr birim standardizasyonu") -------------
#
# Kozmetikte aynı ürün sitelerde farklı yazılıyor: "50 ml" / "50ml" / "50 ML" / "0,05 L".
# Fuzzy matching'e girmeden önce sayısal bir (değer, birim) çiftine indirgenmezse
# "X Şampuan 400ml" ile "X Şampuan 200ml" tehlikeli biçimde benzer görünür.
#
# Kanonik birimler: hacim -> ml, ağırlık -> g, sayılabilir -> adet.
_UNIT_TO_CANONICAL = {
    "ml": ("ml", 1.0), "mililitre": ("ml", 1.0),
    "cl": ("ml", 10.0),
    "dl": ("ml", 100.0),
    "l": ("ml", 1000.0), "lt": ("ml", 1000.0), "litre": ("ml", 1000.0),
    "mg": ("g", 0.001),
    "g": ("g", 1.0), "gr": ("g", 1.0), "gram": ("g", 1.0),
    "kg": ("g", 1000.0),
    "adet": ("adet", 1.0), "li": ("adet", 1.0), "lu": ("adet", 1.0),
    "lı": ("adet", 1.0), "lü": ("adet", 1.0),
}

# "2x50 ml" / "50 ml" / "1,5 lt" / "60'lı" / "3 Paket 56 Adet" desenlerini tek geçişte yakalar.
_SIZE_RE = re.compile(
    r"(?:(?P<count>\d+)\s*(?:[x×]|paket|kutu)\s*)?"   # opsiyonel çoklu paket öneki: "2x", "3 Paket"
    r"(?P<value>\d+(?:[.,]\d+)?)"                      # sayı (Türkçe ondalık virgül dahil)
    r"(?P<sep>\s*['’]?\s*)"                            # "60'lı" gibi kesme işareti / boşluk
    r"(?P<unit>ml|mililitre|cl|dl|lt|litre|l|mg|gr|gram|g|kg|adet|li|lu|lı|lü)"
    r"(?![a-zçğıöşü])",                                # birimden sonra harf gelmemeli ("gram" vs "granül")
    re.IGNORECASE,
)


def parse_size(name: str | None) -> tuple[float | None, str | None]:
    """Ürün adından toplam miktarı ve kanonik birimi çıkarır.

    Döner: (değer, birim) — birim 'ml' | 'g' | 'adet', bulunamazsa (None, None).
    Çoklu paket TOPLAM üzerinden hesaplanır: '2x50 ml' -> (100.0, 'ml'), '3 Paket 56 Adet'
    -> (168.0, 'adet'), çünkü fiyat karşılaştırmasında anlamlı olan kutudaki toplam miktardır.

    Birden fazla eşleşme varsa (örn. '50 ml x 2 adet') hacim/ağırlık eşleşmesi 'adet'e
    tercih edilir — asıl ayırt edici olan odur.

    Tek harflik litre birimi SADECE BÜYÜK 'L' ise kabul edilir. Bu kural veriden çıkarıldı
    (37199 farklı ürün adı tarandı): tek harfle yazılmış litrelerin 24'ü de BÜYÜK L
    ("Duş Jeli 1 L", "Çöp Torbası 10L"), küçük 'l' ile yazılmış 3 örneğin ÜÇÜ DE bozuk/kırpık
    ("Sprey 150l" -> aynı ürün diğer 3 sitede "150 ml"; "Tampon 32'l" -> "32'li";
    "Orkid 16'l" -> "16'lı"). Eskiden bunlar litre sayılıp 150000/32000/16000 ml üretiyordu.
    Boşluk değil harf büyüklüğü ayırt edici: "10L" bitişik ama gerçek litre.
    'lt'/'litre' yazımları (43 isim) bu kuraldan etkilenmez."""
    if not name:
        return None, None

    best: tuple[float, str] | None = None
    for match in _SIZE_RE.finditer(ascii_fold(name)):
        unit_key = match.group("unit").lower()
        if unit_key == "l" and match.group("unit") != "L":
            continue  # küçük 'l' -> kırpılmış "li/lı" ya da bozuk veri, litre değil
        canonical, factor = _UNIT_TO_CANONICAL[unit_key]
        try:
            value = float(match.group("value").replace(",", "."))
        except ValueError:
            continue
        count = int(match.group("count")) if match.group("count") else 1
        total = round(value * factor * count, 3)
        if total <= 0:
            continue
        # hacim/ağırlık, 'adet'ten daha ayırt edici -> ilk bulunanı tut ama adet'i ezmesine izin ver
        if best is None or (best[1] == "adet" and canonical != "adet"):
            best = (total, canonical)

    return best if best else (None, None)


# --- GTIN/EAN normalizasyonu ---------------------------------------------------------------
#
# Ham barkodlar siteler arasında farklı biçimlerde geliyor:
#   - EAN-13 (13 hane, standart)
#   - UPC-A (12 hane) — EAN-13'te başına '0' eklenmiş halidir
#   - EAN-8 (8 hane) — kısa ürünler
#   - Gratis'te baştaki sıfırı KIRPILMIŞ değerler (örn. '44386415270' = UPC-A '044386415270')
# Hepsi GTIN-14'e sola sıfır doldurularak indirgenirse aynı ürün aynı anahtara düşer.
_DIGITS_RE = re.compile(r"\D")


def gtin_check_digit(digits: str) -> int:
    """GTIN (EAN-8/12/13/14) mod-10 kontrol hanesini hesaplar. Girdi kontrol hanesi
    HARİÇ olmalıdır. Sağdan sola 3,1,3,1... ağırlıklandırma."""
    total = 0
    for index, char in enumerate(reversed(digits)):
        total += int(char) * (3 if index % 2 == 0 else 1)
    return (10 - total % 10) % 10


def normalize_gtin(raw: str | None) -> str | None:
    """Ham barkodu kanonik GTIN-14'e çevirir; geçerli bir GTIN değilse None.

    Kabul kriterleri:
      - Rakam dışı karakterler atılır (boşluk/tire kirliliği)
      - Anlamlı uzunluk 8-14 hane (daha kısası Watsons'taki 4-7 hanelik iç kod çöpü)
      - Sola sıfır doldurularak 14 haneye tamamlanır
      - Mod-10 kontrol hanesi DOĞRULANIR — Watsons'ın virgülle zincirlediği çöp değerlerin
        ve baştaki sıfırı kırpılmış olanların ayrımı burada yapılır
    Doğrulama başarısızsa None döner; çağıran taraf bunu 'şüpheli barkod' olarak ele alır."""
    if not raw:
        return None
    digits = _DIGITS_RE.sub("", str(raw))
    if not 8 <= len(digits) <= 14:
        return None
    if int(digits) == 0:
        return None
    if gtin_check_digit(digits[:-1]) != int(digits[-1]):
        return None
    return digits.rjust(14, "0")


def normalize_gtin_lenient(raw: str | None) -> str | None:
    """normalize_gtin gibi ama kontrol hanesi TUTMAZSA da değeri döndürür.

    Gratis'te baştaki sıfırı kırpılmış 11 haneli değerler var (264 satır) — bunların bir
    kısmı sıfır eklenince checksum'ı tutuyor, bir kısmı tutmuyor. Katı doğrulama bunları
    tamamen atar; bu gevşek sürüm 'eşleştirmeyi dene ama confidence'ı düşür' senaryosu için.
    Uzunluk kontrolü yine uygulanır (4-7 hanelik çöp elenmeye devam eder)."""
    if not raw:
        return None
    digits = _DIGITS_RE.sub("", str(raw))
    if not 8 <= len(digits) <= 14 or int(digits) == 0:
        return None
    return digits.rjust(14, "0")
