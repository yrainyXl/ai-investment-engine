import pytest
from fastapi.testclient import TestClient
from investment_engine.api import app

client = TestClient(app)


def test_healthz_is_read_only():
    response = client.get('/healthz')
    assert response.status_code == 200
    assert response.json()['orders_enabled'] is False


def test_otw_http_preview_requires_engine_api_token(monkeypatch):
    monkeypatch.delenv('ENGINE_API_TOKEN', raising=False)
    response = client.post('/v1/analysis/preview', json={'source': 'otw'})
    assert response.status_code == 503


def test_preview_with_engine_token_rejects_unauthorized(monkeypatch):
    monkeypatch.setenv('ENGINE_API_TOKEN', 'test-api-token')
    unauthorized = client.post('/v1/analysis/preview', json={'source': 'fixture'})
    assert unauthorized.status_code == 401
    authorized = client.post('/v1/analysis/preview', json={'source': 'fixture'}, headers={'Authorization': 'Bearer test-api-token'})
    assert authorized.status_code == 200
    assert authorized.json()['execution']['order_submitted'] is False


def test_strategy_listing_requires_engine_token(monkeypatch):
    monkeypatch.delenv("ENGINE_API_TOKEN", raising=False)
    response = client.get("/v1/strategies")
    assert response.status_code == 503


def test_strategy_listing_discovers_otw_without_global_id(monkeypatch):
    monkeypatch.setenv("ENGINE_API_TOKEN", "test-api-token")
    class Fake:
        def list_strategies(self):
            return [{"id": "10000000-0000-4000-8000-000000000001",
                     "name": "Example", "status": "active", "portfolio_id": "portfolio"}]
    monkeypatch.setattr("investment_engine.api._otw_client", lambda: Fake())
    response = client.get("/v1/strategies", headers={"Authorization": "Bearer test-api-token"})
    assert response.status_code == 200
    assert response.json()["count"] == 1
    assert response.json()["strategies"][0]["name"] == "Example"


def test_market_data_endpoints_require_engine_token(monkeypatch):
    monkeypatch.setenv("ENGINE_API_TOKEN", "test-api-token")
    assert client.get("/v1/market-data").status_code == 401
    assert client.post("/v1/market-data/sync", json={"symbol": "601118"}).status_code == 401


def test_market_data_list_is_authenticated(monkeypatch, tmp_path):
    monkeypatch.setenv("ENGINE_API_TOKEN", "test-api-token")
    monkeypatch.setenv("ENGINE_DATA_DIR", str(tmp_path))
    response = client.get("/v1/market-data", headers={"Authorization": "Bearer test-api-token"})
    assert response.status_code == 200
    assert response.json()["series"] == []


def test_backtest_endpoint_returns_insufficient_history(monkeypatch, tmp_path):
    monkeypatch.setenv("ENGINE_API_TOKEN", "test-api-token")
    monkeypatch.setenv("ENGINE_DATA_DIR", str(tmp_path))
    response = client.post(
        "/v1/backtests/run",
        json={"symbol": "601118"},
        headers={"Authorization": "Bearer test-api-token"},
    )
    assert response.status_code == 422
