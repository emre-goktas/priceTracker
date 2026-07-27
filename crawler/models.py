from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Site:
    name: str
    base_url: str
    country: str
    enabled: bool


@dataclass
class RobotsResult:
    http_status: int
    raw_text: str
    sitemap_urls: list[str]
    disallow_patterns: list[str]
    allow_patterns: list[str]
