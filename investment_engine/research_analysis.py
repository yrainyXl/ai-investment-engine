"""Unified, authenticated-entry-point research orchestration and audit payload."""
from __future__ import annotations

from dataclasses import asdict, replace
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Sequence
from zoneinfo import ZoneInfo

from . import release_metadata
from .agent_execution import execute_agents
from .decision_report import synthesize_decision_report
from .e2e_pipeline import (
    HypothesisFactBinding, ResearchHypothesis, _as_of_datetime, _evaluate_bound_hypothesis,
)
from .hypotheses import HypothesisDefinition
from .market_data import Bar
from .report_store import ReportStore
from .research_inputs import normalize_research_symbol, resolve_research_inputs
from .research_portfolio import portfolio_risk
from .strategy_experiments import (
    ExperimentSpec, StrategyExperimentError, StrategyExperimentRegistry, StrategySpec,
    dataset_fingerprint, expected_dataset_id, run_experiment,
)

NO_TRADE = {"action": "no_trade", "orders_enabled": False, "eligible_for_trading": False}
_PROFIT_METRIC = "net_profit_attributable"


def _profit_hypothesis(context: dict) -> ResearchHypothesis:
    entity = context["entity_id"]
    candidates = [
        fact for fact in context["financial_facts"]
        if fact.get("metric") == _PROFIT_METRIC
        and fact.get("period_type") == "quarter"
        and fact.get("statement_scope") == "consolidated"
        and fact.get("eligible_for_assessment") is True
    ]
    bindings = ()
    if candidates:
        latest = max(candidates, key=lambda fact: (fact["period_end"], fact["published_at"]))
        bindings = (HypothesisFactBinding(
            "quarter_profit", latest["fact_id"], _PROFIT_METRIC, "quarter", "consolidated",
        ),)
    return ResearchHypothesis(
        HypothesisDefinition(
            hypothesis_id="nonnegative_quarterly_profit.v1", entity_id=entity,
            strategy_id="fundamental_research", claim="Latest reviewed quarterly attributable profit is nonnegative.",
            operation="sum", variables=("quarter_profit",), target="0",
            target_unit="CNY", direction="above", max_age_days=180,
        ),
        bindings,
    )


def _etf_hypothesis(context: dict, bars: Sequence[Bar]) -> dict:
    window = bars[-6:]
    result = {
        "hypothesis_id": "positive_five_session_close_change.v1",
        "entity_id": context["entity_id"],
        "as_of": context["as_of"],
        "claim": "The latest unadjusted ETF close exceeds the close five stored sessions earlier.",
        "target": "0", "target_unit": "CNY", "direction": "above",
        "computed_value": None, "status": "not_evaluable", "reasons": ["six_qualified_daily_bars_required"],
    }
    if len(window) == 6:
        change = Decimal(str(window[-1].close)) - Decimal(str(window[0].close))
        result.update(
            computed_value=str(change), status="supported" if change > 0 else "contradicted",
            reasons=[], dataset_fingerprint=dataset_fingerprint(window),
            input_source_ids=[f"{bar.source}:{bar.symbol}:{bar.timestamp}" for bar in window],
        )
    return result


def _experiment(
    *, entity_id: str, asset_class: str, as_of: datetime, bars: Sequence[Bar],
    windows: tuple[date, date, date, date] | None,
) -> dict:
    if windows is None:
        return {"status": "not_requested", "reasons": ["declare_is_and_oos_windows"], **NO_TRADE}
    market_cutoff = as_of.astimezone(ZoneInfo("Asia/Shanghai"))
    if windows[3] > market_cutoff.date():
        raise StrategyExperimentError("out-of-sample end cannot be after as_of", code="lookahead_window")
    strategy = StrategySpec(
        strategy_id="sma_trend", strategy_version="1.0.0", name="SMA trend (20/60)",
        asset_class=asset_class, parameters={"fast_period": 20, "slow_period": 60},
        parameter_schema={
            "fast_period": {"type": "int", "required": True, "minimum": 2},
            "slow_period": {"type": "int", "required": True, "minimum": 3},
        },
        dataset_id=expected_dataset_id(entity_id, asset_class, "none"),
        risk_policy_id="research_only", portfolio_id="sma_trend_research",
    )
    registry = StrategyExperimentRegistry((strategy,))
    spec = ExperimentSpec(
        experiment_id="sma_trend.v1", strategy=registry.get("sma_trend", "1.0.0"),
        symbol=entity_id, asset_class=asset_class, adjustment="none", interval="1d",
        is_start=windows[0], is_end=windows[1], oos_start=windows[2], oos_end=windows[3],
        as_of=market_cutoff, fee_bps=3, slippage_bps=5,
    )
    try:
        return run_experiment(spec, bars).to_dict()
    except StrategyExperimentError as exc:
        return {"status": "not_evaluable", "reasons": [exc.code], **NO_TRADE}


def analyze_research(
    *, root: str | Path, entity_id: str, asset_class: str, as_of: datetime | None,
    hypotheses: Sequence[ResearchHypothesis] = (),
    experiment_windows: tuple[date, date, date, date] | None = None,
) -> dict:
    requested_entity_id = entity_id
    entity_id = normalize_research_symbol(entity_id)
    cutoff = _as_of_datetime(as_of) if as_of is not None else None
    risk = portfolio_risk(as_of=cutoff, entity_id=entity_id, asset_class=asset_class)
    if cutoff is None:
        cutoff = _as_of_datetime(datetime.fromisoformat(risk["as_of"]))
    inputs = resolve_research_inputs(root=root, entity_id=entity_id, asset_class=asset_class, as_of=cutoff)
    context = inputs["context"]
    entity_id = context["entity_id"]
    bars = inputs["bars"]
    valuation = inputs["valuation"]
    # Keep the exact experiment input with the report for later reproduction.
    experiment_bars = []
    if experiment_windows is not None:
        start, _, _, end = experiment_windows
        experiment_bars = [bar for bar in bars if start <= date.fromisoformat(bar.timestamp[:10]) <= end]
    experiment = _experiment(
        entity_id=entity_id, asset_class=asset_class, as_of=cutoff, bars=experiment_bars, windows=experiment_windows,
    )
    specs = [
        replace(spec, definition=replace(spec.definition, entity_id=entity_id))
        if normalize_research_symbol(spec.definition.entity_id) == entity_id else spec
        for spec in hypotheses
    ]
    if not specs and asset_class == "stock":
        specs.append(_profit_hypothesis(context))
    facts_by_id = {
        fact["fact_id"]: fact for fact in context["financial_facts"]
        if fact.get("unit") == fact.get("currency") and fact.get("value_scale") == "yuan"
    }
    evaluations = [
        {
            **_evaluate_bound_hypothesis(
                lookup_fact=facts_by_id.get, entity_id=entity_id, as_of=cutoff, spec=spec,
            ),
            "claim": spec.definition.claim,
        }
        for spec in specs
    ]
    if not evaluations and asset_class == "etf":
        current_bars = bars if inputs["market_data"]["status"] == "available" else []
        evaluations = [_etf_hypothesis(context, current_bars)]
    agent_context = context
    if asset_class == "stock" and not any(
        fact.get("eligible_for_assessment") is True for fact in context["financial_facts"]
    ):
        # A stock cannot advance to model conclusions on industry context alone.
        agent_context = {**context, "industry_observations": []}
    agents = execute_agents(agent_context, hypotheses=evaluations if not hypotheses else ())
    if experiment_bars:
        experiment["input_bars"] = [asdict(bar) for bar in experiment_bars]
    gate_risk = {
        **risk,
        "status": (
            "assessed"
            if risk.get("status") == "assessed"
            or (risk.get("status") == "partial" and risk.get("limit_breaches"))
            else "not_evaluable"
        ),
    }
    decision = synthesize_decision_report(
        context=context, hypothesis_results=evaluations, risk_report=gate_risk, valuation_result=valuation,
    )
    status = decision["decision_status"]
    if status == "review_ready" and agents["status"] != "completed":
        status = "insufficient_evidence"
    report = {
        "schema_version": "1", "release": release_metadata(), "status": status,
        "entity_id": entity_id, "requested_entity_id": requested_entity_id,
        "asset_class": asset_class, "as_of": cutoff.isoformat(),
        "context": context,
        "market_data": inputs.get("market_data"),
        "disclosures": inputs["disclosures"],
        "confirmed_facts": inputs["confirmed_facts"],
        "market_expectations": inputs["market_expectations"],
        "hypothesis_results": evaluations, "agents": agents, "valuation": valuation,
        "portfolio_risk": risk, "strategy_experiment": experiment, "decision_report": decision,
        "open_questions": {
            "decision": decision["missing_evidence"],
            "agents": agents.get("missing_evidence", []),
            "portfolio_risk": risk.get("reasons", []),
            "strategy_experiment": experiment.get("not_evaluable_reasons", experiment.get("reasons", [])),
        },
        **NO_TRADE,
    }
    audit = ReportStore(root).save("research_analysis", entity_id, report)
    return {**report, "audit": audit}
