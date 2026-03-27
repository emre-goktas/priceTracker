"""
Base interface for all alert channels.
"""

from abc import ABC, abstractmethod
from analytics.engine import Anomaly


class BaseAlertChannel(ABC):
    @abstractmethod
    def send(self, anomaly: Anomaly, product_name: str, product_url: str) -> bool:
        """Send a price-drop alert. Returns True on success."""
        pass
