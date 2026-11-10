from datetime import datetime, timezone

import pytest

from investment_engine.e2e_pipeline import (
    HypothesisFactBinding, ResearchCycleError, ResearchHypothesis,
    run_research_cycle,
)
from investment_engine.financials import FinancialFact, FinancialFactStore
from investment_engine.hypotheses import HypothesisDefinition
from investment_engine.report_store import ReportStore


def _fact(symbol: str, metric: str, value: str):
    return FinancialFact(
        fact_id=f"synthetic-{symbol}-{metric}",
        symbol=symbol, issuer_name=f"Fixture issuer {symbol}",
        metric=metric, value=value, unit="CNY", value_scale="yuan",
        unit_basis="source table CNY yuan", currency="CNY",
        period_type="year_to_date", period_label="2026-Q3",
        period_start="2026-01-01", period_end="2026-09-30",
        statement_scope="consolidated",
        source_document_id=f"synthetic-doc-{symbol}",
        source_title="Synthetic 2026 Q3 report fixture",
        source_name="test.fixture",
        source_url="https://static.cninfo.com.cn/test-fixture.pdf",
        published_at="2026-10-29T18:00:00+08:00",
        retrieved_at="2026-10-30T09:00:00+08:00",
        source_page=8, source_section="consolidated_income_statement",
        source_label=metric, source_excerpt="synthetic fixture row",
        audit_status="unaudited",
    )


def _hypothesis(symbol: str):
    return ResearchHypothesis(
        definition=HypothesisDefinition(
            hypothesis_id=f"gross-margin-{symbol}", entity_id=symbol,
            strategy_id="synthetic-fundamentals-v1", claim="Gross margin > 5%",
            operation="margin", variables=("gross_profit", "revenue"),
            target="0.05", target_unit="ratio", direction="above",
        ),
        bindings=(
            HypothesisFactBinding("gross_profit", f"synthetic-{symbol}-gross_profit",
                                  "gross_profit", "year_to_date", "consolidated"),
            HypothesisFactBinding("revenue", f"synthetic-{symbol}-revenue",
                                  "revenue", "year_to_date", "consolidated"),
        ),
    )


def test_end_to_end_unreviewed_facts_fail_closed_and_create_local_audit(tmp_path):
    store = FinancialFactStore(tmp_path)
    store.save([_fact("601118", "gross_profit", "200"),
                _fact("601118", "revenue", "1000")])
    result = run_research_cycle(
        root=tmp_path, entity_id="601118", asset_class="stock",
        as_of=datetime.now(timezone.utc), hypotheses=(_hypothesis("601118"),),
    )
    assert result["hypothesis_results"][0]["status"] == "not_evaluable"
    assert result["decision_report"]["decision_status"] == "insufficient_evidence"
    assert result["decision_report"]["action"] == "no_trade"
    assert result["eligible_for_trading"] is False
    assert result["orders_enabled"] is False
    assert ReportStore(tmp_path).get(result["audit"]["report_id"]) is not None


def test_second_industry_uses_identical_pipeline_not_601118_rules(tmp_path):
    symbol = "600519"
    store = FinancialFactStore(tmp_path)
    facts = [_fact(symbol, "gross_profit", "250"), _fact(symbol, "revenue", "1000")]
    store.save(facts)
    for fact in facts:
        store.review(fact.fact_id, reviewer_id="fixture-reviewer",
                     review_note="Fixture test: original statement and units manually checked",
                     unit_verified=True, scope_verified=True)
    result = run_research_cycle(
        root=tmp_path, entity_id=symbol, asset_class="stock",
        as_of=datetime.now(timezone.utc), hypotheses=(_hypothesis(symbol),),
        persist_report=False,
    )
    assert result["hypothesis_results"][0]["status"] == "supported"
    assert result["hypothesis_results"][0]["computed_value"] == "0.25"
    # Even reviewed facts do not imply real portfolio risk / verified valuation.
    assert result["decision_report"]["decision_status"] == "insufficient_evidence"
    assert result["decision_report"]["action"] == "no_trade"
    assert "audit" not in result


def test_no_cached_etf_data_remains_unavailable_not_fabricated(tmp_path):
    result = run_research_cycle(
        root=tmp_path, entity_id="ETF:510300", asset_class="etf",
        as_of=datetime.now(timezone.utc), persist_report=False,
    )
    assert result["context"]["data_status"] == "unavailable"
    assert not result["hypothesis_results"]
    assert result["decision_report"]["decision_status"] == "insufficient_evidence"
    assert result["orders_enabled"] is False


def test_wrong_asset_binding_is_rejected(tmp_path):
    store = FinancialFactStore(tmp_path)
    store.save([_fact("600519", "gross_profit", "200"),
                _fact("600519", "revenue", "1000")])
    with pytest.raises(ResearchCycleError, match="belongs to another asset"):
        run_research_cycle(
            root=tmp_path, entity_id="601118", asset_class="stock",
            as_of=datetime.now(timezone.utc),
            hypotheses=(_hypothesis("601118"),),
        )


def test_rejects_naive_or_future_as_of(tmp_path):
    with pytest.raises(ResearchCycleError, match="timezone"):
        run_research_cycle(root=tmp_path, entity_id="601118",
                           asset_class="stock", as_of=datetime(2026, 11, 10))
