"""Exercise the compatible transport and runtime together without network access."""

import json
import os
from io import BytesIO
from urllib.error import HTTPError, URLError

import pytest

from investment_engine.agent_execution import execute_agents
from investment_engine.agent_runtime import ROLE_ORDER
from investment_engine import model_client
from test_agent_runtime import _agent_payload, _context, _fact, _observation


class _Reply(BytesIO):
    status = 200


class _StubOpener:
    def __init__(self):
        self.requests = []
        self.error = None
        self.override = {}

    def open(self, request, *, timeout):
        body = json.loads(request.data)
        self.requests.append((request, timeout, body))
        if self.error:
            raise self.error
        role = body["model"].removeprefix("stub-")
        prompt = body["messages"][1]["content"]
        source_ids = json.loads(prompt.split("Allowed source_ids: ", 1)[1].splitlines()[0])
        response = {
            "model": body["model"],
            "model_version": "fixture-2026",
            "choices": [{"message": {"content": json.dumps(_agent_payload(role, source_ids))},
                         "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 400, "completion_tokens": 80},
            **self.override,
        }
        return _Reply(json.dumps(response).encode())


@pytest.fixture
def configured(monkeypatch):
    for key in tuple(os.environ):
        if key.startswith("ENGINE_LLM_"):
            monkeypatch.delenv(key)
    for key, value in {
        "ENABLED": "true",
        "PROVIDER": "fixture",
        "BASE_URL": "https://stub.example/v1/",
        "API_KEY": "local-fixture-secret",
        "MAX_COST_USD": "1",
        "INPUT_COST_PER_MILLION": "1",
        "OUTPUT_COST_PER_MILLION": "2",
    }.items():
        monkeypatch.setenv(f"ENGINE_LLM_{key}", value)
    for role in ROLE_ORDER:
        monkeypatch.setenv(f"ENGINE_LLM_{role.upper()}_MODEL", f"stub-{role}")
        monkeypatch.setenv(f"ENGINE_LLM_{role.upper()}_MODEL_VERSION", "fixture-2026")
    opener = _StubOpener()
    monkeypatch.setattr(model_client, "build_opener", lambda *handlers: opener)
    return opener


@pytest.mark.parametrize("asset_class", ["stock", "etf"])
def test_transport_executes_all_roles_and_projects_public_evidence(configured, monkeypatch, asset_class):
    monkeypatch.setenv("ENGINE_LLM_RESEARCH_TIMEOUT_SECONDS", "8")
    monkeypatch.setenv("ENGINE_LLM_RESEARCH_MAX_OUTPUT_TOKENS", "512")
    context = (
        _context(facts=[_fact(source_url="https://example.com/report?token=private-url-token",
                              source_excerpt="private-analyst-note", reviewed_by="private-reviewer")])
        if asset_class == "stock"
        else _context("510300", "etf", observations=[_observation()])
    )
    context["portfolio"] = {"cash": "private-cash", "holdings": ["private-holding"]}
    result = execute_agents(context)

    assert result["status"] == "completed"
    assert result["eligible_for_trading"] is False
    assert result["orders_enabled"] is False
    assert result["model_audit"]["totals"]["cost"] == "0.00168"
    assert result["model_audit"]["gateway"]["provider"] == "fixture"
    assert result["model_audit"]["totals"]["calls"] == 3
    assert [body["model"] for _, _, body in configured.requests] == [
        f"stub-{role}" for role in ROLE_ORDER
    ]
    request, timeout, body = configured.requests[0]
    assert request.get_method() == "POST"
    assert request.full_url == "https://stub.example/v1/chat/completions"
    assert request.headers["Authorization"] == "Bearer local-fixture-secret"
    assert timeout == 8
    assert body["response_format"] == {"type": "json_object"}
    assert body["max_tokens"] == 512
    assert "private-" not in json.dumps([body for _, _, body in configured.requests])
    assert "local-fixture-secret" not in json.dumps(result)


@pytest.mark.parametrize("setting", ["ENABLED", "API_KEY", "MAX_COST_USD", "INPUT_COST_PER_MILLION"])
def test_disabled_or_incomplete_model_settings_make_zero_requests(configured, monkeypatch, setting):
    monkeypatch.delenv(f"ENGINE_LLM_{setting}")
    result = execute_agents(_context(facts=[_fact()]))
    assert result["status"] == "unavailable"
    assert result["model_audit"]["gateway"]["availability"] == (
        "disabled" if setting == "ENABLED" else "invalid_or_incomplete_configuration"
    )
    assert not configured.requests


def test_low_budget_never_reaches_transport(configured, monkeypatch):
    monkeypatch.setenv("ENGINE_LLM_MAX_COST_USD", "0.000001")
    result = execute_agents(_context(facts=[_fact()]))
    assert result["status"] == "unavailable"
    assert result["model_audit"]["gateway"]["preflight_budget_rejections"] > 0
    assert not configured.requests


def test_unreviewed_and_future_material_cannot_start_a_call(configured):
    result = execute_agents(_context(facts=[
        _fact(reviewed_by_human=False),
        _fact(fact_id="future", retrieved_at="2026-11-11T10:00:00+08:00"),
    ]))
    assert result["status"] == "insufficient_evidence"
    assert not configured.requests


@pytest.mark.parametrize("error", [
    HTTPError("https://stub.example/v1/chat/completions", 401, "private-provider-error", {}, None),
    URLError(TimeoutError("private-provider-error")),
])
def test_provider_failures_reserve_budget_and_redact_errors(configured, error):
    configured.error = error
    result = execute_agents(_context(facts=[_fact()]))
    assert result["status"] == "unavailable"
    assert float(result["model_audit"]["gateway"]["reserved_cost_usd"]) > 0
    assert "private-provider-error" not in json.dumps(result)
    assert all(role["status"] != "succeeded" for role in result["roles"].values())


@pytest.mark.parametrize("url", [
    "http://stub.example/v1", "https://user:pass@stub.example/v1", "https://stub.example/v1?q=secret",
])
def test_unsafe_provider_url_fails_closed(configured, monkeypatch, url):
    monkeypatch.setenv("ENGINE_LLM_BASE_URL", url)
    result = execute_agents(_context(facts=[_fact()]))
    assert result["model_audit"]["gateway"]["availability"] == "invalid_or_incomplete_configuration"
    assert not configured.requests


def test_provider_model_mismatch_fails_closed(configured):
    configured.override["model"] = "private-unexpected-model"
    result = execute_agents(_context(facts=[_fact()]))
    assert result["status"] == "unavailable"
    assert all(role["status"] != "succeeded" for role in result["roles"].values())
    assert "private-unexpected-model" not in json.dumps(result)
