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

# User-Agent, Accept-Encoding, Sec-Fetch-* gibi taraf tutan header'lar BİLEREK burada YOK:
# impersonate= parametresi bunları seçilen tarayıcı profiliyle (TLS/JA3 fingerprint dahil)
# tutarlı şekilde otomatik ayarlıyor. Elle üzerine yazmak fingerprint/header uyumsuzluğu
# yaratıp tam da kaçınmaya çalıştığımız bot tespitini tetikleyebilir.
DEFAULT_HEADERS = {
    "Accept-Language": "tr-TR,tr;q=0.9,en-US;q=0.8,en;q=0.7",
}


async def fetch(
    url: str,
    *,
    method: str = "GET",
    headers: dict | None = None,
    timeout: float = DEFAULT_TIMEOUT,
    retries: int = DEFAULT_RETRIES,
    backoff_seconds: float = DEFAULT_BACKOFF_SECONDS,
    impersonate: str = DEFAULT_IMPERSONATE,
) -> Response:
    """Tüm modüllerin ortak HTTP giriş noktası. Doğrudan curl_cffi/httpx/requests çağrısı
    yerine bu kullanılır. curl_cffi ile gerçek bir tarayıcının TLS/JA3 fingerprint'i taklit
    edilir (impersonate) - retry + backoff burada yönetilir."""
    request_headers = {**DEFAULT_HEADERS, **(headers or {})}

    last_exc: Exception | None = None
    async with AsyncSession() as session:
        for attempt in range(1, retries + 1):
            try:
                response = await session.request(
                    method,
                    url,
                    headers=request_headers,
                    timeout=timeout,
                    impersonate=impersonate,
                )
                if response.status_code >= 500 and attempt < retries:
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
