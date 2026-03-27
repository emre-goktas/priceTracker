import pytest
from unittest.mock import MagicMock
from analytics.engine import AnalyticsEngine, Anomaly
from storage.models import PriceHistory


def _make_history(prices: list[float]) -> list[PriceHistory]:
    records = []
    for p in prices:
        record = MagicMock(spec=PriceHistory)
        record.price = p
        records.append(record)
    return records


def test_no_anomaly_on_normal_price():
    engine = AnalyticsEngine(strategy="threshold", threshold=0.80)
    history = _make_history([1000.0] * 10)
    result = engine.analyze("p1", 950.0, history)
    assert result is None


def test_anomaly_on_significant_drop():
    engine = AnalyticsEngine(strategy="threshold", threshold=0.80)
    history = _make_history([1000.0] * 10)
    result = engine.analyze("p1", 700.0, history)
    assert isinstance(result, Anomaly)
    assert result.drop_pct == pytest.approx(0.30, abs=0.01)


def test_circuit_breaker_flags_extreme_drop():
    engine = AnalyticsEngine(strategy="threshold", threshold=0.80)
    history = _make_history([1000.0] * 10)
    result = engine.analyze("p1", 100.0, history)
    assert result is not None
    assert result.requires_review is True
