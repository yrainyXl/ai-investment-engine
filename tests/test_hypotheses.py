from datetime import datetime, timedelta, timezone

import pytest

from investment_engine.hypotheses import (
    HypothesisDefinition,
    HypothesisError,
    HypothesisVariable,
    evaluate_hypothesis,
)


AS_OF = "2026-11-09T12:00:00+08:00"


def variable(variable_id, value, *, unit="CNY", start="2026-01-01", end="2026-09-30",
             period_type="year_to_date", reviewed=True, unit_verified=True,
             scope_verified=True, effective="2026-09-30T16:00:00+08:00",
             published="2026-10-29T18:00:00+08:00", retrieved="2026-10-30T09:00:00+08:00"):
    return HypothesisVariable(
        variable_id=variable_id, metric_id=variable_id, value=value, unit=unit,
        period_type=period_type, period_start=start, period_end=end,
        source_id=f"source:{variable_id}", published_at=published,
        effective_at=effective, retrieved_at=retrieved,
        reviewed_by_human=reviewed, unit_verified=unit_verified,
        scope_verified=scope_verified,
    )


def definition(*, operation="ratio", variables=("numerator", "denominator"),
               target="0.1", target_unit="ratio", direction="above",
               tolerance="0", max_age_days=None):
    return HypothesisDefinition(
        hypothesis_id="hypothesis-1", entity_id="601118", strategy_id="rubber-fundamental-v1",
        claim="Test a measurable investment hypothesis", operation=operation,
        variables=variables, target=target, target_unit=target_unit, direction=direction,
        tolerance=tolerance, max_age_days=max_age_days,
    )


def test_ratio_evaluation_is_deterministic_and_keeps_provenance():
    result = evaluate_hypothesis(
        definition(), {"numerator": variable("numerator", "25"), "denominator": variable("denominator", "100")},
        as_of=AS_OF,
    )
    assert result.status == "supported"
    assert result.computed_value == "0.25"
    assert result.formula == "numerator / denominator"
    assert result.input_source_ids == ("source:numerator", "source:denominator")
    assert result.result_unit == "ratio"


def test_machine_extracted_financial_fact_is_not_evaluable_until_reviewed():
    result = evaluate_hypothesis(
        definition(), {
            "numerator": variable("numerator", "25", reviewed=False),
            "denominator": variable("denominator", "100"),
        }, as_of=AS_OF,
    )
    assert result.status == "not_evaluable"
    assert result.computed_value is None
    assert any("human review is pending" in reason for reason in result.reasons)


@pytest.mark.parametrize("flag,reason", [
    ("unit_verified", "unit is not verified"),
    ("scope_verified", "statistical scope is not verified"),
])
def test_unverified_unit_or_scope_is_not_evaluable(flag, reason):
    values = {"numerator": variable("numerator", "25"), "denominator": variable("denominator", "100")}
    values["numerator"] = variable("numerator", "25", **{flag: False})
    result = evaluate_hypothesis(definition(), values, as_of=AS_OF)
    assert result.status == "not_evaluable"
    assert any(reason in item for item in result.reasons)


def test_publication_after_as_of_is_not_evaluable():
    result = evaluate_hypothesis(
        definition(), {
            "numerator": variable("numerator", "25", published="2026-11-10T09:00:00+08:00"),
            "denominator": variable("denominator", "100"),
        }, as_of=AS_OF,
    )
    assert result.status == "not_evaluable"
    assert any("published_at is after as_of" in item for item in result.reasons)


def test_missing_input_is_not_evaluable():
    result = evaluate_hypothesis(definition(), {"numerator": variable("numerator", "25")}, as_of=AS_OF)
    assert result.status == "not_evaluable"
    assert any("missing required variable: denominator" in item for item in result.reasons)


def test_ytd_and_quarter_periods_cannot_be_mixed_in_ratio():
    result = evaluate_hypothesis(
        definition(), {
            "numerator": variable("numerator", "25", period_type="quarter", start="2026-07-01"),
            "denominator": variable("denominator", "100", period_type="year_to_date"),
        }, as_of=AS_OF,
    )
    assert result.status == "not_evaluable"
    assert any("input periods do not align" in item for item in result.reasons)


def test_growth_compares_distinct_equal_duration_periods():
    current = variable("current", "125", start="2026-01-01", end="2026-09-30")
    prior = variable("prior", "100", start="2025-01-01", end="2025-09-30")
    result = evaluate_hypothesis(
        definition(operation="growth", variables=("current", "prior"), target="0.2"),
        {"current": current, "prior": prior}, as_of=AS_OF,
    )
    assert result.status == "supported"
    assert result.computed_value == "0.25"
    assert result.period_start is None


def test_growth_rejects_different_period_durations():
    current = variable("current", "125", start="2026-01-01", end="2026-09-30")
    prior = variable("prior", "100", start="2025-01-01", end="2025-06-30")
    result = evaluate_hypothesis(
        definition(operation="growth", variables=("current", "prior"), target="0.2"),
        {"current": current, "prior": prior}, as_of=AS_OF,
    )
    assert result.status == "not_evaluable"
    assert any("equal duration" in item for item in result.reasons)


def test_growth_requires_same_unit():
    current = variable("current", "125", unit="CNY")
    prior = variable("prior", "100", unit="USD", start="2025-01-01", end="2025-09-30")
    result = evaluate_hypothesis(
        definition(operation="growth", variables=("current", "prior"), target="0.2"),
        {"current": current, "prior": prior}, as_of=AS_OF,
    )
    assert result.status == "not_evaluable"
    assert any("same unit" in item for item in result.reasons)


def test_zero_denominator_is_not_evaluable():
    result = evaluate_hypothesis(
        definition(), {"numerator": variable("numerator", "25"), "denominator": variable("denominator", "0")},
        as_of=AS_OF,
    )
    assert result.status == "not_evaluable"
    assert "denominator is zero" in result.reasons


def test_target_unit_must_match_computed_unit():
    result = evaluate_hypothesis(
        definition(target_unit="CNY"), {"numerator": variable("numerator", "25"), "denominator": variable("denominator", "100")},
        as_of=AS_OF,
    )
    assert result.status == "not_evaluable"
    assert any("target_unit mismatch" in item for item in result.reasons)


def test_tolerance_creates_inconclusive_band():
    result = evaluate_hypothesis(
        definition(target="0.2", tolerance="0.03"),
        {"numerator": variable("numerator", "21"), "denominator": variable("denominator", "100")},
        as_of=AS_OF,
    )
    assert result.status == "inconclusive"


def test_weighted_sum_uses_decimal_arithmetic():
    definition_ = definition(
        operation="weighted_sum", variables=("a", "b"), target="0.3",
        target_unit="tonne", direction="equal",
    )
    definition_ = HypothesisDefinition(**{
        **definition_.__dict__, "weights": ("0.25", "0.75"),
    })
    result = evaluate_hypothesis(
        definition_, {"a": variable("a", "0.2", unit="tonne"),
                      "b": variable("b", "0.3333333333333333333333333333", unit="tonne")},
        as_of=AS_OF,
    )
    assert result.status == "supported"
    from decimal import Decimal
    assert Decimal(result.computed_value) == Decimal("0.3")


def test_stale_inputs_are_not_evaluable_when_freshness_is_required():
    result = evaluate_hypothesis(
        definition(max_age_days=30),
        {"numerator": variable("numerator", "25", effective="2026-01-01T00:00:00+08:00"),
         "denominator": variable("denominator", "100")},
        as_of=AS_OF,
    )
    assert result.status == "not_evaluable"
    assert any("observation is stale" in item for item in result.reasons)


def test_timezone_is_required_for_as_of():
    with pytest.raises(HypothesisError, match="include timezone"):
        evaluate_hypothesis(
            definition(), {"numerator": variable("numerator", "25"),
                           "denominator": variable("denominator", "100")},
            as_of="2026-11-09T12:00:00",
        )


def test_future_as_of_is_rejected():
    future = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
    with pytest.raises(HypothesisError, match="future"):
        evaluate_hypothesis(
            definition(), {"numerator": variable("numerator", "25"),
                           "denominator": variable("denominator", "100")},
            as_of=future,
        )


@pytest.mark.parametrize("entity_id,metric_a,metric_b", [
    ("601118", "revenue", "cost_of_revenue"),
    ("ETF:510300", "nav", "benchmark_nav"),
])
def test_same_engine_accepts_unrelated_entities(entity_id, metric_a, metric_b):
    hypothesis = HypothesisDefinition(
        hypothesis_id=f"{entity_id}-ratio", entity_id=entity_id, strategy_id="generic-v1",
        claim="Generic ratio test", operation="ratio", variables=("a", "b"),
        target="0.5", target_unit="ratio", direction="above",
    )
    result = evaluate_hypothesis(
        hypothesis, {"a": variable("a", "60", unit="index"),
                     "b": variable("b", "100", unit="index")}, as_of=AS_OF,
    )
    assert result.entity_id == entity_id
    assert result.status == "supported"


def test_arbitrary_expression_is_not_an_allowed_operation():
    with pytest.raises(HypothesisError, match="unsupported operation"):
        HypothesisDefinition(
            hypothesis_id="x", entity_id="x", strategy_id="x", claim="x",
            operation="__import__('os').system('id')", variables=("a",),
            target="0", target_unit="CNY", direction="above",
        ).validate()
