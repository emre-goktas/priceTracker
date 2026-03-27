"""
Centralised settings loaded from environment variables / .env file.
Uses pydantic-settings for automatic validation.
"""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    # Database
    db_url: str = "postgresql+psycopg2://user:password@localhost:5432/pricetracker"

    # Telegram
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""

    # Scheduler
    scrape_interval_minutes: int = 10

    # Analytics
    analytics_strategy: str = "threshold"   # "threshold" | "zscore"
    alert_threshold: float = 0.80           # alert if new_price < avg * this
    z_threshold: float = 2.0

    # Anti-bot
    min_delay_seconds: float = 2.0
    max_delay_seconds: float = 6.0


settings = Settings()
