from fastapi.testclient import TestClient

from investment_engine.api import app


client = TestClient(app)
TOKEN = {"Authorization": "Bearer test-api-token"}


def hypothesis_payload():
    return {
        "hypothesis": {
            "hypothesis_id": "gross-margin-q3",
            "entity_id": "example-equity",
            "strategy_id": "fundamental-v1",
            "claim": "Gross margin remains above 10%",
            "operation": "margin",
            "variables": ["gross_profit", "revenue"],
            "target": "0.1",
            "target_unit": "ratio",
            "direction": "above",
            "tolerance": "0.02",
        },
        "variables": [
            {
                "variable_id": "gross_profit", "metric_id": "gross_profit",
                "value": "20", "unit": "CNY", "period_type": "quarter",
                "period_start": "2026-07-01", "period_end": "2026-09-30",
                "source_id": "source:gross-profit",
                "published_at": "2026-10-29T18:00:00+08:00",
                "effective_at": "2026-09-30T16:00:00+08:00",
                "retrieved_at": "2026-10-30T09:00:00+08:00",
                "reviewed_by_human": True, "unit_verified": True, "scope_verified": True,
            },
            {
                "variable_id": "revenue", "metric_id": "revenue",
                "value": "100", "unit": "CNY", "period_type": "quarter",
                "period_start": "2026-07-01", "period_end": "2026-09-30",
                "source_id": "source:revenue",
                "published_at": "2026-10-29T18:00:00+08:00",
                "effective_at": "2026-09-30T16:00:00+08:00",
                "retrieved_at": "2026-10-30T09:00:00+08:00",
                "reviewed_by_human": True, "unit_verified": True, "scope_verified": True,
            },
        ],
        "as_of": "2026-11-09T12:00:00+08:00",
    }


def test_hypothesis_endpoint_requires_authentication(monkeypatch):
    monkeypatch.setenv("ENGINE_API_TOKEN", "test-api-token")
    response = client.post("/v1/hypotheses/evaluate", json=hypothesis_payload())
    assert response.status_code == 401


def test_hypothesis_endpoint_evaluates_and_persists_local_report(monkeypatch, tmp_path):
    monkeypatch.setenv("ENGINE_API_TOKEN", "test-api-token")
    monkeypatch.setenv("ENGINE_DATA_DIR", str(tmp_path))
    response = client.post("/v1/hypotheses/evaluate", json=hypothesis_payload(), headers=TOKEN)
    assert response.status_code == 200
    body = response.json()
    assert body["evaluation"]["status"] == "supported"
    assert body["evaluation"]["computed_value"] == "0.2"
    assert body["side_effects"] == "local_audit_report_only"
    assert body["orders_enabled"] is False
    assert body["report"]["report_id"]
    assert client.get("/v1/reports?kind=hypothesis", headers=TOKEN).json()["reports"][0]["kind"] == "hypothesis"


def test_hypothesis_endpoint_keeps_unreviewed_facts_ineligible(monkeypatch, tmp_path):
    monkeypatch.setenv("ENGINE_API_TOKEN", "test-api-token")
    monkeypatch.setenv("ENGINE_DATA_DIR", str(tmp_path))
    payload = hypothesis_payload()
    payload["variables"][0]["reviewed_by_human"] = False
    response = client.post("/v1/hypotheses/evaluate", json=payload, headers=TOKEN)
    assert response.status_code == 200
    assert response.json()["evaluation"]["status"] == "not_evaluable"
    assert response.json()["evaluation"]["computed_value"] is None


def test_scenario_endpoint_labels_overrides_as_assumptions(monkeypatch, tmp_path):
    monkeypatch.setenv("ENGINE_API_TOKEN", "test-api-token")
    monkeypatch.setenv("ENGINE_DATA_DIR", str(tmp_path))
    payload = hypothesis_payload()
    payload["scenarios"] = [{
        "scenario_id": "downside", "name": "Downside",
        "description": "Gross profit falls by half",
        "overrides": {"gross_profit": "10"},
    }]
    response = client.post("/v1/hypotheses/scenarios", json=payload, headers=TOKEN)
    assert response.status_code == 200
    scenario = response.json()["scenarios"]["scenarios"][0]["evaluation"]
    assert scenario["status"] == "inconclusive"
    assert scenario["input_kinds"]["gross_profit"] == "scenario_assumption"


def test_multiple_valuation_endpoint(monkeypatch, tmp_path):
    monkeypatch.setenv("ENGINE_API_TOKEN", "test-api-token")
    monkeypatch.setenv("ENGINE_DATA_DIR", str(tmp_path))
    payload = {
        "metric": {
            "metric_id": "eps", "value": "2.5", "unit": "CNY/share",
            "period_end": "2026-09-30", "source_id": "source:eps",
            "published_at": "2026-10-29T18:00:00+08:00",
            "effective_at": "2026-09-30T16:00:00+08:00",
            "retrieved_at": "2026-10-30T09:00:00+08:00",
            "reviewed_by_human": True, "unit_verified": True, "scope_verified": True,
        },
        "multiple_name": "PE", "multiple": "12", "as_of": "2026-11-09T12:00:00+08:00",
    }
    response = client.post("/v1/valuation/multiples", json=payload, headers=TOKEN)
    assert response.status_code == 200
    assert response.json()["valuation"]["implied_value"] == "30.0"


def test_dcf_endpoint_persists_sensitivity_report(monkeypatch, tmp_path):
    monkeypatch.setenv("ENGINE_API_TOKEN", "test-api-token")
    monkeypatch.setenv("ENGINE_DATA_DIR", str(tmp_path))
    payload = {
        "model": {
            "entity_id": "example-equity", "currency": "CNY",
            "forecast_fcf": ["100", "110", "120"],
            "discount_rate": "0.10", "terminal_growth": "0.03",
            "net_debt": "50", "shares_outstanding": "100",
            "net_debt_source_id": "source:net-debt", "shares_source_id": "source:shares",
            "net_debt_published_at": "2026-10-29T18:00:00+08:00",
            "net_debt_effective_at": "2026-09-30T16:00:00+08:00",
            "net_debt_retrieved_at": "2026-10-30T09:00:00+08:00",
            "shares_published_at": "2026-10-29T18:00:00+08:00",
            "shares_effective_at": "2026-09-30T16:00:00+08:00",
            "shares_retrieved_at": "2026-10-30T09:00:00+08:00",
            "assumptions_as_of": "2026-11-09T09:00:00+08:00",
            "assumptions_reviewed_by_human": True, "source_facts_reviewed_by_human": True,
            "unit_verified": True, "scope_verified": True,
        },
        "as_of": "2026-11-09T12:00:00+08:00",
        "sensitivity_discount_rates": ["0.08", "0.10"],
        "sensitivity_terminal_growth_rates": ["0.02", "0.09"],
    }
    response = client.post("/v1/valuation/dcf", json=payload, headers=TOKEN)
    assert response.status_code == 200
    body = response.json()
    assert body["valuation"]["status"] == "calculated"
    assert len(body["sensitivity"]) == 4
    assert body["sensitivity"][1]["status"] == "not_evaluable"
    assert body["report"]["report_id"]
