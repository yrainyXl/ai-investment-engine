"""V07 integration acceptance: M5 agent runtime + M4 strategy experiments.

The two worker modules are unit-tested in their own worktrees.  This file only
checks the integration seam on the same v0.6 base tree:

* both modules import from the same checkout and expose no order/OTW surface;
* both can consume the same point-in-time 510300 ETF research dataset;
* both emit the fixed research-only output (``action="no_trade"``,
  ``eligible_for_trading=False``, ``orders_enabled=False``) and neither output
  can be read as a trading decision.

All fixtures are synthetic.  The model client is a local scripted stub; no
network, OTW write or order path is exercised.
"""
from __future__ import annotations

import json
import math
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

import investment_engine.agent_runtime as agent_runtime
import investment_engine.strategy_experiments as strategy_experiments
from investment_engine.agent_runtime import (
    ROLE_ORDER,
    AgentRuntimeConfig,
    ModelResponse,
    RolePolicy,
    run_research_agents,
)
from investment_engine.market_data import Bar
from investment_engine.strategy_experiments import (
    ExperimentSpec,
    StrategyExperimentError,
    StrategySpec,
    expected_dataset_id,
    run_experiment,
)

AS_OF = datetime(2026, 11, 12, 10, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
NOW = datetime(2026, 12, 1, 0, 0, tzinfo=timezone.utc)
SYMBOL = "510300"
IS_WINDOW = (date(2025, 1, 1), date(2025, 8, 31))
OOS_WINDOW = (date(2025, 9, 1), date(2025, 12, 31))
FIXTURE_SOURCE = "test.v07.integration.fixture.v1"

SAFE_FIELDS = {
    "action": "no_trade",
    "eligible_for_trading": False,
    "orders_enabled": False,
}


# --------------------------------------------------------------------------- #
# synthetic point-in-time dataset for the same 510300 ETF
# --------------------------------------------------------------------------- #
def _etf_bars(count: int = 360) -> list[Bar]:
    rows: list[Bar] = []
    start = date(2025, 1, 1)
    for index in range(count):
        close = round(3.8 + 0.01 * index + 0.25 * math.sin(index / 5.0), 4)
        open_price = round(close - 0.02, 4)
        rows.append(
            Bar(
                symbol=SYMBOL,
                exchange="SSE",
                asset_type="etf",
                interval="1d",
                timestamp=(start + timedelta(days=index)).isoformat(),
                open=open_price,
                high=round(max(open_price, close) + 0.08, 4),
                low=round(min(open_price, close) - 0.08, 4),
                close=close,
                volume=float(1000 + index),
                amount=float(10000 + index),
                source=FIXTURE_SOURCE,
                fetched_at="2026-11-12T00:00:00+00:00",
                adjustment="qfq",
                currency="CNY",
                volume_unit="lots",
            )
        )
    return rows


def _etf_observation() -> dict:
    return {
        "observation_id": "obs-510300-nav",
        "series_id": f"cn.etf.{SYMBOL}.nav",
        "subject": f"Fixture ETF {SYMBOL} unit NAV",
        "metric": "unit_nav",
        "value": 4.12,
        "unit": "CNY/share",
        "effective_at": "2026-09-30T00:00:00+08:00",
        "published_at": "2026-10-08T10:00:00+08:00",
        "retrieved_at": "2026-10-09T10:00:00+08:00",
        "source_name": "test.etf.source",
        "source_url": f"https://example.com/{SYMBOL}/nav",
        "evidence_type": "confirmed_fact",
        "assessment": "supporting",
        "reviewed_by_human": True,
        "unit_verified": True,
        "eligible_for_assessment": True,
        "detail": "Synthetic ETF NAV fixture; not a live quote.",
    }


def _etf_context() -> dict:
    return {
        "schema_version": "1",
        "entity_id": SYMBOL,
        "asset_class": "etf",
        "as_of": AS_OF.isoformat(),
        "data_status": "available",
        "financial_facts": [],
        "industry_observations": [_etf_observation()],
        "missing_evidence": [],
        "provenance_warnings": [],
    }


def _strategy() -> StrategySpec:
    return StrategySpec(
        strategy_id="sma_trend_cn_etf_v07",
        strategy_version="1.0.0",
        name="SMA trend baseline (V07 ETF)",
        asset_class="etf",
        parameters={"fast_period": 10, "slow_period": 30},
        dataset_id=expected_dataset_id(SYMBOL, "etf", "qfq"),
        risk_policy_id="risk_long_only_etf_v1",
        portfolio_id="portfolio_cn_etf_research",
        implementation="sma_trend",
        parameter_schema={
            "fast_period": {"type": "int", "required": True, "minimum": 2},
            "slow_period": {"type": "int", "required": True, "minimum": 3},
        },
    )


def _experiment(**overrides) -> ExperimentSpec:
    payload = {
        "experiment_id": "exp_v07_integration_etf",
        "strategy": _strategy(),
        "symbol": SYMBOL,
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


class _StubClient:
    """Scripted provider stub; one grounded payload per role, in ROLE_ORDER."""

    def __init__(self) -> None:
        self.requests = []

    def complete(self, request):
        self.requests.append(request)
        verdict = {
            "research": "supportive",
            "challenger": "skeptical",
            "risk_review": "mixed",
        }[request.role]
        payload = {
            "role": request.role,
            "verdict": verdict,
            "summary": f"{request.role} summary from the synthetic NAV fixture",
            "supporting": [{"statement": "synthetic NAV fact", "source_ids": ["obs-510300-nav"]}],
            "contradicting": (
                [{"statement": "single synthetic fixture; no live check",
                  "source_ids": ["obs-510300-nav"]}]
                if verdict == "skeptical"
                else []
            ),
            "key_gaps": ["synthetic fixture only; no live data"],
            "confidence": 0.4,
        }
        return ModelResponse(
            text=json.dumps(payload),
            model=request.model,
            model_version="2026-11-01",
            usage={"input_tokens": 100, "output_tokens": 40},
            latency_ms=1.0,
        )


def _runtime_config() -> AgentRuntimeConfig:
    def policy(role: str) -> RolePolicy:
        return RolePolicy(
            role=role,
            model=f"stub-{role}",
            prompt_version=f"prompt-{role}-v1",
            input_cost_per_million="1",
            output_cost_per_million="2",
        )

    return AgentRuntimeConfig(roles=tuple(policy(role) for role in ROLE_ORDER))


_UNSET = object()


def _run_agents(client=_UNSET) -> dict:
    return run_research_agents(
        context=_etf_context(),
        hypothesis_results=[],
        client=_StubClient() if client is _UNSET else client,
        config=_runtime_config(),
    )


# --------------------------------------------------------------------------- #
# integration assertions
# --------------------------------------------------------------------------- #
def test_both_modules_import_from_the_same_v06_tree():
    agent_dir = Path(agent_runtime.__file__).resolve().parent
    experiment_dir = Path(strategy_experiments.__file__).resolve().parent
    assert agent_dir == experiment_dir
    assert agent_runtime.__name__ == "investment_engine.agent_runtime"
    assert strategy_experiments.__name__ == "investment_engine.strategy_experiments"


def test_both_modules_expose_no_order_or_otw_write_surface():
    for module in (agent_runtime, strategy_experiments):
        names = {name.lower() for name in dir(module)}
        for forbidden in (
            "place_order",
            "submit_order",
            "create_order",
            "otw_write",
            "execute_trade",
            "send_order",
        ):
            assert forbidden not in names, f"{module.__name__} exposes {forbidden}"
        source = Path(module.__file__).read_text(encoding="utf-8")
        assert "otw_write" not in source
        assert "import requests" not in source
        assert "urllib.request" not in source


def test_same_etf_dataset_runs_through_both_modules_with_safe_outputs():
    bars = _etf_bars()
    report = run_experiment(_experiment(), bars, now=NOW)
    agent_result = _run_agents()

    payload = report.to_dict()
    assert payload["symbol"] == SYMBOL
    assert payload["asset_class"] == "etf"
    assert payload["status"] == "completed"
    assert payload["experimental_only"] is True
    assert payload["optimization_performed"] is False
    assert payload["tradability"] == "not_tradable"
    for key, value in SAFE_FIELDS.items():
        assert payload[key] == value

    assert agent_result["entity_id"] == SYMBOL
    assert agent_result["asset_class"] == "etf"
    assert agent_result["status"] == "completed"
    assert agent_result["human_review_required"] is True
    assert agent_result["auto_approval"] is False
    for key, value in SAFE_FIELDS.items():
        assert agent_result[key] == value
    assert agent_result["policy"]["research_only"] is True
    assert agent_result["policy"]["trading_path_available"] is False


def test_neither_output_can_be_read_as_a_trade_decision():
    report = run_experiment(_experiment(), _etf_bars(), now=NOW)
    agent_result = _run_agents()

    # The agent module's own forbidden-key list must not appear in its output,
    # and the experiment report must not contain order/position fields either.
    trading_keys = {
        "recommended_action", "recommendation_action", "trade", "trade_decision",
        "trade_recommendation", "trading_action", "buy", "sell", "long", "short",
        "order", "orders", "order_type", "order_side", "side", "quantity",
        "position", "position_size", "position_sizing", "target_position",
        "target_price", "entry_price", "stop_loss", "take_profit",
        "execute", "execution",
    }
    assert trading_keys <= set(agent_runtime.FORBIDDEN_OUTPUT_KEYS)
    forbidden = trading_keys

    def walk(value, path="$"):
        if isinstance(value, dict):
            for key, item in value.items():
                lowered = str(key).lower()
                if lowered == "action":
                    # The fixed safety field is expected; it must never be
                    # anything other than the research-only no_trade value.
                    assert item == "no_trade", f"{path}.{key} must stay no_trade"
                else:
                    assert lowered not in forbidden, f"forbidden key {path}.{key!r}"
                walk(item, f"{path}.{key}")
        elif isinstance(value, list):
            for index, item in enumerate(value):
                walk(item, f"{path}[{index}]")

    walk(agent_result)
    experiment_payload = report.to_dict()
    for key in ("order", "orders", "quantity", "position_size", "recommendation"):
        assert key not in experiment_payload
    assert "not a prediction, recommendation" in experiment_payload["disclaimer"].lower()


def test_agent_runtime_fails_closed_without_a_model_client():
    result = _run_agents(client=None)
    assert result["status"] in {"unavailable", "insufficient_evidence"}
    for key, value in SAFE_FIELDS.items():
        assert result[key] == value
    assert result["human_review_required"] is True
    assert result["auto_approval"] is False
    assert result["model_audit"]["totals"]["calls"] == 0


def test_experiment_fails_closed_on_lookahead_as_of():
    spec = _experiment(as_of=datetime(2027, 1, 1, 0, 0, tzinfo=timezone.utc))
    with pytest.raises(StrategyExperimentError):
        run_experiment(spec, _etf_bars(), now=NOW)
