from __future__ import annotations

import asyncio

import httpx

from shared.logging_config import get_logger

logger = get_logger(__name__)

DEFAULT_TIMEOUT = 15.0
DEFAULT_RETRIES = 3
DEFAULT_BACKOFF_SECONDS = 2.0
DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "tr-TR,tr;q=0.9,en-US;q=0.8,en;q=0.7",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Upgrade-Insecure-Requests": "1",
}


async def fetch(
    url: str,
    *,
    method: str = "GET",
    headers: dict | None = None,
    timeout: float = DEFAULT_TIMEOUT,
    retries: int = DEFAULT_RETRIES,
    backoff_seconds: float = DEFAULT_BACKOFF_SECONDS,
) -> httpx.Response:
    """Tüm modüllerin ortak HTTP giriş noktası. Doğrudan httpx/requests çağrısı yerine bu kullanılır."""
    request_headers = {**DEFAULT_HEADERS, **(headers or {})}

    last_exc: Exception | None = None
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
        for attempt in range(1, retries + 1):
            try:
                response = await client.request(method, url, headers=request_headers)
                if response.status_code >= 500 and attempt < retries:
                    logger.warning(f"{url} -> {response.status_code}, deneme {attempt}/{retries}")
                    await asyncio.sleep(backoff_seconds * attempt)
                    continue
                return response
            except httpx.RequestError as exc:
                last_exc = exc
                logger.warning(f"{url} istek hatası: {exc}, deneme {attempt}/{retries}")
                if attempt < retries:
                    await asyncio.sleep(backoff_seconds * attempt)

    raise last_exc or RuntimeError(f"{url} için bilinmeyen hata")
