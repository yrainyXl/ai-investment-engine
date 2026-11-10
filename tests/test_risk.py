from decimal import Decimal
import pytest

from investment_engine.risk import (
    EquityObservation, HypotheticalPosition, RiskError,
    RiskLimits, StressShock, assess_hypothetical_risk,
)


def positions():
    return (
        HypotheticalPosition("STOCK:A", "stock", "materials", "200", "CNY"),
        HypotheticalPosition("ETF:B", "etf", "broad_index", "300", "CNY"),
    )


def evaluate(**overrides):
    args = {
        "portfolio_id": "test-synthetic", "positions": positions(), "cash": "500",
        "currency": "CNY", "as_of": "2026-11-09",
        "limits": RiskLimits(max_single_weight="0.25", max_sector_weight="0.5"),
    }
    args.update(overrides)
    return assess_hypothetical_risk(**args)


def test_concentration_and_cash_weights_with_decimal_precision():
    result = evaluate()
    assert result.status == "assessed"
    assert Decimal(result.portfolio_value) == 1000
    assert Decimal(result.cash_weight) == Decimal("0.5")
    assert Decimal(result.risky_weight) == Decimal("0.5")
    assert Decimal(result.largest_position_weight) == Decimal("0.3")
    assert result.limit_breaches == ("max_single_weight_exceeded",)
    assert result.max_drawdown is None
    assert result.eligible_for_trading is False
    assert "hypothetical" in " ".join(result.assumptions)


def test_sector_concentration_aggregates_multiple_positions():
    joined = (
        HypotheticalPosition("A", "stock", "tech", "300"),
        HypotheticalPosition("B", "stock", "tech", "350"),
    )
    result = evaluate(positions=joined, cash="350",
                      limits=RiskLimits(max_single_weight="0.6", max_sector_weight="0.5"))
    assert "max_sector_weight_exceeded" in result.limit_breaches


def test_stress_scenarios_never_modify_positions():
    initial = positions()
    result = evaluate(shocks=(StressShock("recession", "Recession", {
        "STOCK:A": "-0.3", "ETF:B": "-0.2",
    }),))
    scenario = result.stress_results[0]
    assert Decimal(scenario["portfolio_return"]) == Decimal("-0.12")
    assert Decimal(scenario["portfolio_loss"]) == Decimal("120")
    assert scenario["input_kind"] == "scenario_assumption"
    assert initial == positions()


def test_drawdown_is_calculated_from_ordered_historical_equity():
    history = (
        EquityObservation("2026-11-01", "1000"),
        EquityObservation("2026-11-02", "1100"),
        EquityObservation("2026-11-03", "880"),
    )
    result = evaluate(history=history, limits=RiskLimits(max_drawdown="0.15"))
    assert Decimal(result.max_drawdown) == Decimal("0.2")
    assert "max_drawdown_exceeded" in result.limit_breaches


@pytest.mark.parametrize("bad", [
    (HypotheticalPosition("A", "stock", "tech", "-1"),),
    (HypotheticalPosition("A", "stock", "tech", "Infinity"),),
    (HypotheticalPosition("A", "stock", "tech", "100", "USD"),),
])
def test_rejects_unsupported_short_nonfinite_and_mixed_currency(bad):
    with pytest.raises(RiskError):
        evaluate(positions=bad)


def test_rejects_duplicates_and_future_equity_history():
    same = (HypotheticalPosition("A", "stock", "a", "5"),
            HypotheticalPosition("A", "etf", "b", "6"))
    with pytest.raises(RiskError, match="duplicate"):
        evaluate(positions=same)
    with pytest.raises(RiskError, match="after as_of"):
        evaluate(history=(EquityObservation("2026-11-09", "1000"),
                          EquityObservation("2026-11-10", "900")))


def test_missing_equity_history_does_not_claim_a_historical_drawdown():
    result = evaluate()
    assert result.max_drawdown is None
    assert all(x != "max_drawdown_exceeded" for x in result.limit_breaches)


def test_rejects_unrecognized_and_unrealistic_shocks():
    with pytest.raises(RiskError, match="unknown scenario symbols"):
        evaluate(shocks=(StressShock("bad", "bad", {"UNKNOWN": "-0.2"}),))
    with pytest.raises(RiskError, match="between -100%"):
        evaluate(shocks=(StressShock("bad", "bad", {"STOCK:A": "-1.1"}),))


def test_supports_another_industry_without_hardcoded_rubber_identifiers():
    report = assess_hypothetical_risk(
        "consumer-tech", (
            HypotheticalPosition("FOOD:C", "stock", "consumer", "400"),
            HypotheticalPosition("TECH:D", "stock", "technology", "200"),
        ), cash="400", currency="CNY", as_of="2026-11-09",
        limits=RiskLimits(max_single_weight="0.5", max_sector_weight="0.6"),
    )
    assert report.status == "assessed"
    assert report.limit_breaches == ()
