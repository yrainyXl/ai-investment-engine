"""One-shot, read-only investment research cycle.

It combines provider-sourced local facts, deterministic hypothesis evaluation,
and an evidence-gated decision *report*, never an order or a buy/sell instruction.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

from .financials import FinancialFactStore
from .hypotheses import (
    HypothesisDefinition, HypothesisError, HypothesisVariable, evaluate_hypothesis,
)
from .report_store import ReportStore


class ResearchCycleError(ValueError):
    pass


@dataclass(frozen=True)
class HypothesisFactBinding:
    """Bind a variable to a specific local report fact and expected semantics."""

    variable_id: str
    fact_id: str
    expected_metric: str
    expected_period_type: str
    expected_statement_scope: str


@dataclass(frozen=True)
class ResearchHypothesis:
    definition: HypothesisDefinition
    bindings: tuple[HypothesisFactBinding, ...]


def _as_of_datetime(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ResearchCycleError("as_of must have a timezone")
    utc = value.astimezone(timezone.utc)
    if utc > datetime.now(timezone.utc):
        raise ResearchCycleError("as_of cannot be in the future")
    return utc


def _evaluate_bound_hypothesis(
    *,
    store: FinancialFactStore,
    entity_id: str,
    as_of: datetime,
    spec: ResearchHypothesis,
) -> dict[str, Any]:
    definition = spec.definition
    definition.validate()
    if definition.entity_id != entity_id:
        raise ResearchCycleError("hypothesis entity_id must match research asset")
    if len({binding.variable_id for binding in spec.bindings}) != len(spec.bindings):
        raise ResearchCycleError("duplicate variable binding")
    bindings = {binding.variable_id: binding for binding in spec.bindings}
    inputs: dict[str, HypothesisVariable] = {}
    for variable_id in definition.variables:
        binding = bindings.get(variable_id)
        if binding is None:
            continue  # evaluate_hypothesis reports missing required variables.
        fact = store.get_by_id(binding.fact_id, as_of=as_of)
        if fact is None:
            continue  # Unknown/unpublished fact => not_evaluable, never a fake fact.
        if str(fact.get("symbol")) != entity_id:
            raise ResearchCycleError("fact belongs to another asset")
        for name, expected, actual in (
            ("metric", binding.expected_metric, fact.get("metric")),
            ("period_type", binding.expected_period_type, fact.get("period_type")),
            ("statement_scope", binding.expected_statement_scope, fact.get("statement_scope")),
        ):
            if str(actual) != expected:
                raise ResearchCycleError(f"fact {binding.fact_id} does not match expected {name}")
        period_end = str(fact["period_end"])
        inputs[variable_id] = HypothesisVariable(
            variable_id=variable_id,
            metric_id=str(fact["metric"]),
            value=str(fact["value"]),
            unit=str(fact["currency"]),
            period_type=str(fact["period_type"]),
            period_start=str(fact["period_start"]),
            period_end=period_end,
            source_id=str(fact["fact_id"]),
            published_at=str(fact["published_at"]),
            effective_at=f"{period_end}T23:59:59+08:00",
            retrieved_at=str(fact["retrieved_at"]),
            reviewed_by_human=fact.get("reviewed_by_human") is True,
            reviewed_at=fact.get("reviewed_at"),
            unit_verified=fact.get("unit_verified") is True,
            scope_verified=fact.get("scope_verified") is True,
        )
    return evaluate_hypothesis(definition, inputs, as_of=as_of.isoformat()).to_dict()


def run_research_cycle(
    *,
    root: str | Path,
    entity_id: str,
    asset_class: str,
    as_of: datetime,
    hypotheses: Sequence[ResearchHypothesis] = (),
    persist_report: bool = True,
) -> dict[str, Any]:
    """Run one reproducible snapshot. No real holdings or orders are accessed.

    This initial E2E iteration has *no verified portfolio resolver*: risk and
    valuation remain explicitly missing until sourced, never assumed valid.
    """
    if not entity_id or not asset_class:
        raise ResearchCycleError("entity_id and asset_class are required")
    cutoff = _as_of_datetime(as_of)

    # Separate worker-owned pure modules. Only import them within execution so
    # the integration branch can be developed before both worker branches land.
    from .research_context import build_research_context
    from .decision_report import synthesize_decision_report

    context = build_research_context(
        root=root, entity_id=entity_id, asset_class=asset_class, as_of=cutoff,
    )
    if str(context.get("entity_id")) != entity_id:
        raise ResearchCycleError("context asset ID is inconsistent")

    fact_store = FinancialFactStore(root)
    evaluations: list[dict[str, Any]] = []
    for spec in hypotheses:
        try:
            evaluations.append(_evaluate_bound_hypothesis(
                store=fact_store, entity_id=entity_id, as_of=cutoff, spec=spec,
            ))
        except HypothesisError as exc:
            raise ResearchCycleError(str(exc)) from exc

    decision = synthesize_decision_report(
        context=context, hypothesis_results=evaluations,
        risk_report=None, valuation_result=None,
    )
    # The public API intentionally cannot wire a hypothetical risk result in as
    # verified live portfolio risk. That requires a separate read-only adapter.
    if decision.get("eligible_for_trading") is not False or decision.get("orders_enabled") is not False:
        raise ResearchCycleError("unsafe decision report: trade eligibility must remain disabled")
    if decision.get("action") != "no_trade":
        raise ResearchCycleError("unsafe decision report: action must remain no_trade")

    payload: dict[str, Any] = {
        "schema_version": "1",
        "entity_id": entity_id,
        "asset_class": asset_class,
        "as_of": cutoff.isoformat(),
        "context": context,
        "hypothesis_results": evaluations,
        "risk_report": None,
        "valuation_result": None,
        "decision_report": decision,
        "orders_enabled": False,
        "eligible_for_trading": False,
        "stage": "read_only_research",
    }
    if persist_report:
        payload["audit"] = ReportStore(root).save("research_cycle", "assessment", payload)
    return payload
