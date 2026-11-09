import pytest

from investment_engine.valuation import (
    DCFInput,
    ValuationError,
    ValuationMetric,
    calculate_dcf,
    calculate_multiple_valuation,
    dcf_sensitivity,
)


AS_OF = "2026-11-09T12:00:00+08:00"


def reviewed_metric(value="2.5", **overrides):
    values = {
        "metric_id": "eps", "value": value, "unit": "CNY/share",
        "period_end": "2026-09-30", "source_id": "cninfo:report:eps",
        "published_at": "2026-10-29T18:00:00+08:00",
        "effective_at": "2026-09-30T16:00:00+08:00",
        "retrieved_at": "2026-10-30T09:00:00+08:00",
        "reviewed_by_human": True, "reviewed_at": "2026-10-30T09:00:00+08:00",
        "unit_verified": True, "scope_verified": True,
    }
    values.update(overrides)
    return ValuationMetric(**values)


def dcf_model(**overrides):
    values = {
        "entity_id": "example-equity", "currency": "CNY",
        "forecast_fcf": ("100", "110", "120"),
        "discount_rate": "0.10", "terminal_growth": "0.03",
        "net_debt": "50", "shares_outstanding": "100",
        "net_debt_source_id": "source:net-debt",
        "shares_source_id": "source:shares",
        "assumptions_reviewed_by_human": True,
        "source_facts_reviewed_by_human": True,
        "unit_verified": True, "scope_verified": True,
    }
    values.update(overrides)
    return DCFInput(**values)


def test_pe_multiple_calculation_requires_reviewed_per_share_metric():
    result = calculate_multiple_valuation(
        reviewed_metric(), multiple_name="PE", multiple="12", as_of=AS_OF,
    )
    assert result.status == "calculated"
    assert result.implied_value == "30.0"
    assert result.unit == "CNY/share"


def test_unreviewed_metric_is_not_eligible_for_multiple_valuation():
    result = calculate_multiple_valuation(
        reviewed_metric(reviewed_by_human=False), multiple_name="PE",
        multiple="12", as_of=AS_OF,
    )
    assert result.status == "not_evaluable"
    assert result.implied_value is None
    assert any("not human reviewed" in reason for reason in result.reasons)


def test_multiple_requires_per_share_unit():
    with pytest.raises(ValuationError, match="per-share"):
        calculate_multiple_valuation(
            reviewed_metric(unit="CNY"), multiple_name="PE", multiple="12", as_of=AS_OF,
        )


def test_negative_or_zero_eps_not_used_for_conventional_positive_pe():
    result = calculate_multiple_valuation(
        reviewed_metric(value="-0.5"), multiple_name="PE", multiple="12", as_of=AS_OF,
    )
    assert result.status == "not_evaluable"
    assert any("must be positive" in reason for reason in result.reasons)


def test_dcf_requires_reviewed_assumptions_and_source_facts():
    result = calculate_dcf(
        dcf_model(assumptions_reviewed_by_human=False), as_of=AS_OF,
    )
    assert result.status == "not_evaluable"
    assert result.value_per_share is None
    assert any("assumptions are not human reviewed" in reason for reason in result.reasons)


def test_dcf_calculates_enterprise_equity_and_per_share_value():
    result = calculate_dcf(dcf_model(), as_of=AS_OF)
    assert result.status == "calculated"
    assert result.enterprise_value is not None
    assert result.equity_value is not None
    assert result.value_per_share is not None
    assert result.source_ids == ("source:net-debt", "source:shares")


def test_dcf_rejects_terminal_growth_equal_to_or_above_discount_rate():
    with pytest.raises(ValuationError, match="below discount_rate"):
        calculate_dcf(dcf_model(discount_rate="0.08", terminal_growth="0.08"), as_of=AS_OF)


def test_dcf_rejects_zero_shares_outstanding():
    with pytest.raises(ValuationError, match="shares_outstanding"):
        calculate_dcf(dcf_model(shares_outstanding="0"), as_of=AS_OF)


def test_dcf_sensitivity_returns_grid_and_marks_invalid_pairs():
    cells = dcf_sensitivity(
        dcf_model(), as_of=AS_OF,
        discount_rates=("0.08", "0.10"),
        terminal_growth_rates=("0.02", "0.09"),
    )
    assert len(cells) == 4
    assert cells[0].status == "calculated"
    assert cells[1].status == "not_evaluable"
    assert cells[1].value_per_share is None
