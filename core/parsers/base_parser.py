from __future__ import annotations

import asyncio
import base64
import json
from collections.abc import AsyncIterator
from xml.etree import ElementTree

from shared.http_client import fetch

# Bu modül site adı bilmez: sadece configs/tr/{site}.yaml'da tanımlı ayarları uygular.
#
# NOT: Kategori ID'leri artık configs/tr/{site}.yaml -> main_categories'te statik duruyor
# (veritabanı/API key'i, kolay değişmez). Bu dosyada daha önce bulunan canlı kategori-keşif
# stratejileri (dynamic_api/own_sitemap_regex/category_page_scrape) kaldırıldı — hiçbir yerden
# çağrılmıyorlardı. configs/tr/{site}.yaml'daki category_discovery blokları referans/geçmiş
# kaydı olarak duruyor; ileride bir kategori-listeleme endpoint'i selfheal katmanında
# değerlendirilmek istenirse oradan yeniden inşa edilir.


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

    Rate-limit ayarları (bekleme + retry) configs/tr/{site}.yaml -> rate_limit'ten okunur,
    kodda hardcode edilmez: art arda hızlı istekler Gratis'te WAF tarafından 403 ile
    bloklanabiliyor (ampirik olarak gözlemlendi), bir sayfa bloklanırsa/hata alırsa vazgeçmeden
    önce birkaç kez yeniden denenir.
    """
    api_cfg = config["category_search_api"]
    response_cfg = api_cfg["response"]
    pagination_cfg = api_cfg.get("pagination", {})
    page_size = pagination_cfg.get("page_size", 60)
    page_param = pagination_cfg.get("page_param")

    rate_cfg = config.get("rate_limit", {})
    delay_seconds = rate_cfg.get("delay_seconds", 1.5)
    max_retries = rate_cfg.get("max_retries", 3)
    retry_backoff_seconds = rate_cfg.get("retry_backoff_seconds", 2.0)

    page = 0
    offset = 0
    while True:
        if page > 0:
            await asyncio.sleep(delay_seconds)

        url, params = build_category_request(config, category_id, offset=offset, limit=page_size, page=page)

        response = None
        for attempt in range(1, max_retries + 1):
            response = await fetch(url, params=params, headers=config.get("base_headers", {}))
            if response.status_code == 200 or attempt == max_retries:
                break
            await asyncio.sleep(retry_backoff_seconds * attempt)

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
