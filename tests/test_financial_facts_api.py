from fastapi.testclient import TestClient

from investment_engine.api import app
from investment_engine.financials import FinancialFact, FinancialFactStore


client = TestClient(app)


def test_financial_facts_endpoint_is_authenticated_and_marks_extraction_pending(tmp_path, monkeypatch):
    monkeypatch.setenv("ENGINE_API_TOKEN", "test-api-token")
    monkeypatch.setenv("ENGINE_DATA_DIR", str(tmp_path))
    fact = FinancialFact(
        fact_id="fact-001",
        symbol="601118",
        issuer_name="海南天然橡胶产业集团股份有限公司",
        metric="revenue",
        value=23_762_244_302.46,
        unit="CNY",
        currency="CNY",
        period_type="year_to_date",
        period_label="2026-Q3",
        period_start="2026-01-01",
        period_end="2026-09-30",
        statement_scope="consolidated",
        source_document_id="cninfo:601118:q3",
        source_title="海南橡胶：2026年第三季度报告",
        source_name="cninfo.official_disclosure_pdf",
        source_url="https://static.cninfo.com.cn/report.pdf",
        published_at="2026-10-29T18:00:00+08:00",
        retrieved_at="2026-11-07T12:00:00+08:00",
        source_page=8,
        source_section="consolidated_income_statement",
        source_label="营业收入",
        source_excerpt="营业收入23762244302.46",
        audit_status="unaudited",
    )
    FinancialFactStore(tmp_path).save([fact])

    assert client.get("/v1/fundamentals/facts").status_code == 401
    response = client.get(
        "/v1/fundamentals/facts?symbol=601118&period=2026-Q3&as_of=2026-11-07T12:00:00%2B08:00",
        headers={"Authorization": "Bearer test-api-token"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["count"] == 1
    assert body["facts"][0]["metric"] == "revenue"
    assert body["facts"][0]["eligible_for_assessment"] is False
    assert body["facts"][0]["extraction_status"] == "machine_extracted_pending_human_review"
