from __future__ import annotations

import asyncio
import base64
import json
from collections.abc import AsyncIterator
from dataclasses import dataclass
from xml.etree import ElementTree

from core.parsers import html_parser
from shared.http_client import fetch
from shared.jsonpath import resolve_path
from shared.logging_config import get_logger

logger = get_logger(__name__)

# Sayfalama için mutlak üst sınır. Normalde hiç devreye girmez (en büyük kategori, Gratis
# 501/Makyaj, 96 sayfa) — bir sitenin sayfa parametresini yok sayıp aynı dolu sayfayı
# tekrar tekrar döndürmesi durumunda (Shopify tipi temalarda görülen davranış) sonsuz
# döngüyü ve sonsuz MinIO yazımını keser. configs/tr/{site}.yaml -> pagination.max_pages
# ile site bazında değiştirilebilir.
DEFAULT_MAX_PAGES = 500

# Bu modül site adı bilmez: sadece configs/tr/{site}.yaml'da tanımlı ayarları uygular.
#
# NOT: Kategori ID'leri artık configs/tr/{site}.yaml -> main_categories'te statik duruyor
# (veritabanı/API key'i, kolay değişmez). Bu dosyada daha önce bulunan canlı kategori-keşif
# stratejileri (dynamic_api/own_sitemap_regex/category_page_scrape) kaldırıldı — hiçbir yerden
# çağrılmıyorlardı. configs/tr/{site}.yaml'daki category_discovery blokları referans/geçmiş
# kaydı olarak duruyor; ileride bir kategori-listeleme endpoint'i selfheal katmanında
# değerlendirilmek istenirse oradan yeniden inşa edilir.


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


def parse_response_body(content: bytes, response_cfg: dict) -> object:
    format_hint = response_cfg.get("format", "json")
    if format_hint == "xml":
        return _xml_element_to_obj(ElementTree.fromstring(content))
    if format_hint == "json_or_xml":
        if content.lstrip()[:1] == b"<":
            return _xml_element_to_obj(ElementTree.fromstring(content))
        return json.loads(content)
    if format_hint == "html_regex_json":
        # HTML sayfasına gömülü, regex ile yakalanabilen JSON blokları çıkarır (örn. Eveshop'un
        # x-labels-data attribute'u) - her eşleşme kendi içinde tek/çoklu öğeli bir liste ya da
        # tek bir obje olabilir, extract_all_json_blocks ikisini de düz bir listeye açar. Bu
        # dönen liste doğrudan "items" olarak kullanılır (bkz. fetch_category_pages) - ayrı bir
        # items_path'e gerek yok.
        text = content.decode("utf-8", errors="replace")
        return html_parser.extract_all_json_blocks(text, response_cfg["extract_regex"])
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
    # category_id/category_code aynı değerin farklı sitelerdeki isimlendirmesi (Watsons kendi
    # API'sinde "categoryCode" terimini kullanıyor) -> ikisi de placeholder olarak desteklenir.
    values = {
        "category_id": category_id,
        "category_code": category_id,
        "offset": offset,
        "limit": limit,
        "page": page,
    }
    # url'in kendisi de placeholder içerebilir (örn. Eveshop: ".../collections/{category_id}") -
    # mevcut siteler url'lerinde '{...}' token kullanmıyor, bu yüzden no-op, geriye dönük uyumlu.
    url = _substitute(api_cfg["url"], values)

    if api_cfg.get("request_style") == "base64_json_payload":
        payload = _substitute(api_cfg["payload_template"], values)
        b64 = base64.b64encode(json.dumps(payload).encode()).decode()
        params = dict(api_cfg.get("extra_params", {}))
        params.update({"data": b64, "__isbase64": "true"})
        return url, params

    params = _substitute(api_cfg.get("params", {}), values)
    return url, params


@dataclass
class CategoryPage:
    """Bir kategori sayfasının çekim sonucu.

    stop_reason SADECE son sayfada doludur ve çağıranın "bu kategori eksiksiz mi çekildi"
    sorusuna cevap verebilmesi içindir — eskiden bu bilgi hiç dışarı çıkmıyordu, bu yüzden
    WAF interstitial'ı gibi HTTP 200 dönen ama ürün içermeyen bir yanıt "kategori bitti"
    sanılıp sessizce başarı olarak loglanabiliyordu.
    """

    page: int
    http_status: int
    content: bytes
    item_count: int = 0
    declared_total: int | None = None   # API'nin bildirdiği toplam ürün sayısı (varsa)
    stop_reason: str | None = None


# stop_reason değerleri:
#   "declared_total_reached" -> API'nin bildirdiği toplama ulaşıldı (sağlıklı bitiş)
#   "total_pages_reached"    -> API'nin bildirdiği sayfa sayısına ulaşıldı (sağlıklı bitiş)
#   "empty_page"             -> boş sayfa geldi; toplam bildirilmiyorsa DOĞRULANAMAZ bitiş
#   "http_error"             -> retry'ler tükendi, sayfa alınamadı (EKSİK)
#   "max_pages"              -> güvenlik sınırına dayandı (anormal, incelenmeli)
HEALTHY_STOP_REASONS = frozenset({"declared_total_reached", "total_pages_reached"})


def _read_pagination_settings(config: dict) -> dict:
    """rate_limit/pagination ayarlarını okur ve DOĞRULAR.

    Config'ten gelen değerler eskiden hiç kontrol edilmiyordu: örneğin max_retries: 0
    yazıldığında retry döngüsü hiç dönmüyor, `response` None kalıyor ve bir sonraki satır
    AttributeError ile patlıyordu. Self-heal katmanı ileride bu dosyaları LLM ile
    güncelleyeceği için mantıksız değerlerin sessizce üretime sızmaması kritik."""
    api_cfg = config["category_search_api"]
    pagination_cfg = api_cfg.get("pagination", {})
    rate_cfg = config.get("rate_limit", {})

    settings = {
        "page_size": int(pagination_cfg.get("page_size", 60)),
        "page_param": pagination_cfg.get("page_param"),
        # zero_indexed=false olan siteler (Eveshop/Shopify: ?page=1'den başlar) için sayfa
        # değeri istek anında +1 kayar; döngünün kendi 'page' sayacı (0'dan başlar, sayfa
        # SAYISINI tutar) bundan etkilenmez.
        "zero_indexed": pagination_cfg.get("zero_indexed", True),
        "max_pages": int(pagination_cfg.get("max_pages", DEFAULT_MAX_PAGES)),
        "delay_seconds": float(rate_cfg.get("delay_seconds", 1.5)),
        "max_retries": int(rate_cfg.get("max_retries", 3)),
        "retry_backoff_seconds": float(rate_cfg.get("retry_backoff_seconds", 2.0)),
    }

    if settings["page_size"] < 1:
        raise ValueError(f"pagination.page_size >= 1 olmalı, config'te: {settings['page_size']}")
    if settings["max_pages"] < 1:
        raise ValueError(f"pagination.max_pages >= 1 olmalı, config'te: {settings['max_pages']}")
    if settings["max_retries"] < 1:
        raise ValueError(f"rate_limit.max_retries >= 1 olmalı, config'te: {settings['max_retries']}")
    if settings["delay_seconds"] < 0 or settings["retry_backoff_seconds"] < 0:
        raise ValueError("rate_limit gecikmeleri negatif olamaz")

    return settings


async def fetch_category_pages(config: dict, category_id: str) -> AsyncIterator[CategoryPage]:
    """Bir kategorinin tüm sayfalarını sırayla çeker, her sayfa için bir CategoryPage üretir.
    Sayfa numarası bazlı (Watsons/Eveshop) ve offset bazlı (Gratis/Rossmann) sayfalamayı
    config'teki 'pagination' alanına göre ayırt eder.

    Rate-limit ayarları (bekleme + retry) configs/tr/{site}.yaml -> rate_limit'ten okunur,
    kodda hardcode edilmez: art arda hızlı istekler Gratis'te WAF tarafından 403 ile
    bloklanabiliyor (ampirik olarak gözlemlendi), bir sayfa bloklanırsa/hata alırsa vazgeçmeden
    önce birkaç kez yeniden denenir.

    Durma nedeni (stop_reason) son sayfada raporlanır — "kategori bitti" ile "bir şey ters
    gitti, erken kesildik" ayrımı çağırana bırakılmadan burada yapılır.
    """
    api_cfg = config["category_search_api"]
    response_cfg = api_cfg["response"]
    cfg = _read_pagination_settings(config)

    page = 0
    offset = 0
    declared_total: int | None = None

    while True:
        if page > 0:
            await asyncio.sleep(cfg["delay_seconds"])

        request_page = page if cfg["zero_indexed"] else page + 1
        url, params = build_category_request(
            config, category_id, offset=offset, limit=cfg["page_size"], page=request_page
        )

        for attempt in range(1, cfg["max_retries"] + 1):
            response = await fetch(url, params=params, headers=config.get("base_headers", {}))
            if response.status_code == 200 or attempt == cfg["max_retries"]:
                break
            await asyncio.sleep(cfg["retry_backoff_seconds"] * attempt)

        if response.status_code != 200:
            yield CategoryPage(page, response.status_code, response.content,
                               declared_total=declared_total, stop_reason="http_error")
            return

        data = parse_response_body(response.content, response_cfg)
        items_path = response_cfg.get("items_path")
        items = _as_list(resolve_path(data, items_path)) if items_path else _as_list(data)
        item_count = len(items)

        if cfg["page_param"]:
            # total_pages_path opsiyonel: bazı siteler (Eveshop gibi HTML kategori sayfaları)
            # toplam sayfa sayısı bildirmiyor - o durumda tek durma sinyali "bu sayfada ürün
            # kalmadı" (stop-on-empty), ki bu bitişin doğrulanamaz olduğu anlamına gelir.
            total_pages_path = response_cfg.get("total_pages_path")
            total_pages = int(resolve_path(data, total_pages_path) or 1) if total_pages_path else None
            total_count_path = response_cfg.get("total_count_path")
            if total_count_path:
                reported = resolve_path(data, total_count_path)
                if reported is not None:
                    declared_total = int(reported)
            page += 1
            if total_pages is not None and page >= total_pages:
                stop_reason = "total_pages_reached"
            elif not items:
                stop_reason = "empty_page"
            elif page >= cfg["max_pages"]:
                stop_reason = "max_pages"
            else:
                stop_reason = None
        else:
            declared_total = int(resolve_path(data, response_cfg["total_count_path"]) or 0)
            offset += cfg["page_size"]
            page += 1
            if offset >= declared_total:
                stop_reason = "declared_total_reached"
            elif not items:
                stop_reason = "empty_page"
            elif page >= cfg["max_pages"]:
                stop_reason = "max_pages"
            else:
                stop_reason = None

        yield CategoryPage(page - 1, response.status_code, response.content,
                           item_count=item_count, declared_total=declared_total,
                           stop_reason=stop_reason)

        if stop_reason is not None:
            if stop_reason == "max_pages":
                logger.error(
                    f"kategori {category_id}: {cfg['max_pages']} sayfa güvenlik sınırına dayanıldı - "
                    "site sayfalama parametresini yok sayıyor olabilir, config gözden geçirilmeli"
                )
            return
