"""Provider-neutral strategy registry and out-of-sample experiment coordinator (M4).

This module is a *thin coordination layer* over the existing, already-tested
pieces. It deliberately does not implement another price backtester:

* price simulation, costs and the buy-and-hold benchmark come from
  :func:`investment_engine.backtest.run_backtest`;
* bar quality, point-in-time cutoffs and provenance fields come from
  :mod:`investment_engine.market_data`;
* the reusable strategy metadata contract (``strategy_id``, ``version``,
  ``asset_classes``, ``parameter_schema``, ``enabled``) comes from
  :class:`investment_engine.contracts.StrategyDefinition`.

What is added here is the experiment skeleton: an immutable strategy registry
whose entries are bound to a dataset, a risk policy and an isolated portfolio,
a declared in-sample (IS) / out-of-sample (OOS) time split with optional
walk-forward folds, and one comparable result shape for a rule baseline and a
future AI strategy.

Hard boundaries of this layer
-----------------------------
* Research only. Every result is fixed to ``action="no_trade"``,
  ``eligible_for_trading=False``, ``orders_enabled=False`` and
  ``experimental_only=True``. These values are re-asserted on construction and
  there is no setter, switch or OTW write path in this module.
* No parameter search, no return maximisation, no automatic strategy
  promotion. ``parameter_provenance`` must be ``"pre_registered"`` and every
  report states ``optimization_performed=False``.
* No look-ahead. Bars dated after the experiment's ``as_of`` are rejected
  rather than silently truncated, and the IS/OOS windows may not overlap.
* Unknown required inputs fail closed with :class:`StrategyExperimentError`
  instead of being defaulted. Insufficient (but valid) samples produce an
  explicit ``not_evaluable`` report instead of a synthetic result.
* Trading-rule data that this repository does not have (dividends, suspension,
  limit up/down, T+1, ST status, market impact) is reported as an explicit
  backtest gap and the result is marked not tradable.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Any, Iterable, Mapping, Sequence

from .backtest import BacktestError, run_backtest
from .contracts import StrategyDefinition, StrategyRegistry
from .market_data import ADJUSTMENTS, ASSET_TYPES, Bar, validate_bars

EXPERIMENT_SCHEMA_VERSION = "1"

#: Supported ``run_backtest`` strategy kinds. Adding a new kind requires an
#: explicit backtest implementation in ``investment_engine.backtest``.
SUPPORTED_IMPLEMENTATIONS = ("sma_trend", "buy_and_hold")

#: Trading rules this repository has no data for. They are reported, not
#: silently assumed away, and they are why ``tradability`` is never ``tradable``.
BACKTEST_GAPS: tuple[str, ...] = (
    "dividend_cashflows_not_modeled",
    "suspension_days_not_modeled",
    "price_limit_up_down_and_queue_not_modeled",
    "t_plus_one_settlement_not_modeled",
    "st_delisting_status_not_modeled",
    "market_impact_and_capacity_not_modeled",
    "survivorship_and_index_constituent_history_not_modeled",
)

#: Results are compared side by side, never ranked or auto-selected.
COMPARISON_POLICY = "neutral_side_by_side_no_ranking"

_METRIC_FIELDS = (
    "total_return",
    "cagr",
    "annualized_volatility",
    "sharpe_ratio",
    "max_drawdown",
    "positive_day_ratio",
    "exposure",
    "turnover_units",
    "entry_count",
    "observations",
    "fee_bps_per_side",
    "slippage_bps_per_side",
)

_ID_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:@+-]{0,127}")
_SYMBOL_PATTERN = re.compile(r"[A-Za-z0-9._-]{1,24}")
_PARAMETER_TYPES = ("int", "float", "bool", "str")


class StrategyExperimentError(ValueError):
    """Fail-closed contract violation in an experiment definition or dataset."""

    def __init__(self, message: str, *, code: str = "invalid_experiment"):
        super().__init__(message)
        self.code = code


# --------------------------------------------------------------------------- #
# small validation helpers
# --------------------------------------------------------------------------- #
def _identifier(value: Any, name: str) -> str:
    if not isinstance(value, str) or not _ID_PATTERN.fullmatch(value.strip()):
        raise StrategyExperimentError(
            f"{name} must be a non-empty identifier", code=f"invalid_{name}"
        )
    return value.strip()


def _symbol(value: Any, name: str = "symbol") -> str:
    if not isinstance(value, str) or not _SYMBOL_PATTERN.fullmatch(value.strip()):
        raise StrategyExperimentError(f"{name} is not a valid market symbol", code=f"invalid_{name}")
    return value.strip()


def _as_date(value: Any, name: str) -> date:
    if isinstance(value, datetime):
        raise StrategyExperimentError(f"{name} must be a date, not a datetime", code=f"invalid_{name}")
    if not isinstance(value, date):
        raise StrategyExperimentError(f"{name} must be a date", code=f"invalid_{name}")
    return value


def _as_utc(value: Any, name: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise StrategyExperimentError(f"{name} must be a timezone-aware datetime", code=f"invalid_{name}")
    return value.astimezone(timezone.utc)


def _bps(value: Any, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise StrategyExperimentError(
            f"{name} must be an explicit number (costs are never defaulted)", code=f"missing_{name}"
        )
    number = float(value)
    if not math.isfinite(number) or number < 0 or number > 500:
        raise StrategyExperimentError(f"{name} must be between 0 and 500 bps", code=f"invalid_{name}")
    return number


def _positive_int(value: Any, name: str, *, minimum: int = 1) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise StrategyExperimentError(f"{name} must be an integer >= {minimum}", code=f"invalid_{name}")
    return value


def _freeze_parameters(parameters: Any) -> dict[str, Any]:
    """Copy a parameter mapping so a caller cannot mutate a registered strategy."""
    if parameters is None:
        return {}
    if not isinstance(parameters, Mapping):
        raise StrategyExperimentError("parameters must be a mapping", code="invalid_parameters")
    frozen: dict[str, Any] = {}
    for key, value in parameters.items():
        if not isinstance(key, str) or not key.strip():
            raise StrategyExperimentError("parameter names must be non-empty strings", code="invalid_parameters")
        if isinstance(value, bool):
            frozen[key] = value
        elif isinstance(value, (int, float)):
            if isinstance(value, float) and not math.isfinite(value):
                raise StrategyExperimentError(f"parameter {key} is not finite", code="invalid_parameters")
            frozen[key] = value
        elif isinstance(value, str):
            frozen[key] = value
        elif value is None:
            frozen[key] = None
        else:
            raise StrategyExperimentError(
                f"parameter {key} must be a scalar (int/float/bool/str/None)", code="invalid_parameters"
            )
    return frozen


def _parameter_type_ok(value: Any, expected: str) -> bool:
    if expected == "bool":
        return isinstance(value, bool)
    if expected == "int":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected == "float":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    return isinstance(value, str)


def _validate_parameters(parameters: Mapping[str, Any], schema: Mapping[str, Any]) -> None:
    if not isinstance(schema, Mapping):
        raise StrategyExperimentError("parameter_schema must be a mapping", code="invalid_parameter_schema")
    declared: dict[str, Mapping[str, Any]] = {}
    for name, rule in schema.items():
        if not isinstance(name, str) or not isinstance(rule, Mapping):
            raise StrategyExperimentError("parameter_schema entries must be mappings", code="invalid_parameter_schema")
        expected = rule.get("type", "float")
        if expected not in _PARAMETER_TYPES:
            raise StrategyExperimentError(
                f"parameter_schema {name} has unsupported type {expected!r}", code="invalid_parameter_schema"
            )
        declared[name] = rule
    missing = sorted(name for name, rule in declared.items() if rule.get("required") and name not in parameters)
    if missing:
        raise StrategyExperimentError(
            "missing required strategy parameters: " + ", ".join(missing), code="missing_parameters"
        )
    unknown = sorted(set(parameters) - set(declared))
    if unknown:
        raise StrategyExperimentError(
            "undeclared strategy parameters: " + ", ".join(unknown), code="unknown_parameters"
        )
    for name, value in parameters.items():
        rule = declared[name]
        if not _parameter_type_ok(value, str(rule.get("type", "float"))):
            raise StrategyExperimentError(
                f"parameter {name} must be {rule.get('type', 'float')}", code="invalid_parameter_type"
            )
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            if "minimum" in rule and value < rule["minimum"]:
                raise StrategyExperimentError(f"parameter {name} is below its minimum", code="parameter_out_of_range")
            if "maximum" in rule and value > rule["maximum"]:
                raise StrategyExperimentError(f"parameter {name} is above its maximum", code="parameter_out_of_range")


def _canonical_json(payload: Any) -> str:
    return json.dumps(_jsonable(payload), sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _jsonable(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    return value


def _sha256(payload: Any) -> str:
    return hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()


# --------------------------------------------------------------------------- #
# strategy specification
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class StrategySpec:
    """An immutable, provider-neutral strategy registration entry.

    ``enabled`` exists so that the contract matches the M4 registry design and
    defaults to ``False``. This research layer has no approval or activation
    path, so constructing a spec with ``enabled=True`` fails closed: enabling a
    strategy is a separate, explicitly reviewed deployment concern.
    """

    strategy_id: str
    strategy_version: str
    name: str
    asset_class: str
    parameters: Mapping[str, Any]
    dataset_id: str
    risk_policy_id: str
    portfolio_id: str
    implementation: str = "sma_trend"
    enabled: bool = False
    decision_horizon: str = "daily"
    risk_model: str = "long_only_research"
    required_inputs: tuple[str, ...] = ("daily_ohlcv",)
    parameter_schema: Mapping[str, Any] = field(default_factory=dict)
    description: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.strategy_version, str) or not self.strategy_version.strip():
            raise StrategyExperimentError("strategy_version is required", code="missing_strategy_version")
        _identifier(self.strategy_id, "strategy_id")
        if not isinstance(self.name, str) or not self.name.strip():
            raise StrategyExperimentError("strategy name is required", code="invalid_strategy_name")
        if self.asset_class not in ASSET_TYPES:
            raise StrategyExperimentError(
                f"asset_class must be one of {sorted(ASSET_TYPES)}", code="invalid_asset_class"
            )
        if self.implementation not in SUPPORTED_IMPLEMENTATIONS:
            raise StrategyExperimentError(
                f"implementation must be one of {list(SUPPORTED_IMPLEMENTATIONS)}",
                code="unsupported_implementation",
            )
        _identifier(self.dataset_id, "dataset_id")
        _identifier(self.risk_policy_id, "risk_policy_id")
        _identifier(self.portfolio_id, "portfolio_id")
        if self.enabled is not False:
            raise StrategyExperimentError(
                "strategies cannot be enabled from the research experiment layer", code="activation_not_supported"
            )
        if not isinstance(self.enabled, bool):
            raise StrategyExperimentError("enabled must be a boolean", code="invalid_enabled")
        schema = dict(self.parameter_schema or {})
        parameters = _freeze_parameters(self.parameters)
        _validate_parameters(parameters, schema)
        if self.implementation == "sma_trend":
            for required in ("fast_period", "slow_period"):
                if required not in parameters:
                    raise StrategyExperimentError(
                        f"sma_trend requires an explicit {required}", code="missing_parameters"
                    )
            if parameters["fast_period"] >= parameters["slow_period"]:
                raise StrategyExperimentError(
                    "fast_period must be smaller than slow_period", code="invalid_parameters"
                )
        # Store defensive copies; the frozen dataclass keeps the binding fixed.
        object.__setattr__(self, "parameters", parameters)
        object.__setattr__(self, "parameter_schema", schema)
        object.__setattr__(self, "required_inputs", tuple(self.required_inputs))

    @property
    def registry_key(self) -> tuple[str, str]:
        return (self.strategy_id, self.strategy_version)

    @property
    def min_bars_required(self) -> int:
        """Bars ``run_backtest`` needs before it will produce a result."""
        if self.implementation == "sma_trend":
            return int(self.parameters["slow_period"]) + 2
        return 3

    def as_definition(self) -> StrategyDefinition:
        """Adapt to the shared :class:`~investment_engine.contracts.StrategyDefinition`."""
        return StrategyDefinition(
            strategy_id=self.strategy_id,
            name=self.name,
            version=self.strategy_version,
            asset_classes=(self.asset_class,),
            required_inputs=tuple(self.required_inputs),
            parameter_schema=dict(self.parameter_schema),
            decision_horizon=self.decision_horizon,
            risk_model=self.risk_model,
            implementation=f"investment_engine.backtest.run_backtest#{self.implementation}",
            enabled=self.enabled,
        )

    def validate(self) -> None:
        """Reuse the v0.6 metadata contract validator; it must keep passing."""
        self.as_definition().validate()

    def to_dict(self) -> dict[str, Any]:
        return {
            "strategy_id": self.strategy_id,
            "strategy_version": self.strategy_version,
            "name": self.name,
            "asset_class": self.asset_class,
            "parameters": dict(self.parameters),
            "dataset_id": self.dataset_id,
            "risk_policy_id": self.risk_policy_id,
            "portfolio_id": self.portfolio_id,
            "implementation": self.implementation,
            "enabled": False,
            "decision_horizon": self.decision_horizon,
            "risk_model": self.risk_model,
            "required_inputs": list(self.required_inputs),
            "parameter_schema": dict(self.parameter_schema),
            "description": self.description,
        }


@dataclass(frozen=True)
class WalkForwardConfig:
    """Fixed, pre-declared walk-forward geometry. No search is performed."""

    train_bars: int
    test_bars: int
    step_bars: int | None = None

    def __post_init__(self) -> None:
        _positive_int(self.train_bars, "train_bars", minimum=3)
        _positive_int(self.test_bars, "test_bars", minimum=2)
        if self.step_bars is not None:
            _positive_int(self.step_bars, "step_bars", minimum=1)

    @property
    def step(self) -> int:
        return self.test_bars if self.step_bars is None else self.step_bars

    def to_dict(self) -> dict[str, Any]:
        return {"train_bars": self.train_bars, "test_bars": self.test_bars, "step_bars": self.step}


# --------------------------------------------------------------------------- #
# experiment specification
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class ExperimentSpec:
    """A declared IS/OOS experiment. All required fields are explicit.

    Costs, windows and parameter provenance have no defaults on purpose:
    an unknown cost or an undeclared window must fail closed rather than be
    silently replaced by a convenient assumption.
    """

    experiment_id: str
    strategy: StrategySpec
    symbol: str
    asset_class: str
    adjustment: str
    interval: str
    is_start: date
    is_end: date
    oos_start: date
    oos_end: date
    as_of: datetime
    fee_bps: float
    slippage_bps: float
    benchmark: str = "buy_and_hold"
    parameter_provenance: str = "pre_registered"
    walk_forward: WalkForwardConfig | None = None
    notes: str = ""

    def __post_init__(self) -> None:
        _identifier(self.experiment_id, "experiment_id")
        if not isinstance(self.strategy, StrategySpec):
            raise StrategyExperimentError("strategy must be a StrategySpec", code="invalid_strategy")
        self.strategy.validate()
        _symbol(self.symbol)
        if self.asset_class not in ASSET_TYPES:
            raise StrategyExperimentError(
                f"asset_class must be one of {sorted(ASSET_TYPES)}", code="invalid_asset_class"
            )
        if self.asset_class != self.strategy.asset_class:
            raise StrategyExperimentError(
                "experiment asset_class must match the strategy asset_class", code="asset_class_mismatch"
            )
        if self.adjustment not in ADJUSTMENTS:
            raise StrategyExperimentError(
                f"adjustment must be one of {sorted(ADJUSTMENTS)}", code="invalid_adjustment"
            )
        if self.interval != "1d":
            raise StrategyExperimentError("only daily bars (interval='1d') are supported", code="invalid_interval")
        start_is = _as_date(self.is_start, "is_start")
        end_is = _as_date(self.is_end, "is_end")
        start_oos = _as_date(self.oos_start, "oos_start")
        end_oos = _as_date(self.oos_end, "oos_end")
        if start_is > end_is:
            raise StrategyExperimentError("is_start must not be after is_end", code="invalid_is_window")
        if start_oos > end_oos:
            raise StrategyExperimentError("oos_start must not be after oos_end", code="invalid_oos_window")
        if start_oos <= end_is:
            raise StrategyExperimentError(
                "in-sample and out-of-sample windows must not overlap or touch",
                code="is_oos_overlap",
            )
        _as_utc(self.as_of, "as_of")
        _bps(self.fee_bps, "fee_bps")
        _bps(self.slippage_bps, "slippage_bps")
        if self.benchmark != "buy_and_hold":
            raise StrategyExperimentError(
                "only the buy_and_hold benchmark is available", code="unsupported_benchmark"
            )
        if self.parameter_provenance != "pre_registered":
            raise StrategyExperimentError(
                "parameters must be pre-registered; this layer performs no parameter search",
                code="parameter_search_not_supported",
            )
        if self.walk_forward is not None and not isinstance(self.walk_forward, WalkForwardConfig):
            raise StrategyExperimentError("walk_forward must be a WalkForwardConfig", code="invalid_walk_forward")
        if self.walk_forward is not None:
            needed = self.strategy.min_bars_required
            if self.walk_forward.test_bars < needed:
                raise StrategyExperimentError(
                    f"walk-forward test_bars must be >= {needed} bars for this strategy",
                    code="walk_forward_window_too_short",
                )
            if self.walk_forward.train_bars < needed:
                raise StrategyExperimentError(
                    f"walk-forward train_bars must be >= {needed} bars for this strategy",
                    code="walk_forward_window_too_short",
                )

    @property
    def parameters(self) -> Mapping[str, Any]:
        return self.strategy.parameters

    def to_dict(self) -> dict[str, Any]:
        return {
            "experiment_id": self.experiment_id,
            "strategy": self.strategy.to_dict(),
            "symbol": self.symbol,
            "asset_class": self.asset_class,
            "adjustment": self.adjustment,
            "interval": self.interval,
            "is_window": {"start": self.is_start.isoformat(), "end": self.is_end.isoformat()},
            "oos_window": {"start": self.oos_start.isoformat(), "end": self.oos_end.isoformat()},
            "as_of": _jsonable(self.as_of),
            "fee_bps": float(self.fee_bps),
            "slippage_bps": float(self.slippage_bps),
            "benchmark": self.benchmark,
            "parameter_provenance": self.parameter_provenance,
            "walk_forward": self.walk_forward.to_dict() if self.walk_forward else None,
            "notes": self.notes,
        }


# --------------------------------------------------------------------------- #
# experiment report
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class ExperimentReport:
    """One comparable, research-only experiment result.

    Safety fields are fixed at construction and re-validated in ``__post_init__``.
    """

    schema_version: str
    experiment_id: str
    strategy_id: str
    strategy_version: str
    asset_class: str
    symbol: str
    portfolio_id: str
    risk_policy_id: str
    dataset_id: str
    parameters: Mapping[str, Any]
    parameter_provenance: str
    status: str
    not_evaluable_reasons: tuple[str, ...]
    as_of: str
    is_window: Mapping[str, Any]
    oos_window: Mapping[str, Any]
    data: Mapping[str, Any]
    costs: Mapping[str, Any]
    is_metrics: Mapping[str, Any] | None
    oos_metrics: Mapping[str, Any] | None
    is_benchmark_metrics: Mapping[str, Any] | None
    oos_benchmark_metrics: Mapping[str, Any] | None
    excess_return_is: float | None
    excess_return_oos: float | None
    walk_forward: Mapping[str, Any] | None
    backtest_gaps: tuple[str, ...]
    provenance_warnings: tuple[str, ...]
    reproducibility_key: str
    assumptions: tuple[str, ...]
    action: str = "no_trade"
    eligible_for_trading: bool = False
    orders_enabled: bool = False
    experimental_only: bool = True
    optimization_performed: bool = False
    tradability: str = "not_tradable"
    disclaimer: str = (
        "Research experiment only. Not a prediction, recommendation, trading signal or "
        "authorization to trade. No order, OTW write or model call is produced."
    )

    def __post_init__(self) -> None:
        if self.action != "no_trade":
            raise StrategyExperimentError("research reports must keep action='no_trade'", code="safety_invariant")
        if self.eligible_for_trading or self.orders_enabled:
            raise StrategyExperimentError(
                "research reports cannot be eligible for trading or enable orders", code="safety_invariant"
            )
        if not self.experimental_only:
            raise StrategyExperimentError("research reports are experimental_only", code="safety_invariant")
        if self.optimization_performed:
            raise StrategyExperimentError(
                "this layer never performs parameter optimisation", code="safety_invariant"
            )
        if self.status not in ("completed", "not_evaluable"):
            raise StrategyExperimentError("unknown report status", code="invalid_status")
        if self.status == "completed" and (self.is_metrics is None or self.oos_metrics is None):
            raise StrategyExperimentError(
                "a completed report needs both IS and OOS metrics", code="incomplete_report"
            )

    @property
    def is_evaluable(self) -> bool:
        return self.status == "completed"

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "experiment_id": self.experiment_id,
            "strategy_id": self.strategy_id,
            "strategy_version": self.strategy_version,
            "asset_class": self.asset_class,
            "symbol": self.symbol,
            "portfolio_id": self.portfolio_id,
            "risk_policy_id": self.risk_policy_id,
            "dataset_id": self.dataset_id,
            "parameters": dict(self.parameters),
            "parameter_provenance": self.parameter_provenance,
            "status": self.status,
            "not_evaluable_reasons": list(self.not_evaluable_reasons),
            "as_of": self.as_of,
            "is_window": dict(self.is_window),
            "oos_window": dict(self.oos_window),
            "data": dict(self.data),
            "costs": dict(self.costs),
            "is_metrics": dict(self.is_metrics) if self.is_metrics is not None else None,
            "oos_metrics": dict(self.oos_metrics) if self.oos_metrics is not None else None,
            "is_benchmark_metrics": dict(self.is_benchmark_metrics) if self.is_benchmark_metrics is not None else None,
            "oos_benchmark_metrics": dict(self.oos_benchmark_metrics) if self.oos_benchmark_metrics is not None else None,
            "excess_return_is": self.excess_return_is,
            "excess_return_oos": self.excess_return_oos,
            "walk_forward": _jsonable(self.walk_forward) if self.walk_forward is not None else None,
            "backtest_gaps": list(self.backtest_gaps),
            "provenance_warnings": list(self.provenance_warnings),
            "reproducibility_key": self.reproducibility_key,
            "assumptions": list(self.assumptions),
            "action": self.action,
            "eligible_for_trading": False,
            "orders_enabled": False,
            "experimental_only": True,
            "optimization_performed": False,
            "tradability": self.tradability,
            "disclaimer": self.disclaimer,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, sort_keys=True)


# --------------------------------------------------------------------------- #
# dataset provenance
# --------------------------------------------------------------------------- #
def _bar_day(bar: Bar) -> date:
    try:
        return date.fromisoformat(str(bar.timestamp)[:10])
    except (TypeError, ValueError) as exc:
        raise StrategyExperimentError("bar has an invalid timestamp", code="invalid_bars") from exc


def dataset_fingerprint(bars: Sequence[Bar]) -> str:
    """Deterministic hash of the exact price series used by an experiment."""
    payload = [
        [
            bar.symbol,
            bar.asset_type,
            bar.adjustment,
            bar.interval,
            bar.timestamp,
            format(float(bar.open), ".10g"),
            format(float(bar.high), ".10g"),
            format(float(bar.low), ".10g"),
            format(float(bar.close), ".10g"),
            format(float(bar.volume), ".10g"),
            None if bar.amount is None else format(float(bar.amount), ".10g"),
            bar.source,
            bar.fetched_at,
        ]
        for bar in bars
    ]
    return _sha256({"schema": EXPERIMENT_SCHEMA_VERSION, "bars": payload})


def expected_dataset_id(symbol: str, asset_class: str, adjustment: str, interval: str = "1d") -> str:
    """The canonical dataset identifier an experiment must declare."""
    return f"{asset_class}:{symbol}:{adjustment}:{interval}"


def _check_bars(spec: ExperimentSpec, bars: Sequence[Bar]) -> list[Bar]:
    rows = list(bars)
    if not rows:
        raise StrategyExperimentError("no bars supplied for the experiment", code="empty_dataset")
    for index, bar in enumerate(rows):
        if not isinstance(bar, Bar):
            raise StrategyExperimentError(f"row {index} is not a Bar", code="invalid_bars")
        if bar.symbol != spec.symbol:
            raise StrategyExperimentError(
                f"row {index} belongs to {bar.symbol}, not {spec.symbol}", code="dataset_symbol_mismatch"
            )
        if bar.asset_type != spec.asset_class:
            raise StrategyExperimentError(
                f"row {index} asset_type {bar.asset_type} does not match {spec.asset_class}",
                code="dataset_asset_class_mismatch",
            )
        if bar.adjustment != spec.adjustment:
            raise StrategyExperimentError(
                f"row {index} adjustment {bar.adjustment} does not match {spec.adjustment}",
                code="dataset_adjustment_mismatch",
            )
        if bar.interval != spec.interval:
            raise StrategyExperimentError(
                f"row {index} interval {bar.interval} does not match {spec.interval}",
                code="dataset_interval_mismatch",
            )
        if not isinstance(bar.source, str) or not bar.source.strip():
            raise StrategyExperimentError(f"row {index} has no data source", code="missing_provenance")
        if not isinstance(bar.fetched_at, str) or not bar.fetched_at.strip():
            raise StrategyExperimentError(f"row {index} has no fetched_at timestamp", code="missing_provenance")
        if not isinstance(bar.currency, str) or not bar.currency.strip():
            raise StrategyExperimentError(f"row {index} has no currency", code="missing_provenance")
    sources = {bar.source for bar in rows}
    if len(sources) > 1:
        raise StrategyExperimentError(
            "bars mix data sources: " + ", ".join(sorted(sources)), code="mixed_provenance"
        )
    declared = expected_dataset_id(spec.symbol, spec.asset_class, spec.adjustment, spec.interval)
    if spec.strategy.dataset_id != declared:
        raise StrategyExperimentError(
            f"strategy dataset_id {spec.strategy.dataset_id!r} does not describe the supplied data "
            f"({declared!r})",
            code="dataset_id_mismatch",
        )
    # Reject look-ahead explicitly before generic quality checks so the caller
    # gets the precise code instead of a generic "invalid series" message.
    future = sorted({_bar_day(bar).isoformat() for bar in rows if _bar_day(bar) > spec.as_of.date()})
    if future:
        raise StrategyExperimentError(
            "dataset contains bars after as_of: " + ", ".join(future[:5]), code="lookahead_future_bars"
        )
    quality = validate_bars(rows, as_of=spec.as_of.date())
    if not quality["valid"]:
        raise StrategyExperimentError(
            "invalid bar series: " + "; ".join(quality["errors"][:3]), code="invalid_bars"
        )
    return rows


def _window_rows(rows: Sequence[Bar], start: date, end: date) -> list[Bar]:
    return [bar for bar in rows if start <= _bar_day(bar) <= end]


def _window_block(rows: Sequence[Bar], start: date, end: date) -> dict[str, Any]:
    return {
        "start": start.isoformat(),
        "end": end.isoformat(),
        "bar_count": len(rows),
        "first_bar": rows[0].timestamp if rows else None,
        "last_bar": rows[-1].timestamp if rows else None,
    }


def _data_block(spec: ExperimentSpec, rows: Sequence[Bar], is_rows: Sequence[Bar],
                oos_rows: Sequence[Bar]) -> dict[str, Any]:
    fetched = sorted(bar.fetched_at for bar in rows)
    return {
        "symbol": spec.symbol,
        "asset_class": spec.asset_class,
        "exchange": rows[0].exchange,
        "currency": rows[0].currency,
        "source": rows[0].source,
        "adjustment": spec.adjustment,
        "price_frequency": spec.interval,
        "dataset_id": spec.strategy.dataset_id,
        "dataset_fingerprint": dataset_fingerprint(rows),
        "fetched_at_earliest": fetched[0],
        "fetched_at_latest": fetched[-1],
        "bar_count": len(rows),
        "first_bar": rows[0].timestamp,
        "last_bar": rows[-1].timestamp,
        "is_bar_count": len(is_rows),
        "oos_bar_count": len(oos_rows),
        "min_bars_required_per_window": spec.strategy.min_bars_required,
    }


# --------------------------------------------------------------------------- #
# metric extraction
# --------------------------------------------------------------------------- #
def _metrics_block(result: Mapping[str, Any], *, label: str) -> dict[str, Any]:
    metrics = result.get("metrics")
    if not isinstance(metrics, Mapping):
        raise StrategyExperimentError("backtest result has no metrics block", code="backtest_contract_incomplete")
    missing = [name for name in _METRIC_FIELDS if name not in metrics]
    if missing:
        raise StrategyExperimentError(
            "backtest metrics missing: " + ", ".join(missing), code="backtest_contract_incomplete"
        )
    block = {name: metrics[name] for name in _METRIC_FIELDS}
    block["label"] = label
    return block


def _run_slice(spec: ExperimentSpec, rows: Sequence[Bar], *, window_end: date) -> dict[str, Any]:
    """Delegate to the existing v0.6 backtester; never re-implement signals."""
    try:
        return run_backtest(
            rows,
            strategy=spec.strategy.implementation,
            fast_period=int(spec.parameters.get("fast_period", 20)),
            slow_period=int(spec.parameters.get("slow_period", 60)),
            fee_bps=float(spec.fee_bps),
            slippage_bps=float(spec.slippage_bps),
            as_of=window_end,
        )
    except BacktestError as exc:
        raise StrategyExperimentError(str(exc), code="backtest_failed") from exc


def _excess(result: Mapping[str, Any]) -> float | None:
    value = result.get("excess_return_vs_benchmark")
    return None if value is None else float(value)


# --------------------------------------------------------------------------- #
# walk-forward
# --------------------------------------------------------------------------- #
def walk_forward_folds(rows: Sequence[Bar], config: WalkForwardConfig, *,
                       min_train_bars: int) -> list[tuple[list[Bar], list[Bar]]]:
    """Fixed rolling train/test slices. Declared geometry, no data snooping."""
    folds: list[tuple[list[Bar], list[Bar]]] = []
    train = max(config.train_bars, min_train_bars)
    offset = 0
    while offset + train + config.test_bars <= len(rows):
        train_rows = list(rows[offset:offset + train])
        test_rows = list(rows[offset + train:offset + train + config.test_bars])
        folds.append((train_rows, test_rows))
        offset += config.step
    return folds


def _walk_forward_block(spec: ExperimentSpec, oos_rows: Sequence[Bar]) -> dict[str, Any] | None:
    config = spec.walk_forward
    if config is None:
        return None
    folds = walk_forward_folds(oos_rows, config, min_train_bars=spec.strategy.min_bars_required)
    fold_reports: list[dict[str, Any]] = []
    compounded = 1.0
    compounded_benchmark = 1.0
    completed = 0
    worst_return: float | None = None
    worst_drawdown: float | None = None
    for index, (train_rows, test_rows) in enumerate(folds):
        train_result = _run_slice(spec, train_rows, window_end=_bar_day(train_rows[-1]))
        test_result = _run_slice(spec, test_rows, window_end=_bar_day(test_rows[-1]))
        train_metrics = _metrics_block(train_result, label=f"fold_{index}_train")
        test_metrics = _metrics_block(test_result, label=f"fold_{index}_test")
        bench_metrics = _metrics_block(test_result["benchmark"], label=f"fold_{index}_bench")
        completed += 1
        compounded *= 1.0 + float(test_metrics["total_return"])
        compounded_benchmark *= 1.0 + float(bench_metrics["total_return"])
        worst_return = test_metrics["total_return"] if worst_return is None else min(
            worst_return, float(test_metrics["total_return"])
        )
        worst_drawdown = float(test_metrics["max_drawdown"]) if worst_drawdown is None else min(
            worst_drawdown, float(test_metrics["max_drawdown"])
        )
        fold_reports.append({
            "fold_index": index,
            "train_window": _window_block(train_rows, _bar_day(train_rows[0]), _bar_day(train_rows[-1])),
            "test_window": _window_block(test_rows, _bar_day(test_rows[0]), _bar_day(test_rows[-1])),
            "train_metrics": train_metrics,
            "test_metrics": test_metrics,
            "test_benchmark_metrics": bench_metrics,
        })
    return {
        "config": config.to_dict(),
        "fold_count": len(fold_reports),
        "completed_fold_count": completed,
        "folds": fold_reports,
        "aggregate_total_return": round(compounded - 1.0, 6) if completed else None,
        "aggregate_benchmark_total_return": round(compounded_benchmark - 1.0, 6) if completed else None,
        "aggregate_excess_return": (
            round((compounded - 1.0) - (compounded_benchmark - 1.0), 6) if completed else None
        ),
        "worst_fold_return": worst_return,
        "worst_fold_max_drawdown": worst_drawdown,
        "ratio_metrics_aggregation": "not_performed",
        "notes": [
            "Each fold is an independent long-only backtest; positions restart flat at each test window.",
            "Aggregate return compounds fold test returns; Sharpe/CAGR are not stitched across folds.",
            "Fold geometry is declared before running and is never tuned against the test windows.",
        ],
    }


# --------------------------------------------------------------------------- #
# the coordinator
# --------------------------------------------------------------------------- #
def run_experiment(
    spec: ExperimentSpec,
    bars: Sequence[Bar],
    *,
    now: datetime | None = None,
) -> ExperimentReport:
    """Run one declared IS/OOS experiment with the existing backtester.

    Raises :class:`StrategyExperimentError` when the definition or the dataset
    violates the contract (look-ahead, overlap, mixed provenance, unknown
    inputs). Returns a ``not_evaluable`` report when the data is valid but too
    short to support the declared split.
    """
    if not isinstance(spec, ExperimentSpec):
        raise StrategyExperimentError("spec must be an ExperimentSpec", code="invalid_experiment")
    reference = now if now is not None else datetime.now(timezone.utc)
    reference = _as_utc(reference, "now")
    if spec.as_of.astimezone(timezone.utc) > reference:
        raise StrategyExperimentError("as_of cannot be in the future", code="future_as_of")

    rows = _check_bars(spec, bars)
    is_rows = _window_rows(rows, spec.is_start, spec.is_end)
    oos_rows = _window_rows(rows, spec.oos_start, spec.oos_end)
    required = spec.strategy.min_bars_required
    reasons: list[str] = []
    if len(is_rows) < required:
        reasons.append(f"insufficient_is_bars:{len(is_rows)}<{required}")
    if len(oos_rows) < required:
        reasons.append(f"insufficient_oos_bars:{len(oos_rows)}<{required}")
    if spec.walk_forward is not None:
        fold_count = len(walk_forward_folds(oos_rows, spec.walk_forward,
                                            min_train_bars=required))
        if fold_count == 0:
            reasons.append("insufficient_walk_forward_folds")

    data = _data_block(spec, rows, is_rows, oos_rows)
    costs = {
        "fee_bps_per_side": float(spec.fee_bps),
        "slippage_bps_per_side": float(spec.slippage_bps),
        "total_bps_per_side": float(spec.fee_bps) + float(spec.slippage_bps),
        "model": "flat basis points per side applied on every position change",
        "explicit": True,
    }
    provenance_warnings = [
        "Bars are a stored research fixture/cache snapshot, not a live exchange feed.",
        "Costs are declared flat basis points; spread, taxes and impact are absent.",
    ]
    if spec.adjustment != "none":
        provenance_warnings.append(
            f"adjustment={spec.adjustment!r} is the currently reported adjusted series; historical "
            "bars may embed later corporate-action revisions and are not a point-in-time adjusted series."
        )
    gaps = list(BACKTEST_GAPS)
    if spec.adjustment != "none":
        gaps.append("point_in_time_adjustment_unverified")

    assumptions = (
        "Signals use completed daily closes and execute at the next session open.",
        "Long-only, no leverage, no shorting.",
        "The buy-and-hold benchmark is charged one entry cost.",
        "Both IS and OOS windows are evaluated with identical costs and parameters.",
        "Out-of-sample means only that the window is disjoint from the IS window; it does not "
        "guarantee the parameters were unknown to the researcher.",
    )

    base = {
        "schema_version": EXPERIMENT_SCHEMA_VERSION,
        "experiment_id": spec.experiment_id,
        "strategy_id": spec.strategy.strategy_id,
        "strategy_version": spec.strategy.strategy_version,
        "asset_class": spec.asset_class,
        "symbol": spec.symbol,
        "portfolio_id": spec.strategy.portfolio_id,
        "risk_policy_id": spec.strategy.risk_policy_id,
        "dataset_id": spec.strategy.dataset_id,
        "parameters": dict(spec.parameters),
        "parameter_provenance": spec.parameter_provenance,
        "as_of": _jsonable(spec.as_of),
        "is_window": _window_block(is_rows, spec.is_start, spec.is_end),
        "oos_window": _window_block(oos_rows, spec.oos_start, spec.oos_end),
        "data": data,
        "costs": costs,
        "backtest_gaps": tuple(gaps),
        "provenance_warnings": tuple(provenance_warnings),
        "assumptions": assumptions,
    }
    reproducibility_key = _sha256({
        "schema_version": EXPERIMENT_SCHEMA_VERSION,
        "spec": spec.to_dict(),
        "dataset_fingerprint": data["dataset_fingerprint"],
        "metric_contract": list(_METRIC_FIELDS),
    })

    if reasons:
        return ExperimentReport(
            **base,
            status="not_evaluable",
            not_evaluable_reasons=tuple(reasons),
            is_metrics=None,
            oos_metrics=None,
            is_benchmark_metrics=None,
            oos_benchmark_metrics=None,
            excess_return_is=None,
            excess_return_oos=None,
            walk_forward=None,
            reproducibility_key=reproducibility_key,
            tradability="not_evaluable",
        )

    is_result = _run_slice(spec, is_rows, window_end=spec.is_end)
    oos_result = _run_slice(spec, oos_rows, window_end=spec.oos_end)
    return ExperimentReport(
        **base,
        status="completed",
        not_evaluable_reasons=(),
        is_metrics=_metrics_block(is_result, label="in_sample"),
        oos_metrics=_metrics_block(oos_result, label="out_of_sample"),
        is_benchmark_metrics=_metrics_block(is_result["benchmark"], label="in_sample_benchmark"),
        oos_benchmark_metrics=_metrics_block(oos_result["benchmark"], label="out_of_sample_benchmark"),
        excess_return_is=_excess(is_result),
        excess_return_oos=_excess(oos_result),
        walk_forward=_walk_forward_block(spec, oos_rows),
        reproducibility_key=reproducibility_key,
    )


run_strategy_experiment = run_experiment


# --------------------------------------------------------------------------- #
# registry
# --------------------------------------------------------------------------- #
class StrategyExperimentRegistry:
    """Versioned, isolated strategy registry for experiments.

    ``contracts.StrategyDefinition`` is reused for metadata validation, but this
    registry keys specs by ``(strategy_id, strategy_version)``: the v0.6
    ``StrategyRegistry`` keys by ``strategy_id`` alone and therefore cannot hold
    the version history an IS/OOS comparison needs. ``export_enabled()`` bridges
    back to the v0.6 registry for the read-only, enabled-only view.

    Isolation rule: one strategy identity owns one portfolio. Two different
    strategies may never share a ``portfolio_id``, so experiments cannot leak
    mutable portfolio/account state into each other.
    """

    def __init__(self, specs: Iterable[StrategySpec] = ()):
        self._specs: dict[tuple[str, str], StrategySpec] = {}
        self._portfolios: dict[str, str] = {}
        for spec in specs:
            self.register(spec)

    def register(self, spec: StrategySpec) -> None:
        if not isinstance(spec, StrategySpec):
            raise StrategyExperimentError("spec must be a StrategySpec", code="invalid_strategy")
        spec.validate()
        key = spec.registry_key
        if key in self._specs:
            raise StrategyExperimentError(
                f"duplicate strategy {key[0]}@{key[1]}", code="duplicate_strategy"
            )
        owner = self._portfolios.get(spec.portfolio_id)
        if owner is not None and owner != spec.strategy_id:
            raise StrategyExperimentError(
                f"portfolio_id {spec.portfolio_id!r} is already owned by {owner!r}; "
                "strategies must not share mutable portfolio state",
                code="portfolio_isolation_violation",
            )
        self._portfolios[spec.portfolio_id] = spec.strategy_id
        self._specs[key] = spec

    def get(self, strategy_id: str, strategy_version: str) -> StrategySpec:
        key = (strategy_id, strategy_version)
        if key not in self._specs:
            raise StrategyExperimentError(
                f"unknown strategy {strategy_id}@{strategy_version}", code="unknown_strategy"
            )
        return self._specs[key]

    def versions(self, strategy_id: str) -> list[str]:
        return sorted(version for (sid, version) in self._specs if sid == strategy_id)

    def list(self) -> list[dict[str, Any]]:
        return [self._specs[key].to_dict() for key in sorted(self._specs)]

    def portfolios(self) -> dict[str, str]:
        return dict(self._portfolios)

    def export_enabled(self) -> StrategyRegistry:
        """Project enabled specs into the shared v0.6 registry (empty for now)."""
        registry = StrategyRegistry()
        latest: dict[str, StrategySpec] = {}
        for spec in self._specs.values():
            if not spec.enabled:
                continue
            current = latest.get(spec.strategy_id)
            if current is None or spec.strategy_version > current.strategy_version:
                latest[spec.strategy_id] = spec
        for spec in sorted(latest.values(), key=lambda item: item.strategy_id):
            registry.register(spec.as_definition())
        return registry


# --------------------------------------------------------------------------- #
# neutral comparison
# --------------------------------------------------------------------------- #
def compare_experiments(
    reports: Sequence[ExperimentReport],
    *,
    baseline_strategy_id: str | None = None,
) -> dict[str, Any]:
    """Side-by-side comparison of IS/OOS results. Never ranks or selects."""
    if not reports:
        raise StrategyExperimentError("at least one report is required", code="no_reports")
    if baseline_strategy_id is not None and not any(
        report.strategy_id == baseline_strategy_id for report in reports
    ):
        raise StrategyExperimentError(
            "baseline_strategy_id is not among the supplied reports", code="unknown_baseline"
        )
    rows: list[dict[str, Any]] = []
    for report in reports:
        if not isinstance(report, ExperimentReport):
            raise StrategyExperimentError("reports must be ExperimentReport instances", code="invalid_report")
        baseline = None
        if baseline_strategy_id is not None and report.strategy_id == baseline_strategy_id:
            baseline = report
        rows.append({
            "experiment_id": report.experiment_id,
            "strategy_id": report.strategy_id,
            "strategy_version": report.strategy_version,
            "asset_class": report.asset_class,
            "symbol": report.symbol,
            "status": report.status,
            "portfolio_id": report.portfolio_id,
            "fee_bps_per_side": report.costs["fee_bps_per_side"],
            "slippage_bps_per_side": report.costs["slippage_bps_per_side"],
            "is_total_return": None if report.is_metrics is None else report.is_metrics["total_return"],
            "oos_total_return": None if report.oos_metrics is None else report.oos_metrics["total_return"],
            "is_max_drawdown": None if report.is_metrics is None else report.is_metrics["max_drawdown"],
            "oos_max_drawdown": None if report.oos_metrics is None else report.oos_metrics["max_drawdown"],
            "excess_return_is": report.excess_return_is,
            "excess_return_oos": report.excess_return_oos,
            "reproducibility_key": report.reproducibility_key,
            "experimental_only": True,
            "eligible_for_trading": False,
            "orders_enabled": False,
            "action": "no_trade",
        })
    return {
        "schema_version": EXPERIMENT_SCHEMA_VERSION,
        "comparison_policy": COMPARISON_POLICY,
        "selection_performed": False,
        "baseline_strategy_id": baseline_strategy_id,
        "rows": rows,
        "action": "no_trade",
        "eligible_for_trading": False,
        "orders_enabled": False,
        "experimental_only": True,
        "notes": [
            "Comparison only; no ranking, scoring, promotion or parameter selection is applied.",
            "A higher historical return is not evidence of a better strategy.",
            "Different asset classes and windows are not directly comparable even in one table.",
        ],
    }
