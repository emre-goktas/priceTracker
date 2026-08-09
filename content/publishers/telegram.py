from __future__ import annotations

import os

from dotenv import load_dotenv

from shared.http_client import fetch
from shared.logging_config import get_logger

load_dotenv()
logger = get_logger(__name__)

# Telegram sendMessage tek mesajda en fazla 4096 karakter kabul ediyor.
MAX_MESSAGE_LENGTH = 4096
# sendPhoto'nun caption'ı için ayrı, daha kısa bir sınır (4096 değil).
MAX_CAPTION_LENGTH = 1024


async def send_photo(photo_url: str, caption: str) -> None:
    """Telegram sendPhoto - photo bir URL string olarak kabul ediliyor, indirip yeniden
    yüklemeye gerek yok (ürün CDN'leri genel erişime açık)."""
    token = os.environ["TELEGRAM_BOT_TOKEN"]
    chat_id = os.environ["TELEGRAM_CHAT_ID"]
    url = f"https://api.telegram.org/bot{token}/sendPhoto"

    response = await fetch(
        url,
        method="POST",
        json={
            "chat_id": chat_id,
            "photo": photo_url,
            "caption": caption[:MAX_CAPTION_LENGTH],
            "parse_mode": "HTML",
        },
    )
    if response.status_code != 200:
        logger.error(f"Telegram foto gönderimi başarısız: {response.status_code} {response.text}")
        response.raise_for_status()


async def send_message(text: str) -> None:
    token = os.environ["TELEGRAM_BOT_TOKEN"]
    chat_id = os.environ["TELEGRAM_CHAT_ID"]
    url = f"https://api.telegram.org/bot{token}/sendMessage"

    for i in range(0, len(text), MAX_MESSAGE_LENGTH):
        chunk = text[i : i + MAX_MESSAGE_LENGTH]
        response = await fetch(
            url,
            method="POST",
            json={
                "chat_id": chat_id,
                "text": chunk,
                "disable_web_page_preview": True,
                "parse_mode": "HTML",
            },
        )
        if response.status_code != 200:
            logger.error(f"Telegram gönderimi başarısız: {response.status_code} {response.text}")
            response.raise_for_status()
