"""
Telegram alert channel using Bot API.
"""

import requests

from alerts.base import BaseAlertChannel
from analytics.engine import Anomaly


class TelegramAlerter(BaseAlertChannel):
    def __init__(self, bot_token: str, chat_id: str):
        self.bot_token = bot_token
        self.chat_id = chat_id
        self.api_url = f"https://api.telegram.org/bot{bot_token}/sendMessage"

    def _format_message(self, anomaly: Anomaly, product_name: str, product_url: str) -> str:
        review_warning = "\n⚠️ *Requires manual review* (>80% drop detected)" if anomaly.requires_review else ""
        return (
            f"🔥 *Price Drop Alert!*\n\n"
            f"📦 *{product_name}*\n"
            f"💰 Old avg: `{anomaly.old_avg:.2f} TL`\n"
            f"💥 New price: `{anomaly.new_price:.2f} TL`\n"
            f"📉 Drop: `{anomaly.drop_pct * 100:.1f}%`\n"
            f"🔗 [View Product]({product_url})"
            f"{review_warning}"
        )

    def send(self, anomaly: Anomaly, product_name: str, product_url: str) -> bool:
        message = self._format_message(anomaly, product_name, product_url)
        resp = requests.post(
            self.api_url,
            json={
                "chat_id": self.chat_id,
                "text": message,
                "parse_mode": "Markdown",
                "disable_web_page_preview": False,
            },
            timeout=10,
        )
        return resp.ok
