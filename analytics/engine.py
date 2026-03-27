"""
Analytics Engine – detects significant price anomalies.

Two strategies (configurable):
  - threshold : simple % drop rule  (new_price < avg * threshold)
  - zscore    : statistical z-score  (z < -z_threshold)
"""

import statistics
from dataclasses import dataclass

from storage.models import PriceHistory


@dataclass
class Anomaly:
    product_id: str
    new_price: float
    old_avg: float
    drop_pct: float           # e.g. 0.25 → 25% drop
    requires_review: bool     # True if drop > 80% (circuit breaker)


CIRCUIT_BREAKER_THRESHOLD = 0.20  # price dropped to less than 20% of previous


class AnalyticsEngine:
    def __init__(self, strategy: str = "threshold", threshold: float = 0.80, z_threshold: float = 2.0):
        """
        strategy  : "threshold" or "zscore"
        threshold : alert if new_price < avg_price * threshold  (default: 0.80 → 20% drop)
        z_threshold : alert if z-score < -z_threshold
        """
        self.strategy = strategy
        self.threshold = threshold
        self.z_threshold = z_threshold

    def analyze(self, product_id: str, new_price: float, history: list[PriceHistory]) -> Anomaly | None:
        if not history:
            return None

        prices = [h.price for h in history]
        avg = statistics.mean(prices)

        if avg == 0:
            return None

        drop_pct = (avg - new_price) / avg

        # Circuit breaker: suspiciously large drop → flag for manual review
        requires_review = new_price < avg * CIRCUIT_BREAKER_THRESHOLD

        is_anomaly = False
        if self.strategy == "threshold":
            is_anomaly = new_price < avg * self.threshold
        elif self.strategy == "zscore" and len(prices) >= 2:
            std = statistics.stdev(prices)
            if std > 0:
                z = (new_price - avg) / std
                is_anomaly = z < -self.z_threshold

        if is_anomaly:
            return Anomaly(
                product_id=product_id,
                new_price=new_price,
                old_avg=avg,
                drop_pct=drop_pct,
                requires_review=requires_review,
            )
        return None
