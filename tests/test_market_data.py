from datetime import date, timedelta
from pathlib import Path

import pytest

from investment_engine.market_data import Bar, MarketDataError, MarketDataStore, validate_bars


def make_bar(day, close=10.0, **kwargs):
    return Bar(
        symbol="601118", exchange="SSE", asset_type="stock", interval="1d",
        timestamp=day.isoformat(), open=close, high=close + 0.5, low=close - 0.5,
        close=close, volume=1000, amount=10000, source="test.fixture",
        fetched_at="2026-11-05T00:00:00+00:00", adjustment="qfq",
        **kwargs,
    )


def test_valid_bars_and_calendar_gap_warning():
    start = date(2026, 1, 5)
    bars = [make_bar(start, 10), make_bar(start + timedelta(days=1), 10.2),
            make_bar(start + timedelta(days=7), 10.4)]
    report = validate_bars(bars, as_of=date(2026, 11, 5))
    assert report["valid"] is True
    assert report["warning_count"] == 1


def test_duplicate_and_future_bars_are_rejected():
    start = date(2026, 1, 5)
    assert not validate_bars([make_bar(start), make_bar(start)])["valid"]
    assert not validate_bars([make_bar(date(2027, 1, 1))], as_of=date(2026, 11, 5))["valid"]


def test_bad_ohlc_is_rejected():
    bar = make_bar(date(2026, 1, 5))
    invalid = Bar(**{**bar.__dict__, "high": 9.0})
    assert not validate_bars([invalid])["valid"]


def test_store_upserts_by_timestamp(tmp_path):
    store = MarketDataStore(tmp_path)
    start = date(2026, 1, 5)
    store.save([make_bar(start, 10), make_bar(start + timedelta(days=1), 11)])
    store.save([make_bar(start + timedelta(days=1), 12), make_bar(start + timedelta(days=2), 13)])
    rows = store.load("601118", "stock", "qfq")
    assert len(rows) == 3
    assert rows[1].close == 12
    assert store.list_series()[0]["bar_count"] == 3


def test_store_rejects_invalid_symbol(tmp_path):
    store = MarketDataStore(tmp_path)
    with pytest.raises(MarketDataError):
        store.load("../secret", "stock", "qfq")
