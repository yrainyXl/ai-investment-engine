"""Tests for the M4 strategy registry and IS/OOS experiment coordinator.

Fixtures are synthetic and explicitly labelled as such. Two different asset
classes are covered on purpose: a mainland A-share (601118) and an exchange
traded fund (510300), which stand in for two unrelated industries/exposures.
"""
from __future__ import annotations

import dataclasses
import inspect
import json
import math
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

import investment_engine.strategy_experiments as experiments
from investment_engine.contracts import StrategyRegistry
from investment_engine.market_data import Bar, MarketDataStore
from investment_engine.strategy_experiments import (
    BACKTEST_GAPS,
    ExperimentReport,
    ExperimentSpec,
    StrategyExperimentError,
    StrategyExperimentRegistry,
    StrategySpec,
    WalkForwardConfig,
    compare_experiments,
    expected_dataset_id,
    run_experiment,
)

AS_OF = datetime(2026, 11, 12, 10, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
#: Pinned evaluation clock so the suite never depends on the wall clock.
NOW = datetime(2026, 12, 1, 0, 0, tzinfo=timezone.utc)
IS_WINDOW = (date(2025, 1, 1), date(2025, 8, 31))
OOS_WINDOW = (date(2025, 9, 1), date(2025, 12, 31))
FIXTURE_SOURCE = "test.fixture.ohlcv.v1"


def run(spec: ExperimentSpec, bars, *, now: datetime = NOW) -> ExperimentReport:
    """Run an experiment against a fixed clock for deterministic tests."""
    return run_experiment(spec, bars, now=now)


def make_bars(
    *,
    symbol: str = "601118",
    exchange: str = "SSE",
    asset_type: str = "stock",
    adjustment: str = "qfq",
    start: date = date(2025, 1, 1),
    count: int = 360,
    base: float = 10.0,
    drift: float = 0.02,
    wave: float = 0.45,
    source: str = FIXTURE_SOURCE,
    fetched_at: str = "2026-11-12T00:00:00+00:00",
) -> list[Bar]:
    rows: list[Bar] = []
    for index in range(count):
        close = round(base + drift * index + wave * math.sin(index / 5.0), 4)
        open_price = round(close - 0.02, 4)
        rows.append(
            Bar(
                symbol=symbol,
                exchange=exchange,
                asset_type=asset_type,
                interval="1d",
                timestamp=(start + timedelta(days=index)).isoformat(),
                open=open_price,
                high=round(max(open_price, close) + 0.08, 4),
                low=round(min(open_price, close) - 0.08, 4),
                close=close,
                volume=float(1000 + index),
                amount=float(10000 + index),
                source=source,
                fetched_at=fetched_at,
                adjustment=adjustment,
                currency="CNY",
                volume_unit="lots",
            )
        )
    return rows


def stock_spec(**overrides) -> StrategySpec:
    payload = {
        "strategy_id": "sma_trend_cn_stock",
        "strategy_version": "1.0.0",
        "name": "SMA trend baseline (A-share)",
        "asset_class": "stock",
        "parameters": {"fast_period": 5, "slow_period": 20},
        "dataset_id": expected_dataset_id("601118", "stock", "qfq"),
        "risk_policy_id": "risk_long_only_v1",
        "portfolio_id": "portfolio_cn_stock_research",
        "implementation": "sma_trend",
        "parameter_schema": {
            "fast_period": {"type": "int", "required": True, "minimum": 2},
            "slow_period": {"type": "int", "required": True, "minimum": 3},
        },
        "description": "Deterministic rule baseline reused from the v0.6 backtester.",
    }
    payload.update(overrides)
    return StrategySpec(**payload)


def etf_spec(**overrides) -> StrategySpec:
    payload = {
        "strategy_id": "sma_trend_cn_etf",
        "strategy_version": "1.0.0",
        "name": "SMA trend baseline (broad ETF)",
        "asset_class": "etf",
        "parameters": {"fast_period": 10, "slow_period": 30},
        "dataset_id": expected_dataset_id("510300", "etf", "qfq"),
        "risk_policy_id": "risk_long_only_etf_v1",
        "portfolio_id": "portfolio_cn_etf_research",
        "implementation": "sma_trend",
        "parameter_schema": {
            "fast_period": {"type": "int", "required": True, "minimum": 2},
            "slow_period": {"type": "int", "required": True, "minimum": 3},
        },
    }
    payload.update(overrides)
    return StrategySpec(**payload)


def stock_experiment(**overrides) -> ExperimentSpec:
    payload = {
        "experiment_id": "exp_sma_stock_is_oos_v1",
        "strategy": stock_spec(),
        "symbol": "601118",
        "asset_class": "stock",
        "adjustment": "qfq",
        "interval": "1d",
        "is_start": IS_WINDOW[0],
        "is_end": IS_WINDOW[1],
        "oos_start": OOS_WINDOW[0],
        "oos_end": OOS_WINDOW[1],
        "as_of": AS_OF,
        "fee_bps": 10.0,
        "slippage_bps": 5.0,
        "notes": "Pre-registered baseline; no parameter search.",
    }
    payload.update(overrides)
    return ExperimentSpec(**payload)


def etf_experiment(**overrides) -> ExperimentSpec:
    payload = {
        "experiment_id": "exp_sma_etf_is_oos_v1",
        "strategy": etf_spec(),
        "symbol": "510300",
        "asset_class": "etf",
        "adjustment": "qfq",
        "interval": "1d",
        "is_start": IS_WINDOW[0],
        "is_end": IS_WINDOW[1],
        "oos_start": OOS_WINDOW[0],
        "oos_end": OOS_WINDOW[1],
        "as_of": AS_OF,
        "fee_bps": 5.0,
        "slippage_bps": 2.0,
    }
    payload.update(overrides)
    return ExperimentSpec(**payload)


def etf_bars(**overrides) -> list[Bar]:
    payload = {
        "symbol": "510300",
        "exchange": "SSE",
        "asset_type": "etf",
        "adjustment": "qfq",
        "base": 3.8,
        "drift": 0.01,
        "wave": 0.25,
    }
    payload.update(overrides)
    return make_bars(**payload)


# --------------------------------------------------------------------------- #
# registry and strategy contract
# --------------------------------------------------------------------------- #
def test_strategy_defaults_to_disabled_and_round_trips_shared_contract():
    spec = stock_spec()
    assert spec.enabled is False
    assert spec.to_dict()["enabled"] is False
    spec.validate()

    # The shared v0.6 metadata contract still accepts the adapted definition.
    shared = StrategyRegistry()
    shared.register(spec.as_definition())
    registered = shared.get("sma_trend_cn_stock")
    assert registered.version == "1.0.0"
    assert registered.enabled is False
    assert registered.asset_classes == ("stock",)
    assert registered.implementation.endswith("#sma_trend")

    registry = StrategyExperimentRegistry([spec])
    assert registry.list()[0]["enabled"] is False
    assert registry.get("sma_trend_cn_stock", "1.0.0").parameters["slow_period"] == 20
    # Nothing is enabled, so the enabled-only projection is empty.
    assert registry.export_enabled().list() == []


def test_strategy_cannot_be_enabled_from_the_research_layer():
    with pytest.raises(StrategyExperimentError) as info:
        stock_spec(enabled=True)
    assert info.value.code == "activation_not_supported"


def test_strategy_parameters_are_frozen_copies():
    parameters = {"fast_period": 5, "slow_period": 20}
    spec = stock_spec(parameters=parameters)
    parameters["slow_period"] = 999
    assert spec.parameters["slow_period"] == 20

    exported = spec.to_dict()
    exported["parameters"]["slow_period"] = 1
    assert spec.parameters["slow_period"] == 20

    with pytest.raises(dataclasses.FrozenInstanceError):
        spec.parameters = {"fast_period": 1, "slow_period": 2}  # type: ignore[misc]


def test_unknown_and_missing_parameters_fail_closed():
    with pytest.raises(StrategyExperimentError) as missing:
        stock_spec(parameters={"fast_period": 5})
    assert missing.value.code == "missing_parameters"

    with pytest.raises(StrategyExperimentError) as unknown:
        stock_spec(parameters={"fast_period": 5, "slow_period": 20, "magic": 7})
    assert unknown.value.code == "unknown_parameters"

    with pytest.raises(StrategyExperimentError) as ordering:
        stock_spec(parameters={"fast_period": 30, "slow_period": 20})
    assert ordering.value.code == "invalid_parameters"


def test_registry_enforces_portfolio_isolation_but_allows_new_versions():
    registry = StrategyExperimentRegistry([stock_spec()])
    with pytest.raises(StrategyExperimentError) as conflict:
        registry.register(etf_spec(portfolio_id="portfolio_cn_stock_research"))
    assert conflict.value.code == "portfolio_isolation_violation"
    assert registry.portfolios() == {"portfolio_cn_stock_research": "sma_trend_cn_stock"}

    registry.register(stock_spec(strategy_version="1.1.0"))
    assert registry.versions("sma_trend_cn_stock") == ["1.0.0", "1.1.0"]

    with pytest.raises(StrategyExperimentError) as duplicate:
        registry.register(stock_spec())
    assert duplicate.value.code == "duplicate_strategy"

    with pytest.raises(StrategyExperimentError) as unknown:
        registry.get("sma_trend_cn_stock", "9.9.9")
    assert unknown.value.code == "unknown_strategy"


# --------------------------------------------------------------------------- #
# happy paths: two different asset classes
# --------------------------------------------------------------------------- #
def test_stock_experiment_completes_with_is_oos_and_benchmark():
    report = run(stock_experiment(), make_bars())
    assert report.status == "completed"
    assert report.is_evaluable is True

    body = report.to_dict()
    assert body["asset_class"] == "stock"
    assert body["symbol"] == "601118"
    assert body["dataset_id"] == "stock:601118:qfq:1d"
    assert body["data"]["source"] == FIXTURE_SOURCE
    assert body["data"]["adjustment"] == "qfq"
    assert body["data"]["price_frequency"] == "1d"
    assert body["data"]["is_bar_count"] > 0 and body["data"]["oos_bar_count"] > 0
    assert body["is_window"]["start"] == "2025-01-01"
    assert body["oos_window"]["start"] == "2025-09-01"
    assert len(body["data"]["dataset_fingerprint"]) == 64

    for block in ("is_metrics", "oos_metrics", "is_benchmark_metrics", "oos_benchmark_metrics"):
        assert set(experiments._METRIC_FIELDS).issubset(body[block])
    assert body["excess_return_oos"] is not None
    assert body["costs"]["total_bps_per_side"] == 15.0
    assert body["optimization_performed"] is False
    assert body["parameter_provenance"] == "pre_registered"


def test_etf_experiment_completes_for_a_different_asset_class():
    report = run(etf_experiment(), etf_bars())
    assert report.status == "completed"
    body = report.to_dict()
    assert body["asset_class"] == "etf"
    assert body["symbol"] == "510300"
    assert body["dataset_id"] == "etf:510300:qfq:1d"
    assert body["data"]["exchange"] == "SSE"
    assert body["oos_metrics"]["observations"] > 0
    assert body["oos_benchmark_metrics"]["exposure"] == 1.0
    assert body["eligible_for_trading"] is False
    assert body["orders_enabled"] is False
    assert body["action"] == "no_trade"
    assert body["experimental_only"] is True


# --------------------------------------------------------------------------- #
# point-in-time and window guards
# --------------------------------------------------------------------------- #
def test_future_bars_are_rejected_rather_than_truncated():
    rows = make_bars()
    rows.append(
        Bar(
            symbol="601118", exchange="SSE", asset_type="stock", interval="1d",
            timestamp="2027-01-04", open=11.0, high=11.2, low=10.9, close=11.1,
            volume=1000.0, amount=11000.0, source=FIXTURE_SOURCE,
            fetched_at="2026-11-12T00:00:00+00:00", adjustment="qfq",
        )
    )
    with pytest.raises(StrategyExperimentError) as info:
        run(stock_experiment(), rows)
    assert info.value.code == "lookahead_future_bars"
    assert "2027-01-04" in str(info.value)


def test_is_and_oos_windows_may_not_overlap():
    with pytest.raises(StrategyExperimentError) as info:
        stock_experiment(oos_start=date(2025, 8, 31))
    assert info.value.code == "is_oos_overlap"

    with pytest.raises(StrategyExperimentError) as reversed_window:
        stock_experiment(is_start=date(2025, 9, 1), is_end=date(2025, 1, 1))
    assert reversed_window.value.code == "invalid_is_window"


def test_insufficient_out_of_sample_sample_is_not_evaluable():
    report = run(
        stock_experiment(oos_start=date(2025, 9, 1), oos_end=date(2025, 9, 5)),
        make_bars(),
    )
    assert report.status == "not_evaluable"
    assert report.is_evaluable is False
    assert report.not_evaluable_reasons[0].startswith("insufficient_oos_bars")
    assert report.oos_metrics is None
    body = report.to_dict()
    assert body["tradability"] == "not_evaluable"
    assert body["eligible_for_trading"] is False
    assert body["orders_enabled"] is False
    assert body["action"] == "no_trade"
    assert len(body["reproducibility_key"]) == 64


def test_window_outside_the_dataset_is_not_evaluable():
    report = run(
        stock_experiment(
            is_start=date(2024, 1, 1), is_end=date(2024, 6, 30),
            oos_start=date(2024, 7, 1), oos_end=date(2024, 12, 31),
        ),
        make_bars(),
    )
    assert report.status == "not_evaluable"
    assert any(reason.startswith("insufficient_is_bars") for reason in report.not_evaluable_reasons)


# --------------------------------------------------------------------------- #
# dataset / provenance guards
# --------------------------------------------------------------------------- #
def test_dataset_id_must_describe_the_supplied_data():
    spec = stock_spec(dataset_id="stock:510300:qfq:1d")
    with pytest.raises(StrategyExperimentError) as info:
        run(stock_experiment(strategy=spec), make_bars())
    assert info.value.code == "dataset_id_mismatch"


def test_asset_class_mismatch_is_rejected():
    with pytest.raises(StrategyExperimentError) as info:
        run(stock_experiment(), etf_bars())
    assert info.value.code in {"dataset_symbol_mismatch", "dataset_asset_class_mismatch"}


def test_mixed_and_missing_provenance_fail_closed():
    rows = make_bars()
    rows[10] = dataclasses.replace(rows[10], source="other.vendor.feed")
    with pytest.raises(StrategyExperimentError) as mixed:
        run(stock_experiment(), rows)
    assert mixed.value.code == "mixed_provenance"

    blanked = make_bars()
    blanked[3] = dataclasses.replace(blanked[3], source="  ")
    with pytest.raises(StrategyExperimentError) as missing:
        run(stock_experiment(), blanked)
    assert missing.value.code == "missing_provenance"

    no_fetch = make_bars()
    no_fetch[3] = dataclasses.replace(no_fetch[3], fetched_at="")
    with pytest.raises(StrategyExperimentError) as no_timestamp:
        run(stock_experiment(), no_fetch)
    assert no_timestamp.value.code == "missing_provenance"


def test_adjustment_mismatch_is_rejected():
    rows = make_bars(adjustment="hfq")
    with pytest.raises(StrategyExperimentError) as info:
        run(stock_experiment(), rows)
    assert info.value.code == "dataset_adjustment_mismatch"


# --------------------------------------------------------------------------- #
# no optimisation, no implicit defaults
# --------------------------------------------------------------------------- #
def test_parameter_search_is_not_supported():
    with pytest.raises(StrategyExperimentError) as info:
        stock_experiment(parameter_provenance="tuned_on_oos")
    assert info.value.code == "parameter_search_not_supported"


def test_costs_and_windows_have_no_implicit_defaults():
    with pytest.raises(TypeError):
        ExperimentSpec(
            experiment_id="exp_missing_costs",
            strategy=stock_spec(),
            symbol="601118",
            asset_class="stock",
            adjustment="qfq",
            interval="1d",
            is_start=IS_WINDOW[0],
            is_end=IS_WINDOW[1],
            oos_start=OOS_WINDOW[0],
            oos_end=OOS_WINDOW[1],
            as_of=AS_OF,
        )
    with pytest.raises(StrategyExperimentError) as negative:
        stock_experiment(fee_bps=-1.0)
    assert negative.value.code == "invalid_fee_bps"
    with pytest.raises(StrategyExperimentError) as intraday:
        stock_experiment(interval="1h")
    assert intraday.value.code == "invalid_interval"


def test_naive_as_of_is_rejected():
    with pytest.raises(StrategyExperimentError) as info:
        stock_experiment(as_of=datetime(2026, 11, 12, 10, 0))
    assert info.value.code == "invalid_as_of"


def test_future_as_of_is_rejected_at_run_time():
    with pytest.raises(StrategyExperimentError) as info:
        run(
            stock_experiment(as_of=datetime(2027, 1, 5, tzinfo=timezone.utc)),
            make_bars(),
        )
    assert info.value.code == "future_as_of"


# --------------------------------------------------------------------------- #
# reproducibility, costs, walk-forward, isolation
# --------------------------------------------------------------------------- #
def test_repeated_experiment_is_byte_identical():
    spec = stock_experiment()
    rows = make_bars()
    first = run(spec, rows)
    second = run(spec, make_bars())
    assert first.to_dict() == second.to_dict()
    assert first.to_json() == second.to_json()
    assert first.reproducibility_key == second.reproducibility_key


def test_changed_costs_change_results_and_identity():
    rows = make_bars()
    cheap = run(stock_experiment(fee_bps=0.0, slippage_bps=0.0), rows)
    costly = run(stock_experiment(fee_bps=30.0, slippage_bps=10.0), rows)

    assert costly.status == cheap.status == "completed"
    assert costly.oos_metrics["total_return"] <= cheap.oos_metrics["total_return"]
    assert costly.costs["total_bps_per_side"] == 40.0
    assert costly.oos_metrics["fee_bps_per_side"] == 30.0
    assert costly.reproducibility_key != cheap.reproducibility_key


def test_walk_forward_produces_declared_folds_only():
    report = run(
        stock_experiment(experiment_id="exp_sma_stock_wf_v1",
                         walk_forward=WalkForwardConfig(train_bars=40, test_bars=24)),
        make_bars(),
    )
    assert report.status == "completed"
    walk = report.to_dict()["walk_forward"]
    assert walk["fold_count"] >= 2
    assert walk["completed_fold_count"] == walk["fold_count"]
    assert walk["config"] == {"train_bars": 40, "test_bars": 24, "step_bars": 24}
    assert walk["ratio_metrics_aggregation"] == "not_performed"
    assert walk["aggregate_total_return"] is not None
    assert walk["worst_fold_max_drawdown"] is not None
    # Every fold's test window is strictly after its train window.
    for fold in walk["folds"]:
        assert fold["test_window"]["start"] > fold["train_window"]["end"]
        # run_backtest counts open-to-open returns, so N bars give N-1 observations.
        assert fold["test_metrics"]["observations"] == 24 - 1


def test_walk_forward_with_no_room_is_not_evaluable():
    report = run(
        stock_experiment(walk_forward=WalkForwardConfig(train_bars=120, test_bars=90)),
        make_bars(),
    )
    assert report.status == "not_evaluable"
    assert "insufficient_walk_forward_folds" in report.not_evaluable_reasons


def test_walk_forward_geometry_shorter_than_the_strategy_is_rejected():
    with pytest.raises(StrategyExperimentError) as too_short:
        stock_experiment(walk_forward=WalkForwardConfig(train_bars=40, test_bars=5))
    assert too_short.value.code == "walk_forward_window_too_short"


def test_two_strategies_stay_isolated():
    registry = StrategyExperimentRegistry([stock_spec(), etf_spec()])
    stock_report = run(stock_experiment(), make_bars())
    etf_report = run(etf_experiment(), etf_bars())

    assert stock_report.portfolio_id != etf_report.portfolio_id
    assert stock_report.reproducibility_key != etf_report.reproducibility_key
    assert stock_report.parameters["slow_period"] == 20
    assert etf_report.parameters["slow_period"] == 30
    # Running one experiment never mutates the other strategy's registration.
    assert registry.get("sma_trend_cn_stock", "1.0.0").parameters == {
        "fast_period": 5, "slow_period": 20,
    }
    assert registry.get("sma_trend_cn_etf", "1.0.0").parameters == {
        "fast_period": 10, "slow_period": 30,
    }
    assert registry.portfolios() == {
        "portfolio_cn_stock_research": "sma_trend_cn_stock",
        "portfolio_cn_etf_research": "sma_trend_cn_etf",
    }


def test_comparison_is_neutral_and_never_selects():
    reports = [
        run(stock_experiment(), make_bars()),
        run(etf_experiment(), etf_bars()),
    ]
    comparison = compare_experiments(reports, baseline_strategy_id="sma_trend_cn_stock")
    assert comparison["comparison_policy"] == "neutral_side_by_side_no_ranking"
    assert comparison["selection_performed"] is False
    assert comparison["eligible_for_trading"] is False
    assert comparison["orders_enabled"] is False
    assert comparison["action"] == "no_trade"
    assert len(comparison["rows"]) == 2
    assert [row["experiment_id"] for row in comparison["rows"]] == [
        "exp_sma_stock_is_oos_v1", "exp_sma_etf_is_oos_v1",
    ]
    for row in comparison["rows"]:
        assert row["eligible_for_trading"] is False
        assert row["orders_enabled"] is False
        assert row["action"] == "no_trade"
    assert "rank" not in comparison and "winner" not in comparison

    with pytest.raises(StrategyExperimentError) as info:
        compare_experiments(reports, baseline_strategy_id="not_in_list")
    assert info.value.code == "unknown_baseline"


# --------------------------------------------------------------------------- #
# research-only safety invariants
# --------------------------------------------------------------------------- #
def test_report_keeps_research_only_invariants():
    report = run(stock_experiment(), make_bars())
    body = report.to_dict()
    assert body["action"] == "no_trade"
    assert body["eligible_for_trading"] is False
    assert body["orders_enabled"] is False
    assert body["experimental_only"] is True
    assert body["optimization_performed"] is False
    assert "Not a prediction" in body["disclaimer"]
    json.dumps(body)  # JSON serialisable

    for field, value in (
        ("eligible_for_trading", True),
        ("orders_enabled", True),
        ("action", "buy"),
        ("experimental_only", False),
        ("optimization_performed", True),
    ):
        with pytest.raises(StrategyExperimentError):
            dataclasses.replace(report, **{field: value})


def test_module_exposes_no_otw_or_order_interface():
    source = inspect.getsource(experiments)
    for forbidden in ("otw_write", "place_order", "create_order", "requests", "httpx", "socket"):
        assert forbidden not in source
    assert not hasattr(experiments, "place_order")
    assert not hasattr(experiments, "write_to_otw")


def test_backtest_gaps_are_declared_and_result_is_not_tradable():
    report = run(stock_experiment(), make_bars())
    gaps = report.backtest_gaps
    for required in (
        "dividend_cashflows_not_modeled",
        "suspension_days_not_modeled",
        "price_limit_up_down_and_queue_not_modeled",
        "t_plus_one_settlement_not_modeled",
    ):
        assert required in gaps
    assert "point_in_time_adjustment_unverified" in gaps
    assert report.tradability == "not_tradable"
    warnings = " ".join(report.provenance_warnings)
    assert "adjusted" in warnings
    assert set(BACKTEST_GAPS).issubset(set(gaps))


def test_unadjusted_series_drops_the_adjustment_caveat():
    rows = make_bars(adjustment="none")
    spec = stock_spec(
        dataset_id=expected_dataset_id("601118", "stock", "none"),
    )
    report = run(stock_experiment(strategy=spec, adjustment="none"), rows)
    assert report.status == "completed"
    assert "point_in_time_adjustment_unverified" not in report.backtest_gaps
    assert not any("adjusted" in warning for warning in report.provenance_warnings)


# --------------------------------------------------------------------------- #
# integration with the persisted market-data cache
# --------------------------------------------------------------------------- #
def test_store_round_trip_feeds_the_experiment(tmp_path):
    store = MarketDataStore(tmp_path)
    store.save(make_bars())
    store.save(etf_bars())

    loaded_stock = store.load("601118", "stock", "qfq")
    loaded_etf = store.load("510300", "etf", "qfq")
    assert len(loaded_stock) == 360

    stock_report = run(stock_experiment(), loaded_stock)
    etf_report = run(etf_experiment(), loaded_etf)
    assert stock_report.status == "completed"
    assert etf_report.status == "completed"
    assert stock_report.data["dataset_fingerprint"] != etf_report.data["dataset_fingerprint"]
    # The fingerprint is stable across a re-read of the same persisted series.
    assert run(stock_experiment(), store.load("601118", "stock", "qfq")).reproducibility_key == (
        stock_report.reproducibility_key
    )
