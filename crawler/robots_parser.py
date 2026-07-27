from __future__ import annotations

from crawler.models import RobotsResult
from shared.http_client import fetch
from shared.logging_config import get_logger

logger = get_logger(__name__)


def parse_robots_txt(raw_text: str) -> tuple[list[str], list[str], list[str]]:
    sitemap_urls: list[str] = []
    disallow_patterns: list[str] = []
    allow_patterns: list[str] = []

    for line in raw_text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or ":" not in line:
            continue

        directive, _, value = line.partition(":")
        directive = directive.strip().lower()
        value = value.strip()

        if directive == "sitemap" and value:
            sitemap_urls.append(value)
        elif directive == "disallow" and value:
            disallow_patterns.append(value)
        elif directive == "allow" and value:
            allow_patterns.append(value)

    return sitemap_urls, disallow_patterns, allow_patterns


async def fetch_robots_txt(base_url: str) -> RobotsResult:
    url = base_url.rstrip("/") + "/robots.txt"
    response = await fetch(url)
    sitemap_urls, disallow_patterns, allow_patterns = parse_robots_txt(response.text)

    logger.info(
        f"{url} -> {response.status_code} | {len(sitemap_urls)} sitemap, "
        f"{len(disallow_patterns)} disallow, {len(allow_patterns)} allow"
    )

    return RobotsResult(
        http_status=response.status_code,
        raw_text=response.text,
        sitemap_urls=sitemap_urls,
        disallow_patterns=disallow_patterns,
        allow_patterns=allow_patterns,
    )
