from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

from investment_engine.market_data import AKShareProvider, Bar, MarketDataError, MarketDataService, MarketDataStore, completed_daily_bars, validate_bars


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


def test_sync_refreshes_from_oldest_cached_adjusted_bar(tmp_path):
    store = MarketDataStore(tmp_path)
    first = date(2026, 1, 5)
    store.save([make_bar(first, 10)])
    class Provider:
        def __init__(self):
            self.start_date = None
        def fetch(self, **kwargs):
            self.start_date = kwargs["start_date"]
            return [make_bar(first, 11), make_bar(first + timedelta(days=1), 12)]
    provider = Provider()
    result = MarketDataService(store=store, provider=provider).sync(
        symbol="601118", asset_type="stock", start_date="20260106",
        end_date="20260106", adjustment="qfq",
    )
    assert provider.start_date == "2026-01-05"
    assert result["effective_refresh_start_date"] == "2026-01-05"
    assert store.load("601118", "stock", "qfq")[0].close == 11


def test_store_refuses_future_rows_when_as_of_is_supplied(tmp_path):
    store = MarketDataStore(tmp_path)
    future = make_bar(date(2027, 1, 1))
    with pytest.raises(MarketDataError):
        store.save([future], as_of=date(2026, 11, 5))


def test_intraday_as_of_excludes_unfinished_same_day_bar():
    previous = make_bar(date(2026, 11, 4), 10)
    current = make_bar(date(2026, 11, 5), 11)
    before_close = datetime(2026, 11, 5, 2, 0, tzinfo=timezone.utc)  # 10:00 Shanghai
    after_close = datetime(2026, 11, 5, 7, 5, tzinfo=timezone.utc)  # 15:05 Shanghai
    assert [x.timestamp for x in completed_daily_bars([previous, current], as_of=before_close)] == [
        "2026-11-04"
    ]
    assert [x.timestamp for x in completed_daily_bars([previous, current], as_of=after_close)] == [
        "2026-11-04", "2026-11-05"
    ]


def test_completed_daily_bars_requires_timezone():
    with pytest.raises(MarketDataError):
        completed_daily_bars([], as_of=datetime(2026, 11, 5, 16, 0))


class FakeFrame:
    def __init__(self, rows):
        self.rows = rows
        self.empty = not rows
    def to_dict(self, orient):
        assert orient == "records"
        return self.rows


def test_akshare_stock_adapter_normalizes_eastmoney_fields():
    class FakeAK:
        def stock_zh_a_hist(self, **kwargs):
            assert kwargs["symbol"] == "601118"
            assert kwargs["adjust"] == "qfq"
            return FakeFrame([{
                "日期": "2026-11-04", "开盘": 7.2, "最高": 7.4, "最低": 7.1,
                "收盘": 7.3, "成交量": 1234, "成交额": 900000,
            }])
    provider = AKShareProvider.__new__(AKShareProvider)
    provider.ak = FakeAK()
    bars = provider.fetch(
        symbol="601118", asset_type="stock", start_date="20261101",
        end_date="20261105", adjustment="qfq",
    )
    assert len(bars) == 1
    assert bars[0].close == 7.3
    assert bars[0].source == "akshare.eastmoney.stock_zh_a_hist"
    assert bars[0].volume_unit == "lots"


def test_akshare_futures_adapter_uses_continuous_contract_and_date_filter():
    class FakeAK:
        def futures_zh_daily_sina(self, **kwargs):
            assert kwargs == {"symbol": "RU0"}
            return FakeFrame([
                {"date": "2026-11-04", "open": 16000, "high": 16100,
                 "low": 15900, "close": 16050, "volume": 12345},
                {"date": "2026-11-06", "open": 16100, "high": 16200,
                 "low": 16000, "close": 16150, "volume": 23456},
            ])
    provider = AKShareProvider.__new__(AKShareProvider)
    provider.ak = FakeAK()
    bars = provider.fetch(
        symbol="RU0", asset_type="future", start_date="20261101",
        end_date="20261105", adjustment="continuous",
    )
    assert len(bars) == 1
    assert bars[0].timestamp == "2026-11-04"
    assert bars[0].adjustment == "continuous"
    assert bars[0].exchange == "SHFE"
    assert bars[0].volume_unit == "contracts"


def test_known_ru0_upstream_ohlc_outlier_is_quarantined_exactly():
    class FakeAK:
        def futures_zh_daily_sina(self, **kwargs):
            assert kwargs["symbol"] == "RU0"
            return FakeFrame([
                {"date": "2023-08-25", "open": 13205, "high": 13340,
                 "low": 13190, "close": 13270, "volume": 194862, "settle": 0},
                {"date": "2023-08-28", "open": 13270, "high": 13425,
                 "low": 13225, "close": 13200, "volume": 269600, "settle": 0},
                {"date": "2023-08-29", "open": 13205, "high": 13270,
                 "low": 13120, "close": 13180, "volume": 179572, "settle": 0},
            ])
    provider = AKShareProvider.__new__(AKShareProvider)
    provider.ak = FakeAK()
    bars = provider.fetch(
        symbol="RU0", asset_type="future", start_date="20230801",
        end_date="20230830", adjustment="continuous"
    )
    assert [x.timestamp for x in bars] == ["2023-08-25", "2023-08-29"]
    assert len(provider.quarantine_records) == 1
    row = provider.quarantine_records[0]
    assert row["date"] == "2023-08-28"
    assert row["close"] == 13200
    assert row["settle"] == 0
    assert "issues/3" in row["review"]


def test_unknown_ohlc_outlier_still_rejected():
    class FakeAK:
        def futures_zh_daily_sina(self, **kwargs):
            return FakeFrame([
                {"date": "2023-08-28", "open": 13270, "high": 13425,
                 "low": 13225, "close": 13199, "volume": 269600, "settle": 0},
            ])
    provider = AKShareProvider.__new__(AKShareProvider)
    provider.ak = FakeAK()
    with pytest.raises(MarketDataError, match="inconsistent OHLC"):
        provider.fetch(symbol="RU0", asset_type="future",
                       start_date="20230801", end_date="20230830",
                       adjustment="continuous")


def test_quarantine_sidecar_retains_raw_exclusion_and_series_flag(tmp_path):
    class FakeAK:
        def futures_zh_daily_sina(self, **kwargs):
            return FakeFrame([
                {"date": "2023-08-25", "open": 13205, "high": 13340,
                 "low": 13190, "close": 13270, "volume": 194862, "settle": 0},
                {"date": "2023-08-28", "open": 13270, "high": 13425,
                 "low": 13225, "close": 13200, "volume": 269600, "settle": 0},
                {"date": "2023-08-29", "open": 13205, "high": 13270,
                 "low": 13120, "close": 13180, "volume": 179572, "settle": 0},
            ])
    provider = AKShareProvider.__new__(AKShareProvider)
    provider.ak = FakeAK()
    store = MarketDataStore(tmp_path)
    result = MarketDataService(store=store, provider=provider).sync(
        symbol="RU0", asset_type="future", start_date="20230801",
        end_date="20230830", adjustment="continuous",
    )
    assert result["source_record_status"] == "partial_quarantined"
    assert result["quarantined_count"] == 1
    assert result["quality"]["valid"] is True
    assert result["quality"]["warning_count"] > 0
    assert store.read_quarantine("RU0", "future", "continuous")["excluded_rows"][0]["date"] == "2023-08-28"
    assert store.list_series()[0]["excluded_rows"] == 1
    assert len(store.load("RU0", "future", "continuous")) == 2
