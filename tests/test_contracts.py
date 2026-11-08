import pytest

from investment_engine.contracts import (
    CompanyFinancials,
    FinancialContractError,
    IndustryMetricDefinition,
    IndustryMetricObservation,
    StrategyDefinition,
    StrategyRegistry,
)


def company_fact(**overrides):
    values = dict(
        entity_id="600519", entity_type="equity", metric="revenue",
        value="123456789.12", currency="CNY", scale="yuan",
        period_type="year_to_date", period_start="2026-01-01", period_end="2026-09-30",
        statement_scope="consolidated", source_id="report-1", source_name="cninfo",
        source_url="https://static.cninfo.com.cn/report.pdf",
        published_at="2026-10-30T10:00:00+08:00", retrieved_at="2026-11-01T10:00:00+08:00",
        unit_basis="report table: 元",
    )
    values.update(overrides)
    return CompanyFinancials(**values)


def test_company_financial_contract_is_issuer_neutral_and_exact_decimal():
    fact = company_fact()
    fact.validate()
    assert fact.entity_id == "600519"
    assert fact.value == "123456789.12"


@pytest.mark.parametrize("overrides", [
    {"value": "NaN"},
    {"currency": ""},
    {"period_end": "2025-01-01"},
    {"source_url": "http://example.com/report"},
])
def test_company_financial_contract_rejects_invalid_values(overrides):
    with pytest.raises(FinancialContractError):
        company_fact(**overrides).validate()


def test_industry_metrics_are_configurable_not_rubber_specific():
    definition = IndustryMetricDefinition(
        metric_id="steel.capacity_utilization", industry_id="steel",
        display_name="产能利用率", unit="percent", description="钢铁行业产能利用率",
        source_scope="national", aggregation="average",
    )
    definition.validate()
    observation = IndustryMetricObservation(
        metric_id=definition.metric_id, entity_id="steel:cn",
        value="82.4", unit="percent", effective_at="2026-10-31",
        published_at="2026-11-02T08:00:00+08:00", source_id="source-1",
        source_scope="national",
    )
    observation.validate()


def test_strategy_registry_registers_and_resolves_multiple_asset_classes():
    registry = StrategyRegistry()
    registry.register(StrategyDefinition(
        strategy_id="value.basic", name="Basic Value", version="1.0.0",
        asset_classes=("equity", "etf"), required_inputs=("financials", "valuation"),
        parameter_schema={"max_pe": {"type": "number"}},
        decision_horizon="quarterly", risk_model="max_drawdown",
        implementation="investment_engine.strategies.value", enabled=False,
    ))
    assert registry.get("value.basic").asset_classes == ("equity", "etf")
    assert registry.list()[0]["enabled"] is False
    with pytest.raises(FinancialContractError, match="duplicate"):
        registry.register(registry.get("value.basic"))


def test_provider_financial_fact_maps_to_shared_contract_without_auto_approval():
    from types import SimpleNamespace
    from investment_engine.contracts import company_financial_from_fact

    provider_fact = SimpleNamespace(
        symbol="601118", issuer_name="海南天然橡胶产业集团股份有限公司",
        metric="revenue", value="23762244302.46", currency="CNY",
        value_scale="yuan", period_type="year_to_date", period_start="2026-01-01",
        period_end="2026-09-30", statement_scope="consolidated",
        source_document_id="cninfo:601118:q3", source_name="cninfo.official_disclosure_pdf",
        source_url="https://static.cninfo.com.cn/report.pdf",
        published_at="2026-10-29T18:00:00+08:00", retrieved_at="2026-11-07T12:00:00+08:00",
        unit_basis="report table states 元 / 人民币", source_page=8,
        source_section="consolidated_income_statement", source_label="营业收入",
        source_excerpt="营业收入 23762244302.46", audit_status="unaudited",
        reviewed_by_human=False, unit_verified=False,
    )
    item = company_financial_from_fact(provider_fact)
    assert item.entity_id == "601118"
    assert item.entity_name == "海南天然橡胶产业集团股份有限公司"
    assert item.value == "23762244302.46"
    assert item.source_page == 8
    assert item.source_section == "consolidated_income_statement"
    assert item.extraction_status == "machine_extracted_pending_human_review"
