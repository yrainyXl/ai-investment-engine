"""Synthetic-fixture tests for decision-report synthesis.

Every input below is synthesized from the project's own pure modules or from
literal fixtures. Nothing here is live market data, a verified holding or an
order. Two assets are covered (a rubber stock and a broad-index ETF) so the
aggregator is not tailored to one industry.
"""
import json

import pytest

from investment_engine.decision_report import (
    DecisionReportError,
    synthesize_decision_report,
)
from investment_engine.hypotheses import (
    HypothesisDefinition,
    HypothesisVariable,
    evaluate_hypothesis,
)
from investment_engine.risk import (
    HypotheticalPosition,
    RiskLimits,
    assess_hypothetical_risk,
)
from investment_engine.valuation import (
    ValuationMetric,
    calculate_multiple_valuation,
)

AS_OF = "2026-11-10T09:30:00+08:00"


def variable(variable_id, value, *, reviewed=True):
    return HypothesisVariable(
        variable_id=variable_id,
        metric_id=variable_id,
        value=value,
        unit="CNY",
        period_type="year_to_date",
        period_start="2026-01-01",
        period_end="2026-09-30",
        source_id=f"synthetic:{variable_id}",
        published_at="2026-10-29T18:00:00+08:00",
        effective_at="2026-09-30T16:00:00+08:00",
        retrieved_at="2026-10-30T09:00:00+08:00",
        reviewed_by_human=reviewed,
        reviewed_at="2026-10-30T09:00:00+08:00" if reviewed else None,
        unit_verified=True,
        scope_verified=True,
    )


def hypothesis_result(*, entity_id="601118", status="supported"):
    """Produce a real ``EvaluationResult.to_dict()`` for the requested status."""
    definition = HypothesisDefinition(
        hypothesis_id="rubber-margin-v1",
        entity_id=entity_id,
        strategy_id="synthetic-rubber-v1",
        claim="Synthetic gross-margin thesis for fixture use only",
        operation="ratio",
        variables=("gross_profit", "revenue"),
        target="0.20",
        target_unit="ratio",
        direction="above",
    )
    inputs = {
        "gross_profit": variable("gross_profit", "60"),
        "revenue": variable("revenue", "240"),
    }
    if status == "not_evaluable":
        inputs["gross_profit"] = variable("gross_profit", "60", reviewed=False)
    elif status == "contradicted":
        inputs["gross_profit"] = variable("gross_profit", "24")
    result = evaluate_hypothesis(definition, inputs, as_of=AS_OF)
    assert result.status == status
    return result.to_dict()


def fact(*, fact_id="fact:revenue:2026q3", reviewed=True, eligible=True):
    return {
        "fact_id": fact_id,
        "metric": "revenue",
        "value": "2400000000",
        "currency": "CNY",
        "period_type": "year_to_date",
        "period_start": "2026-01-01",
        "period_end": "2026-09-30",
        "source_url": "https://example.invalid/synthetic/601118-2026q3",
        "published_at": "2026-10-29T18:00:00+08:00",
        "retrieved_at": "2026-10-30T09:00:00+08:00",
        "reviewed_at": "2026-11-02T10:00:00+08:00" if reviewed else None,
        "eligible_for_assessment": eligible,
        "eligibility_reason": "synthetic fixture",
    }


def context(
    *,
    entity_id="601118",
    asset_class="stock",
    data_status="available",
    facts=None,
    missing=(),
    warnings=(),
):
    return {
        "schema_version": "1",
        "entity_id": entity_id,
        "asset_class": asset_class,
        "as_of": AS_OF,
        "data_status": data_status,
        "financial_facts": [fact()] if facts is None else facts,
        "industry_observations": [],
        "missing_evidence": list(missing),
        "provenance_warnings": list(warnings),
    }


def valuation_result(*, metric_id="eps:601118", value="0.42", multiple="12", entity_id=None):
    metric = ValuationMetric(
        metric_id=metric_id,
        value=value,
        unit="CNY/share",
        period_end="2026-09-30",
        source_id=f"synthetic:{metric_id}",
        published_at="2026-10-29T18:00:00+08:00",
        effective_at="2026-09-30T16:00:00+08:00",
        retrieved_at="2026-10-30T09:00:00+08:00",
        reviewed_by_human=True,
        reviewed_at="2026-11-02T10:00:00+08:00",
        unit_verified=True,
        scope_verified=True,
    )
    payload = calculate_multiple_valuation(
        metric, multiple_name="pe", multiple=multiple, as_of=AS_OF
    ).to_dict()
    if entity_id is not None:
        payload["entity_id"] = entity_id
    return payload


def risk_report(*, breach=False):
    if breach:
        positions = (HypotheticalPosition("601118", "stock", "rubber", "300"),)
        cash = "700"
    else:
        positions = (HypotheticalPosition("601118", "stock", "rubber", "150"),)
        cash = "850"
    return assess_hypothetical_risk(
        "synthetic-decision-portfolio",
        positions,
        cash=cash,
        currency="CNY",
        as_of="2026-11-09",
        limits=RiskLimits(max_single_weight="0.20", max_sector_weight="0.40"),
    ).to_dict()


def report(**overrides):
    args = {
        "context": context(),
        "hypothesis_results": [hypothesis_result()],
        "risk_report": risk_report(),
        "valuation_result": valuation_result(),
    }
    args.update(overrides)
    return synthesize_decision_report(**args)


def test_review_ready_for_reviewed_stock_fixture_using_real_module_outputs():
    result = report()

    assert result["schema_version"] == "1"
    assert result["entity_id"] == "601118"
    assert result["as_of"] == AS_OF
    assert result["decision_status"] == "review_ready"
    assert result["action"] == "no_trade"
    assert result["orders_enabled"] is False
    assert result["eligible_for_trading"] is False
    assert [item["hypothesis_id"] for item in result["supporting_factors"]] == [
        "rubber-margin-v1"
    ]
    assert result["contradicting_factors"] == []
    assert result["missing_evidence"] == []
    assert result["risk_flags"] == []
    assert any("ready for human review" in line for line in result["rationale"])
    json.dumps(result)


def test_review_ready_for_second_asset_etf_fixture():
    etf_positions = (HypotheticalPosition("510300", "etf", "broad_index", "150"),)
    etf_risk = assess_hypothetical_risk(
        "synthetic-etf-portfolio",
        etf_positions,
        cash="850",
        currency="CNY",
        as_of="2026-11-09",
        limits=RiskLimits(),
    ).to_dict()

    result = synthesize_decision_report(
        context=context(entity_id="510300", asset_class="etf"),
        hypothesis_results=[hypothesis_result(entity_id="510300")],
        risk_report=etf_risk,
        valuation_result=valuation_result(
            metric_id="nav:510300", value="3.75", multiple="1", entity_id="510300"
        ),
    )

    assert result["entity_id"] == "510300"
    assert result["decision_status"] == "review_ready"
    assert result["action"] == "no_trade"
    assert len(result["supporting_factors"]) == 1


def test_unavailable_context_is_insufficient_evidence():
    result = report(context=context(data_status="unavailable", facts=[]))

    assert result["decision_status"] == "insufficient_evidence"
    assert "context.data_unavailable" in result["missing_evidence"]
    assert "reviewed_financial_fact" in result["missing_evidence"]


def test_unreviewed_financial_fact_blocks_review_ready():
    result = report(context=context(facts=[fact(reviewed=False, eligible=False)]))

    assert result["decision_status"] == "insufficient_evidence"
    assert "reviewed_financial_fact" in result["missing_evidence"]


def test_not_evaluable_hypothesis_is_insufficient_evidence():
    result = report(hypothesis_results=[hypothesis_result(status="not_evaluable")])

    assert result["decision_status"] == "insufficient_evidence"
    assert "hypothesis_not_evaluable:rubber-margin-v1" in result["missing_evidence"]
    assert result["supporting_factors"] == []
    assert result["contradicting_factors"] == []


def test_missing_valuation_is_insufficient_evidence():
    result = report(valuation_result=None)

    assert result["decision_status"] == "insufficient_evidence"
    assert "valuation_missing" in result["missing_evidence"]


def test_not_evaluable_valuation_is_insufficient_evidence():
    result = report(valuation_result=valuation_result(value="0"))

    assert result["decision_status"] == "insufficient_evidence"
    assert "valuation_not_evaluable" in result["missing_evidence"]


def test_assessed_limit_breach_is_risk_blocked():
    result = report(risk_report=risk_report(breach=True))

    assert result["decision_status"] == "risk_blocked"
    assert "limit_breach:max_single_weight_exceeded" in result["risk_flags"]
    assert result["action"] == "no_trade"
    assert any("breached limits" in line for line in result["rationale"])


def test_not_evaluable_risk_report_is_insufficient_evidence():
    risk = risk_report()
    risk["status"] = "not_evaluable"
    result = report(risk_report=risk)

    assert result["decision_status"] == "insufficient_evidence"
    assert "risk_not_evaluable" in result["missing_evidence"]
    assert "risk_not_evaluable" in result["risk_flags"]


def test_risk_breach_outranks_other_evidence_gaps():
    result = report(risk_report=risk_report(breach=True), valuation_result=None)

    assert result["decision_status"] == "risk_blocked"
    assert "valuation_missing" in result["missing_evidence"]
    assert any("Evidence gaps also remain" in line for line in result["rationale"])


def test_contradicting_evidence_is_reported_without_enabling_trading():
    result = report(hypothesis_results=[hypothesis_result(status="contradicted")])

    assert result["decision_status"] == "review_ready"
    assert result["contradicting_factors"][0]["hypothesis_id"] == "rubber-margin-v1"
    assert result["supporting_factors"] == []
    assert any("Contradicting evidence" in line for line in result["rationale"])
    assert result["eligible_for_trading"] is False


def test_supported_result_with_pending_review_reasons_fails_closed():
    forged = hypothesis_result()
    forged["reasons"] = ["gross_profit: human review is pending"]

    result = report(hypothesis_results=[forged])

    assert result["decision_status"] == "insufficient_evidence"
    assert (
        "hypothesis_result_inconsistent:rubber-margin-v1" in result["missing_evidence"]
    )
    assert result["supporting_factors"] == []


def test_supported_result_without_computed_value_fails_closed():
    forged = hypothesis_result()
    forged["computed_value"] = None

    result = report(hypothesis_results=[forged])

    assert result["decision_status"] == "insufficient_evidence"
    assert (
        "hypothesis_result_inconsistent:rubber-margin-v1" in result["missing_evidence"]
    )
    assert result["supporting_factors"] == []


def test_fact_eligible_without_review_timestamp_fails_closed():
    result = report(context=context(facts=[fact(reviewed=False, eligible=True)]))

    assert result["decision_status"] == "insufficient_evidence"
    assert (
        "context.financial_fact_eligible_without_review:fact:revenue:2026q3"
        in result["missing_evidence"]
    )
    assert "reviewed_financial_fact" in result["missing_evidence"]


@pytest.mark.parametrize(
    "overrides, marker",
    [
        (
            {"hypothesis_results": [dict(hypothesis_result(), status="probably_true")]},
            "hypothesis_status_unknown",
        ),
        ({"context": context(data_status="verified")}, "context.data_status_unknown"),
        (
            {"valuation_result": dict(valuation_result(), status="estimated")},
            "valuation_status_unknown",
        ),
        ({"risk_report": dict(risk_report(), status="looks_fine")}, "risk_status_unknown"),
    ],
)
def test_unknown_statuses_fail_closed(overrides, marker):
    result = report(**overrides)

    assert result["decision_status"] == "insufficient_evidence"
    assert any(item.startswith(marker) for item in result["missing_evidence"])


def test_malformed_hypothesis_record_fails_closed():
    result = report(hypothesis_results=["not-a-mapping"])

    assert result["decision_status"] == "insufficient_evidence"
    assert "hypothesis_result_malformed:0" in result["missing_evidence"]


def test_malformed_limit_breaches_fail_closed():
    risk = risk_report(breach=True)
    risk["limit_breaches"] = "max_single_weight_exceeded"

    result = report(risk_report=risk)

    assert result["decision_status"] == "insufficient_evidence"
    assert "risk_limit_breaches_malformed" in result["missing_evidence"]


def test_risk_report_claiming_trading_eligibility_fails_closed():
    risk = risk_report()
    risk["eligible_for_trading"] = True

    result = report(risk_report=risk)

    assert result["decision_status"] == "insufficient_evidence"
    assert "risk_eligibility_inconsistent" in result["risk_flags"]


def test_hypothesis_for_another_entity_fails_closed():
    result = report(hypothesis_results=[hypothesis_result(entity_id="999999")])

    assert result["decision_status"] == "insufficient_evidence"
    assert any(
        item.startswith("hypothesis_entity_mismatch")
        for item in result["missing_evidence"]
    )


def test_partial_context_data_is_insufficient_evidence():
    result = report(context=context(data_status="partial"))

    assert result["decision_status"] == "insufficient_evidence"
    assert "context.data_partial" in result["missing_evidence"]


def test_context_declared_missing_evidence_forces_insufficient():
    result = report(context=context(missing=["industry_observations"]))

    assert result["decision_status"] == "insufficient_evidence"
    assert "industry_observations" in result["missing_evidence"]


def test_missing_hypothesis_results_is_insufficient_evidence():
    result = report(hypothesis_results=[])

    assert result["decision_status"] == "insufficient_evidence"
    assert "hypothesis_results" in result["missing_evidence"]


def test_provenance_warnings_are_surfaced_for_transparency():
    result = report(context=context(warnings=["synthetic fixture only"]))

    assert "provenance_warning:synthetic fixture only" in result["risk_flags"]


@pytest.mark.parametrize("broken", [None, [], "context"])
def test_non_mapping_context_raises(broken):
    with pytest.raises(DecisionReportError):
        synthesize_decision_report(
            context=broken, hypothesis_results=[], risk_report=None
        )


def test_missing_entity_or_timezone_naive_as_of_raises():
    with pytest.raises(DecisionReportError):
        synthesize_decision_report(
            context={"entity_id": "", "as_of": AS_OF},
            hypothesis_results=[],
            risk_report=None,
        )
    with pytest.raises(DecisionReportError):
        synthesize_decision_report(
            context={"entity_id": "601118", "as_of": "2026-11-10T09:30:00"},
            hypothesis_results=[],
            risk_report=None,
        )


def test_report_is_deterministic_and_json_round_trips():
    first = report()
    second = report()

    assert first == second
    assert json.loads(json.dumps(first)) == first


@pytest.mark.parametrize(
    "overrides",
    [
        {},
        {"context": context(data_status="unavailable", facts=[])},
        {"hypothesis_results": []},
        {"valuation_result": None},
        {"risk_report": None},
        {"risk_report": risk_report(breach=True)},
    ],
)
def test_no_combination_can_enable_trading(overrides):
    result = report(**overrides)

    assert result["action"] == "no_trade"
    assert result["orders_enabled"] is False
    assert result["eligible_for_trading"] is False
    assert result["decision_status"] in {
        "insufficient_evidence",
        "risk_blocked",
        "review_ready",
    }


def test_structured_context_missing_evidence_and_warnings_are_preserved():
    ctx = context(
        missing=({"evidence_type": "industry_observations",
                  "entity_id": "601118",
                  "reason": "no linked verified inventory observations"},),
        warnings=({"code": "inventory_unverified",
                   "message": "reported inventory source has unknown statistical scope"},),
    )
    result = synthesize_decision_report(
        context=ctx,
        hypothesis_results=[hypothesis_result()],
        risk_report=None,
        valuation_result=None,
    )
    assert result["decision_status"] == "insufficient_evidence"
    assert any("no linked verified inventory observations" in reason
               for reason in result["missing_evidence"])
    assert any("inventory_unverified" in flag for flag in result["risk_flags"])
    assert result["action"] == "no_trade"
