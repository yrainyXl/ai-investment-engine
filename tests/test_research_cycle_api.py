from fastapi.testclient import TestClient

from investment_engine.api import app


client = TestClient(app)


def test_research_cycle_requires_research_bearer(monkeypatch, tmp_path):
    monkeypatch.setenv("ENGINE_API_TOKEN", "test-api-token")
    monkeypatch.setenv("ENGINE_DATA_DIR", str(tmp_path))
    request = {"entity_id": "510300", "asset_class": "etf"}
    assert client.post("/v1/research/cycle", json=request).status_code == 401


def test_research_cycle_returns_audited_no_trade_when_no_data(monkeypatch, tmp_path):
    monkeypatch.setenv("ENGINE_API_TOKEN", "test-api-token")
    monkeypatch.setenv("ENGINE_DATA_DIR", str(tmp_path))
    response = client.post(
        "/v1/research/cycle",
        json={"entity_id": "510300", "asset_class": "etf"},
        headers={"Authorization": "Bearer test-api-token"},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["decision_report"]["decision_status"] == "insufficient_evidence"
    assert payload["context"]["data_status"] == "unavailable"
    assert payload["orders_enabled"] is False
    assert payload["decision_report"]["action"] == "no_trade"
    assert payload["audit"]["report_id"]


def test_research_cycle_rejects_unknown_request_fields(monkeypatch, tmp_path):
    monkeypatch.setenv("ENGINE_API_TOKEN", "test-api-token")
    monkeypatch.setenv("ENGINE_DATA_DIR", str(tmp_path))
    response = client.post(
        "/v1/research/cycle",
        json={"entity_id": "510300", "asset_class": "etf", "reviewed_by_human": True},
        headers={"Authorization": "Bearer test-api-token"},
    )
    assert response.status_code == 422
