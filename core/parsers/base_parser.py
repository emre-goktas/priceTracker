from __future__ import annotations

import base64
import json
import re
from collections import defaultdict
from collections.abc import AsyncIterator
from xml.etree import ElementTree

from core.db import get_discovered_urls
from shared.http_client import fetch
from shared.logging_config import get_logger

logger = get_logger(__name__)

# Bu modül site adı bilmez: sadece configs/tr/{site}.yaml'da tanımlı stratejileri uygular.
# Yeni bir strateji gerekirse buraya eklenir; site_plugins/{site}.py'da literal regex/ID olmaz.


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


def _localname(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _xml_element_to_obj(element: ElementTree.Element) -> object:
    """Basit bir XML elemanını dict/list yapısına çevirir (attribute yok sayılır).
    Aynı etikete sahip birden fazla kardeş varsa liste olur."""
    children = list(element)
    if not children:
        return element.text.strip() if element.text else None

    result: dict[str, object] = {}
    for child in children:
        tag = _localname(child.tag)
        value = _xml_element_to_obj(child)
        if tag in result:
            if not isinstance(result[tag], list):
                result[tag] = [result[tag]]
            result[tag].append(value)
        else:
            result[tag] = value
    return result


def parse_response_body(content: bytes, format_hint: str) -> dict:
    if format_hint == "xml":
        return _xml_element_to_obj(ElementTree.fromstring(content))
    if format_hint == "json_or_xml":
        if content.lstrip()[:1] == b"<":
            return _xml_element_to_obj(ElementTree.fromstring(content))
        return json.loads(content)
    return json.loads(content)


def _as_list(value: object) -> list:
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def _substitute(obj: object, values: dict) -> object:
    """payload_template/params içindeki '{category_id}' gibi placeholder'ları değerlerle değiştirir."""
    if isinstance(obj, str):
        for key, val in values.items():
            token = "{" + key + "}"
            if obj == token:
                return val
            if token in obj:
                obj = obj.replace(token, str(val))
        return obj
    if isinstance(obj, dict):
        return {k: _substitute(v, values) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_substitute(v, values) for v in obj]
    return obj


def build_category_request(config: dict, category_id: str, offset: int = 0, limit: int = 60, page: int = 0) -> tuple[str, dict]:
    api_cfg = config["category_search_api"]
    url = api_cfg["url"]
    # category_id/category_code aynı değerin farklı sitelerdeki isimlendirmesi (Watsons kendi
    # API'sinde "categoryCode" terimini kullanıyor) -> ikisi de placeholder olarak desteklenir.
    values = {
        "category_id": category_id,
        "category_code": category_id,
        "offset": offset,
        "limit": limit,
        "page": page,
    }

    if api_cfg.get("request_style") == "base64_json_payload":
        payload = _substitute(api_cfg["payload_template"], values)
        b64 = base64.b64encode(json.dumps(payload).encode()).decode()
        params = dict(api_cfg.get("extra_params", {}))
        params.update({"data": b64, "__isbase64": "true"})
        return url, params

    params = _substitute(api_cfg.get("params", {}), values)
    return url, params


async def fetch_category_pages(config: dict, category_id: str) -> AsyncIterator[tuple[int, int, bytes]]:
    """Bir kategorinin tüm sayfalarını sırayla çeker. Her sayfa için
    (sayfa_no, http_status, ham_bytes) üretir. Sayfa numarası bazlı (Watsons) ve offset
    bazlı (Gratis/Rossmann) sayfalamayı config'teki 'pagination' alanına göre ayırt eder.
    """
    api_cfg = config["category_search_api"]
    response_cfg = api_cfg["response"]
    pagination_cfg = api_cfg.get("pagination", {})
    page_size = pagination_cfg.get("page_size", 60)
    page_param = pagination_cfg.get("page_param")

    page = 0
    offset = 0
    while True:
        url, params = build_category_request(config, category_id, offset=offset, limit=page_size, page=page)
        response = await fetch(url, params=params, headers=config.get("base_headers", {}))

        yield page, response.status_code, response.content

        if response.status_code != 200:
            break

        data = parse_response_body(response.content, response_cfg.get("format", "json"))
        items = _as_list(resolve_path(data, response_cfg["items_path"]))

        if page_param:
            total_pages = int(resolve_path(data, response_cfg["total_pages_path"]) or 1)
            page += 1
            if page >= total_pages or not items:
                break
        else:
            total = int(resolve_path(data, response_cfg["total_count_path"]) or 0)
            offset += page_size
            page += 1
            if offset >= total or not items:
                break


# --- Kategori keşfi stratejileri ---


async def discover_categories(site_name: str, config: dict, base_url: str) -> list[str]:
    strategy = config["category_discovery"]["strategy"]
    if strategy == "dynamic_api":
        return await _discover_dynamic_api(config)
    if strategy == "own_sitemap_regex":
        return await _discover_own_sitemap_regex(site_name, config, base_url)
    if strategy == "category_page_scrape":
        return await _discover_category_page_scrape(site_name, config, base_url)
    raise ValueError(f"Bilinmeyen category_discovery stratejisi: {strategy}")


async def _discover_dynamic_api(config: dict) -> list[str]:
    disc = config["category_discovery"]
    response = await fetch(disc["url"], params=disc.get("extra_params", {}), headers=config.get("base_headers", {}))
    data = response.json()
    container = resolve_path(data, disc["response"]["category_ids_path"]) or {}
    exclude = set(disc["response"].get("exclude_keys", []))
    return sorted(k for k in container.keys() if k not in exclude)


async def _discover_own_sitemap_regex(site_name: str, config: dict, base_url: str) -> list[str]:
    """Sitemap URL'lerinden kategori kodu çıkarır.

    Ana kategori (categoryCode) ile sorgulamak zaten TÜM alt-ağacın ürünlerini kapsıyor
    (anchor-category davranışı, ampirik olarak doğrulandı — bkz. watsons.yaml). Bu yüzden
    'main_category_rule' varsa SADECE gerçek ana kategoriler döndürülür (derinlik + alt-URL
    sayısı sinyaliyle kampanya/marka landing page'lerinden ayıklanmış); yoksa (kural tanımlı
    değilse) tüm kodlar döner.
    """
    disc = config["category_discovery"]
    urls = await get_discovered_urls(site_name, source_contains=disc["source_sitemap_contains"])

    main_rule = disc.get("main_category_rule")
    if not main_rule:
        pattern = re.compile(disc["url_regex"])
        codes: set[str] = set()
        for url in urls:
            match = pattern.search(url)
            if match:
                codes.add(match.group(1))
        return sorted(codes)

    target_depth = main_rule.get("depth", 1)
    min_children = main_rule.get("min_child_urls", 1)
    base_prefix = base_url.rstrip("/") + "/"
    slug_pattern = re.compile(rf"^{re.escape(base_prefix)}([a-z0-9\-]+(?:/[a-z0-9\-]+)*)/c/([0-9_]+)$")

    parsed: list[tuple[str, str, int]] = []
    for url in urls:
        match = slug_pattern.match(url)
        if not match:
            continue
        slug_path, code = match.groups()
        depth = slug_path.count("/") + 1
        parsed.append((slug_path, code, depth))

    main_codes: set[str] = set()
    for slug, code, depth in parsed:
        if depth != target_depth:
            continue
        prefix = slug + "/"
        child_count = sum(1 for s, _, d in parsed if d > target_depth and s.startswith(prefix))
        if child_count >= min_children:
            main_codes.add(code)

    return sorted(main_codes)


async def _discover_category_page_scrape(site_name: str, config: dict, base_url: str) -> list[str]:
    disc = config["category_discovery"]
    root_cfg = disc["root_slug_source"]
    exclude_patterns = root_cfg.get("exclude_patterns", [])
    min_child_paths = root_cfg.get("min_child_paths", 1)

    urls = await get_discovered_urls(site_name)
    base_prefix = base_url.rstrip("/") + "/"

    slugs = []
    for url in urls:
        if any(p in url for p in exclude_patterns):
            continue
        if not url.startswith(base_prefix):
            continue
        path = url[len(base_prefix):].strip("/")
        if path:
            slugs.append(path)

    child_counts: dict[str, int] = defaultdict(int)
    for path in slugs:
        if "/" in path:
            child_counts[path.split("/")[0]] += 1

    root_slugs = [slug for slug, count in child_counts.items() if count >= min_child_paths]

    own_id_regex = re.compile(disc["extraction"]["own_id_regex"])

    # NOT: sadece KÖK kategori ID'si toplanır, çocukları ayrıca eklenmez. Ampirik olarak
    # doğrulandı (kisisel-bakim id=4, total=3109 — tek başına 3 çocuğu bile 1851'i buluyor):
    # ana kategoriyle sorgulamak zaten tüm alt-ağacın ürünlerini kapsıyor (anchor-category
    # davranışı). Çocukları da ayrıca taramak devasa bir redundant crawl yaratırdı.
    category_ids: set[str] = set()
    for slug in root_slugs:
        page_url = f"{base_prefix}{slug}"
        response = await fetch(page_url, headers=config.get("base_headers", {}))
        if response.status_code != 200:
            logger.warning(f"{site_name}: kök kategori sayfası çekilemedi {page_url} -> {response.status_code}")
            continue

        match = own_id_regex.search(response.text)
        if match:
            category_ids.add(match.group(1))
        else:
            logger.warning(f"{site_name}: {slug} sayfasında own_id_regex eşleşmedi")

    return sorted(category_ids)
