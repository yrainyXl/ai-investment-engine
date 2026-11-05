"""Small, transparent daily-bar backtester with next-session execution.

Signals use completed closes and positions become effective at the next bar's open.
No intraday execution, shorting, leverage or live orders are implemented.
"""
from __future__ import annotations

import math
from datetime import date
from statistics import mean, pstdev
from typing import Any, Sequence

from .market_data import Bar, validate_bars


class BacktestError(ValueError):
    pass


def _metrics(returns: list[float], positions: list[int], turnover: list[float], *,
             fee_bps: float, slippage_bps: float, elapsed_bars: int) -> dict[str, Any]:
    if not returns:
        raise BacktestError("Not enough bars to calculate returns")
    equity = 1.0
    curve = [equity]
    for value in returns:
        equity *= max(0.0, 1.0 + value)
        curve.append(equity)
    peak = curve[0]
    max_drawdown = 0.0
    for value in curve:
        peak = max(peak, value)
        if peak > 0:
            max_drawdown = min(max_drawdown, value / peak - 1.0)
    daily_vol = pstdev(returns) if len(returns) > 1 else 0.0
    avg_return = mean(returns)
    annual_vol = daily_vol * math.sqrt(252)
    sharpe = (avg_return / daily_vol * math.sqrt(252)) if daily_vol > 0 else None
    years = len(returns) / 252
    cagr = (equity ** (1 / years) - 1) if years > 0 and equity > 0 else None
    return {
        "total_return": round(equity - 1.0, 6),
        "cagr": round(cagr, 6) if cagr is not None else None,
        "annualized_volatility": round(annual_vol, 6),
        "sharpe_ratio": round(sharpe, 4) if sharpe is not None else None,
        "max_drawdown": round(max_drawdown, 6),
        "positive_day_ratio": round(sum(1 for x in returns if x > 0) / len(returns), 4),
        "exposure": round(sum(positions) / len(positions), 4) if positions else 0.0,
        "turnover_units": round(sum(turnover), 4),
        "entry_count": sum(1 for i, p in enumerate(positions) if p == 1 and (i == 0 or positions[i - 1] == 0)),
        "observations": len(returns),
        "fee_bps_per_side": fee_bps,
        "slippage_bps_per_side": slippage_bps,
    }


def run_backtest(bars: Sequence[Bar], *, strategy: str = "sma_trend", fast_period: int = 20,
                 slow_period: int = 60, fee_bps: float = 10.0,
                 slippage_bps: float = 5.0, as_of: date | None = None) -> dict[str, Any]:
    rows = list(bars)
    if strategy not in {"sma_trend", "buy_and_hold"}:
        raise BacktestError("strategy must be sma_trend or buy_and_hold")
    if fee_bps < 0 or slippage_bps < 0 or fee_bps > 500 or slippage_bps > 500:
        raise BacktestError("fee/slippage bps must be between 0 and 500")
    if fast_period < 2 or slow_period <= fast_period:
        raise BacktestError("Require 2 <= fast_period < slow_period")
    quality = validate_bars(rows, as_of=as_of)
    if not quality["valid"]:
        raise BacktestError("Invalid bars: " + "; ".join(quality["errors"][:3]))
    minimum = slow_period + 2 if strategy == "sma_trend" else 3
    if len(rows) < minimum:
        raise BacktestError(f"Need at least {minimum} bars for {strategy}")
    opens = [float(row.open) for row in rows]
    closes = [float(row.close) for row in rows]

    if strategy == "buy_and_hold":
        close_signals = [1] * len(rows)
    else:
        close_signals = [0] * len(rows)
        for i in range(slow_period - 1, len(rows)):
            fast = mean(closes[i - fast_period + 1:i + 1])
            slow = mean(closes[i - slow_period + 1:i + 1])
            # Signal is known only after close i; execute at next session's open.
            close_signals[i] = int(closes[i] > fast and fast > slow)

    # Return i is open[i] -> open[i+1]; position[i] was decided at close[i-1].
    gross_returns = [opens[i + 1] / opens[i] - 1.0 for i in range(len(opens) - 1)]
    if strategy == "buy_and_hold":
        positions = [1] * len(gross_returns)
    else:
        positions = ([0] + close_signals[:-1])[:len(gross_returns)]
    cost_rate = (fee_bps + slippage_bps) / 10000.0
    turnover: list[float] = []
    net_returns: list[float] = []
    previous_position = 0
    for position, gross in zip(positions, gross_returns):
        traded = abs(position - previous_position)
        turnover.append(float(traded))
        net_returns.append(position * gross - traded * cost_rate)
        previous_position = position

    benchmark_positions = [1] * len(gross_returns)
    benchmark_turnover = [1.0] + [0.0] * (len(gross_returns) - 1)
    benchmark_returns = [
        gross - turnover_value * cost_rate
        for gross, turnover_value in zip(gross_returns, benchmark_turnover)
    ]
    return {
        "status": "completed",
        "strategy": strategy,
        "signal": "close-based SMA fast/slow; long-only; no leverage",
        "execution_model": "signal after close[t], enter/exit at open[t+1], measure open-to-open returns",
        "lookahead_bias_control": "signal shifted by one session; no same-bar close execution",
        "first_entry_execution_date": next((rows[i].timestamp for i, p in enumerate(positions) if p == 1), None),
        "data": {
            "symbol": rows[0].symbol, "asset_type": rows[0].asset_type,
            "source": rows[0].source, "adjustment": rows[0].adjustment,
            "bar_count": len(rows), "first_bar": rows[0].timestamp, "last_bar": rows[-1].timestamp,
            "currency": rows[0].currency, "volume_unit": rows[0].volume_unit,
        },
        "assumptions": [
            "Daily OHLCV only; orders are modeled at next session open, not actual exchange fills.",
            "Fee and slippage are configurable flat basis points per side.",
            "No taxes, limit-up/down queueing, suspension handling, market impact, dividends cashflows or survivorship adjustment.",
            "For qfq/hfq bars, reported returns follow the provider's adjusted price series; this is not an executable historical fill series.",
            "Buy-and-hold benchmark is charged one entry cost and is held through the final open.",
        ],
        "metrics": _metrics(net_returns, positions, turnover, fee_bps=fee_bps,
                            slippage_bps=slippage_bps, elapsed_bars=len(rows)),
        "benchmark": {
            "name": "buy_and_hold",
            "metrics": _metrics(benchmark_returns, benchmark_positions, benchmark_turnover,
                                fee_bps=fee_bps, slippage_bps=slippage_bps, elapsed_bars=len(rows)),
        },
        "excess_return_vs_benchmark": round(
            math.prod(1 + x for x in net_returns) - math.prod(1 + x for x in benchmark_returns), 6
        ),
        "eligible_for_trading": False,
        "disclaimer": "Research backtest only. Not a prediction, recommendation or authorization to trade.",
    }
