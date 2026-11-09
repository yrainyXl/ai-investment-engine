from fastapi.testclient import TestClient

from investment_engine.api import app
from investment_engine.financials import FinancialFact, FinancialFactStore


client = TestClient(app)
TOKEN = {"Authorization": "Bearer test-api-token"}
REVIEW_TOKEN = {"X-Fact-Review-Token": "test-review-token"}

def make_fact(fact_id, metric, value):
    return FinancialFact(
        fact_id=fact_id, symbol="601118", issuer_name="Example issuer",
        metric=metric, value=value, unit="CNY", value_scale="yuan",
        unit_basis="RMB yuan", currency="CNY", period_type="quarter",
        period_label="2026-Q3", period_start="2026-07-01", period_end="2026-09-30",
        statement_scope="consolidated", source_document_id="cninfo:601118:q3",
        source_title="2026年第三季度报告", source_name="cninfo",
        source_url="https://static.cninfo.com.cn/report.pdf",
        published_at="2026-10-29T18:00:00+08:00",
        retrieved_at="2026-10-30T09:00:00+08:00", source_page=1,
        source_section="consolidated_income_statement", source_label=metric,
        source_excerpt="source excerpt", audit_status="unaudited",
    )


def seed_facts(tmp_path, *, reviewed=False):
    store = FinancialFactStore(tmp_path)
    facts = [
        make_fact("fact-gross-profit", "gross_profit", "200"),
        make_fact("fact-revenue", "revenue", "1000"),
    ]
    store.save(facts)
    if reviewed:
        for fact in facts:
            store.review(
                fact.fact_id, reviewer_id="human-reviewer",
                review_note="Checked the official source page, reporting scope and unit.",
                unit_verified=True, scope_verified=True,
            )
    return facts


def hypothesis_payload(facts):
    return {
        "hypothesis": {
            "hypothesis_id": "gross-margin-q3", "entity_id": "601118",
            "strategy_id": "fundamental-v1",
            "claim": "Gross margin remains above 10%",
            "operation": "margin",
            "variables": ["gross_profit", "revenue"],
            "target": "0.1", "target_unit": "ratio", "direction": "above",
            "tolerance": "0.02",
        },
        "fact_refs": [
            {"variable_id": "gross_profit", "fact_id": facts[0].fact_id,
             "expected_metric": "gross_profit", "expected_period_type": "quarter",
             "expected_statement_scope": "consolidated"},
            {"variable_id": "revenue", "fact_id": facts[1].fact_id,
             "expected_metric": "revenue", "expected_period_type": "quarter",
             "expected_statement_scope": "consolidated"},
        ],
    }


def test_hypothesis_endpoint_requires_authentication(monkeypatch, tmp_path):
    monkeypatch.setenv("ENGINE_API_TOKEN", "test-api-token")
    monkeypatch.setenv("ENGINE_DATA_DIR", str(tmp_path))
    facts = seed_facts(tmp_path, reviewed=True)
    response = client.post("/v1/hypotheses/evaluate", json=hypothesis_payload(facts))
    assert response.status_code == 401


def test_hypothesis_endpoint_resolves_reviewed_local_facts_and_persists_report(monkeypatch, tmp_path):
    monkeypatch.setenv("ENGINE_API_TOKEN", "test-api-token")
    monkeypatch.setenv("ENGINE_DATA_DIR", str(tmp_path))
    facts = seed_facts(tmp_path, reviewed=True)
    response = client.post("/v1/hypotheses/evaluate", json=hypothesis_payload(facts), headers=TOKEN)
    assert response.status_code == 200
    body = response.json()
    assert body["evaluation"]["status"] == "supported"
    assert body["evaluation"]["computed_value"] == "0.2"
    assert body["evaluation"]["input_source_ids"] == [facts[0].fact_id, facts[1].fact_id]
    assert body["side_effects"] == "local_audit_report_only"
    assert body["orders_enabled"] is False
    assert body["report"]["report_id"]
    assert client.get("/v1/reports?kind=hypothesis", headers=TOKEN).json()["reports"][0]["kind"] == "hypothesis"


def test_hypothesis_endpoint_keeps_unreviewed_facts_ineligible_and_rejects_spoofed_flags(monkeypatch, tmp_path):
    monkeypatch.setenv("ENGINE_API_TOKEN", "test-api-token")
    monkeypatch.setenv("ENGINE_DATA_DIR", str(tmp_path))
    facts = seed_facts(tmp_path, reviewed=False)
    payload = hypothesis_payload(facts)
    response = client.post("/v1/hypotheses/evaluate", json=payload, headers=TOKEN)
    assert response.status_code == 200
    assert response.json()["evaluation"]["status"] == "not_evaluable"
    assert response.json()["evaluation"]["computed_value"] is None

    payload["fact_refs"][0]["reviewed_by_human"] = True
    spoofed = client.post("/v1/hypotheses/evaluate", json=payload, headers=TOKEN)
    assert spoofed.status_code == 422


def test_fact_review_requires_explicit_confirmation_and_keeps_thesis_unassessed(monkeypatch, tmp_path):
    monkeypatch.setenv("ENGINE_API_TOKEN", "test-api-token")
    monkeypatch.setenv("ENGINE_DATA_DIR", str(tmp_path))
    facts = seed_facts(tmp_path, reviewed=False)
    monkeypatch.setenv("ENGINE_FACT_REVIEW_TOKEN", "test-review-token")
    body = {
        "reviewer_id": "human-reviewer", "review_note": "Checked official statement page and units.",
        "unit_verified": True, "scope_verified": True,
    }
    missing_confirmation = client.post(
        f"/v1/fundamentals/facts/{facts[0].fact_id}/review", json=body, headers={**TOKEN, **REVIEW_TOKEN},
    )
    assert missing_confirmation.status_code == 400
    body["confirm_source_checked"] = True
    response = client.post(
        f"/v1/fundamentals/facts/{facts[0].fact_id}/review", json=body, headers={**TOKEN, **REVIEW_TOKEN},
    )
    assert response.status_code == 200
    reviewed = response.json()["fact"]
    assert reviewed["reviewed_by_human"] is True
    assert reviewed["unit_verified"] is True
    assert reviewed["scope_verified"] is True
    assert reviewed["assessment"] == "unassessed"


def test_scenario_endpoint_labels_overrides_as_assumptions(monkeypatch, tmp_path):
    monkeypatch.setenv("ENGINE_API_TOKEN", "test-api-token")
    monkeypatch.setenv("ENGINE_DATA_DIR", str(tmp_path))
    facts = seed_facts(tmp_path, reviewed=True)
    payload = hypothesis_payload(facts)
    payload["scenarios"] = [{
        "scenario_id": "downside", "name": "Downside",
        "description": "Gross profit falls by half",
        "overrides": {"gross_profit": "100"},
    }]
    response = client.post("/v1/hypotheses/scenarios", json=payload, headers=TOKEN)
    assert response.status_code == 200
    scenario = response.json()["scenarios"]["scenarios"][0]["evaluation"]
    assert scenario["status"] == "inconclusive"
    assert scenario["input_kinds"]["gross_profit"] == "scenario_assumption"
    assert scenario["input_source_ids"][0] == "scenario:downside:gross_profit"


def test_fact_review_disabled_without_separate_credential(monkeypatch, tmp_path):
    monkeypatch.setenv("ENGINE_API_TOKEN", "test-api-token")
    monkeypatch.delenv("ENGINE_FACT_REVIEW_TOKEN", raising=False)
    monkeypatch.setenv("ENGINE_DATA_DIR", str(tmp_path))
    facts = seed_facts(tmp_path)
    body = {
        "reviewer_id": "reviewer", "review_note": "Source checked",
        "unit_verified": True, "scope_verified": True, "confirm_source_checked": True,
    }
    response = client.post(f"/v1/fundamentals/facts/{facts[0].fact_id}/review",
                           json=body, headers=TOKEN)
    assert response.status_code == 503


def test_fact_review_rejects_bearer_token_without_review_credential(monkeypatch, tmp_path):
    monkeypatch.setenv("ENGINE_API_TOKEN", "test-api-token")
    monkeypatch.setenv("ENGINE_FACT_REVIEW_TOKEN", "test-review-token")
    monkeypatch.setenv("ENGINE_DATA_DIR", str(tmp_path))
    facts = seed_facts(tmp_path)
    body = {
        "reviewer_id": "reviewer", "review_note": "Source checked",
        "unit_verified": True, "scope_verified": True, "confirm_source_checked": True,
    }
    response = client.post(f"/v1/fundamentals/facts/{facts[0].fact_id}/review",
                           json=body, headers=TOKEN)
    assert response.status_code == 403


def test_hypothesis_rejects_mismatched_metric_or_statement_scope(monkeypatch, tmp_path):
    monkeypatch.setenv("ENGINE_API_TOKEN", "test-api-token")
    monkeypatch.setenv("ENGINE_DATA_DIR", str(tmp_path))
    facts = seed_facts(tmp_path, reviewed=True)
    payload = hypothesis_payload(facts)
    payload["fact_refs"][0]["expected_metric"] = "revenue"
    response = client.post("/v1/hypotheses/evaluate", json=payload, headers=TOKEN)
    assert response.status_code == 422
    assert "different metric" in response.json()["detail"]

    payload = hypothesis_payload(facts)
    payload["fact_refs"][1]["expected_statement_scope"] = "parent"
    response = client.post("/v1/hypotheses/evaluate", json=payload, headers=TOKEN)
    assert response.status_code == 422
    assert "different statement scope" in response.json()["detail"]
