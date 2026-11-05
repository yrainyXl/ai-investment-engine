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
