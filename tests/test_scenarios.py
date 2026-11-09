from investment_engine.hypotheses import HypothesisDefinition, HypothesisVariable
from investment_engine.scenarios import ScenarioDefinition, evaluate_scenarios


AS_OF = "2026-11-09T12:00:00+08:00"


def make_variable(name, value):
    return HypothesisVariable(
        variable_id=name, metric_id=name, value=value, unit="CNY",
        period_type="quarter", period_start="2026-07-01", period_end="2026-09-30",
        source_id=f"source:{name}", published_at="2026-10-29T18:00:00+08:00",
        effective_at="2026-09-30T16:00:00+08:00", retrieved_at="2026-10-30T09:00:00+08:00",
        reviewed_by_human=True, reviewed_at="2026-10-30T09:00:00+08:00",
        unit_verified=True, scope_verified=True,
    )


def make_definition():
    return HypothesisDefinition(
        hypothesis_id="gross-margin", entity_id="example-equity", strategy_id="fundamental-v1",
        claim="Gross margin remains above 10%", operation="margin",
        variables=("gross_profit", "revenue"), target="0.1",
        target_unit="ratio", direction="above", tolerance="0.02",
    )


def test_scenario_overrides_are_labeled_assumptions_not_observed_facts():
    result = evaluate_scenarios(
        make_definition(),
        {"gross_profit": make_variable("gross_profit", "20"),
         "revenue": make_variable("revenue", "100")},
        (
            ScenarioDefinition("downside", "Downside", "Gross profit falls by 50%", {"gross_profit": "10"}),
        ),
        as_of=AS_OF,
    )
    scenario = result.scenarios[0].evaluation
    assert result.baseline.status == "supported"
    assert scenario.status == "inconclusive"
    assert scenario.input_kinds["gross_profit"] == "scenario_assumption"
    assert scenario.input_source_ids[0] == "scenario:downside:gross_profit"
    assert "assumptions" in result.to_dict()["note"]


def test_scenario_cannot_override_unknown_or_missing_variables():
    import pytest
    from investment_engine.hypotheses import HypothesisError

    with pytest.raises(HypothesisError, match="unknown variables"):
        evaluate_scenarios(
            make_definition(), {"gross_profit": make_variable("gross_profit", "20"),
                                "revenue": make_variable("revenue", "100")},
            (ScenarioDefinition("bad", "Bad", "Unknown input", {"price": "5"}),),
            as_of=AS_OF,
        )
