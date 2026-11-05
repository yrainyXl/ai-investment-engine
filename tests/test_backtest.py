from datetime import date, timedelta

import pytest

from investment_engine.backtest import BacktestError, run_backtest
from investment_engine.market_data import Bar


def bars(n=140):
    start = date(2025, 1, 1)
    rows = []
    for i in range(n):
        close = 10 + i * 0.05
        rows.append(Bar(
            symbol="601118", exchange="SSE", asset_type="stock", interval="1d",
            timestamp=(start + timedelta(days=i)).isoformat(),
            open=close - 0.02, high=close + 0.1, low=close - 0.1, close=close,
            volume=1000, amount=10000, source="test.fixture",
            fetched_at="2026-11-05T00:00:00+00:00", adjustment="qfq",
        ))
    return rows


def test_sma_backtest_uses_next_session_open():
    rows = bars()
    result = run_backtest(rows, strategy="sma_trend", fast_period=5, slow_period=20)
    assert result["status"] == "completed"
    assert result["lookahead_bias_control"].startswith("signal shifted")
    assert result["first_entry_execution_date"] > rows[19].timestamp
    assert result["eligible_for_trading"] is False
    assert "max_drawdown" in result["metrics"]


def test_buy_and_hold_benchmark_is_available():
    result = run_backtest(bars(), strategy="buy_and_hold")
    assert result["metrics"]["exposure"] == 1.0
    assert result["benchmark"]["metrics"]["exposure"] == 1.0


def test_backtest_rejects_insufficient_history():
    with pytest.raises(BacktestError):
        run_backtest(bars(20), strategy="sma_trend", fast_period=5, slow_period=20)


def test_costs_reduce_trend_strategy_return():
    rows = bars()
    no_cost = run_backtest(rows, strategy="sma_trend", fast_period=5, slow_period=20,
                           fee_bps=0, slippage_bps=0)
    with_cost = run_backtest(rows, strategy="sma_trend", fast_period=5, slow_period=20,
                             fee_bps=20, slippage_bps=10)
    assert with_cost["metrics"]["total_return"] <= no_cost["metrics"]["total_return"]
