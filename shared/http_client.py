from __future__ import annotations

import asyncio

from curl_cffi.requests import AsyncSession, Response
from curl_cffi.requests.exceptions import RequestException

from shared.logging_config import get_logger

logger = get_logger(__name__)

DEFAULT_TIMEOUT = 15.0
DEFAULT_RETRIES = 3
DEFAULT_BACKOFF_SECONDS = 2.0
DEFAULT_IMPERSONATE = "chrome120"

# 429 (Too Many Requests) da retry edilir - rate-limit tipik bir "az bekle, tekrar dene"
# durumudur, 5xx sunucu hatalarından ayrı değerlendirilmemeli.
RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}

# User-Agent, Accept-Encoding, Sec-Fetch-* gibi taraf tutan header'lar BİLEREK burada YOK:
# impersonate= parametresi bunları seçilen tarayıcı profiliyle (TLS/JA3 fingerprint dahil)
# tutarlı şekilde otomatik ayarlıyor. Elle üzerine yazmak fingerprint/header uyumsuzluğu
# yaratıp tam da kaçınmaya çalıştığımız bot tespitini tetikleyebilir.
DEFAULT_HEADERS = {
    "Accept-Language": "tr-TR,tr;q=0.9,en-US;q=0.8,en;q=0.7",
}

# Tüm fetch() çağrıları TEK bir AsyncSession'ı paylaşır (connection pooling/keep-alive).
# Her istekte yeni session açmak TCP/TLS handshake'ini tekrarlatır - hem belirgin şekilde
# yavaşlatır hem de (binlerce istekte) socket tükenmesine yol açabilir.
_session: AsyncSession | None = None
_session_lock = asyncio.Lock()


async def _get_session() -> AsyncSession:
    global _session
    if _session is None:
        async with _session_lock:
            if _session is None:
                _session = AsyncSession()
    return _session


async def close() -> None:
    """Process/script sonunda açık kalan paylaşılan session'ı kapatır."""
    global _session
    if _session is not None:
        await _session.close()
        _session = None


async def fetch(
    url: str,
    *,
    method: str = "GET",
    headers: dict | None = None,
    params: dict | None = None,
    json: dict | list | None = None,
    data: dict | str | bytes | None = None,
    timeout: float = DEFAULT_TIMEOUT,
    retries: int = DEFAULT_RETRIES,
    backoff_seconds: float = DEFAULT_BACKOFF_SECONDS,
    impersonate: str = DEFAULT_IMPERSONATE,
) -> Response:
    """Tüm modüllerin ortak HTTP giriş noktası. Doğrudan curl_cffi/httpx/requests çağrısı
    yerine bu kullanılır. curl_cffi ile gerçek bir tarayıcının TLS/JA3 fingerprint'i taklit
    edilir (impersonate) - retry + backoff burada yönetilir. `json`/`data` POST body'si
    gönderen istekler (örn. self-heal, ileride POST API'ler) için desteklenir."""
    request_headers = {**DEFAULT_HEADERS, **(headers or {})}
    session = await _get_session()

    last_exc: Exception | None = None
    for attempt in range(1, retries + 1):
        try:
            response = await session.request(
                method,
                url,
                params=params,
                json=json,
                data=data,
                headers=request_headers,
                timeout=timeout,
                impersonate=impersonate,
            )
            if response.status_code in RETRYABLE_STATUS_CODES and attempt < retries:
                logger.warning(f"{url} -> {response.status_code}, deneme {attempt}/{retries}")
                await asyncio.sleep(backoff_seconds * attempt)
                continue
            return response
        except RequestException as exc:
            last_exc = exc
            logger.warning(f"{url} istek hatası: {exc}, deneme {attempt}/{retries}")
            if attempt < retries:
                await asyncio.sleep(backoff_seconds * attempt)

    raise last_exc or RuntimeError(f"{url} için bilinmeyen hata")
