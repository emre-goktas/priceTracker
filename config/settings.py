"""
Centralised settings loaded from environment variables / .env file.
Uses pydantic-settings for automatic validation.
"""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    # Database  (SQLite default for dev; swap to postgres+asyncpg in prod)
    db_url: str = "sqlite+aiosqlite:///pricetracker.db"

    # Telegram
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""

    # Scheduler
    scrape_interval_minutes: int = 15

    # Analytics
    analytics_strategy: str = "threshold"   # "threshold" | "zscore"
    alert_threshold: float = 0.80           # alert if new_price < avg * this
    z_threshold: float = 2.0

    # Anti-bot delays (seconds)
    min_delay_seconds: float = 2.0
    max_delay_seconds: float = 6.0

    # Site Base URLs (Homepages)
    vatan_base_url: str = "https://www.vatanbilgisayar.com"
    amazon_base_url: str = "https://www.amazon.com.tr"
    teknosa_base_url: str = "https://www.teknosa.com"
    hepsiburada_base_url: str = "https://www.hepsiburada.com"
    mediamarkt_base_url: str = "https://www.mediamarkt.com.tr"
    
    vatan_urls: list[str] = [] # Kept for backward compatibility/overrides
    hepsiburada_urls: list[str] = []
    mediamarkt_urls: list[str] = []

settings = Settings()
