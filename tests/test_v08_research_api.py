import io
import json
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from examples.v08_demo import AS_OF, WINDOWS, seed_demo
from investment_engine import model_client
from investment_engine.api import app
from investment_engine.financials import FinancialFact, FinancialFactStore

HEADERS = {"Authorization": "Bearer synthetic-v08-test"}
ROLES = {"research": "supportive", "challenger": "skeptical", "risk_review": "mixed"}


@pytest.fixture
def api(monkeypatch, tmp_path):
    monkeypatch.setenv("ENGINE_API_TOKEN", "synthetic-v08-test")
    monkeypatch.setenv("ENGINE_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("ENGINE_LLM_ENABLED", raising=False)
    monkeypatch.delenv("ENGINE_OTW_RESEARCH_STRATEGIES", raising=False)
    monkeypatch.delenv("ENGINE_OTW_RESEARCH_TOKEN", raising=False)
    monkeypatch.setenv("ENGINE_GIT_SHA", "a" * 40)
    with TestClient(app) as client:
        yield client


class _HTTPStub:
    def __init__(self, source_id):
        self.calls = []
        self.source_id = source_id

    def open(self, request, *, timeout):
        body = json.loads(request.data)
        role = body["model"].removeprefix("fixture-")
        self.calls.append(role)
        assert request.full_url == "https://example.com/v1/chat/completions"
        assert request.get_method() == "POST"
        assert timeout == 20
        assert body["response_format"] == {"type": "json_object"}
        assert self.source_id in body["messages"][1]["content"]
        claim = {"statement": "Synthetic cited claim", "source_ids": [self.source_id]}
        output = {
            "role": role, "verdict": ROLES[role], "summary": "Synthetic fixture explanation",
            "supporting": [claim], "contradicting": [claim] if role == "challenger" else [],
            "key_gaps": ["Only synthetic evidence was included."],
            "confidence": 0.4,
        }
        payload = {
            "model": body["model"],
            "choices": [{"message": {"content": json.dumps(output)}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 100, "completion_tokens": 40},
        }
        response = io.BytesIO(json.dumps(payload).encode())
        response.status = 200
        return response


def configure_stub(monkeypatch, source_id):
    stub = _HTTPStub(source_id)
    monkeypatch.setattr(model_client, "build_opener", lambda *_args: stub)
    settings = {
        "ENABLED": "true", "PROVIDER": "stub", "BASE_URL": "https://example.com/v1",
        "API_KEY": "fake-key", "MODEL": "fixture-research", "MAX_COST_USD": "1",
        "INPUT_COST_PER_MILLION": "1", "OUTPUT_COST_PER_MILLION": "2",
    }
    settings.update({f"{role.upper()}_MODEL": f"fixture-{role}" for role in ROLES})
    for key, value in settings.items():
        monkeypatch.setenv(f"ENGINE_LLM_{key}", value)
    return stub


def seed_stock_financials(tmp_path, monkeypatch, *, reviewed):
    fact = FinancialFact(
        fact_id="fixture-quarter-profit", symbol="601118", issuer_name="Synthetic issuer",
        metric="net_profit_attributable", value="125000", unit="CNY", value_scale="yuan",
        unit_basis="synthetic financial statement CNY", currency="CNY", period_type="quarter",
        period_label="2025-Q3", period_start="2025-07-01", period_end="2025-09-30",
        statement_scope="consolidated", source_document_id="fixture-quarterly-report",
        source_title="Synthetic quarterly report", source_name="synthetic.v08",
        source_url="https://example.com/synthetic/quarterly-report",
        published_at="2025-10-29T00:00:00+00:00", retrieved_at="2025-10-30T00:00:00+00:00",
        source_page=1, source_section="quarterly summary", source_label="quarterly profit",
        source_excerpt="Synthetic test evidence", audit_status="fixture",
    )
    store = FinancialFactStore(tmp_path)
    store.save([fact])
    if reviewed:
        class ReviewClock(datetime):
            @classmethod
            def now(cls, tz=None):
                return datetime(2025, 12, 31, 1, tzinfo=timezone.utc).astimezone(tz)

        with monkeypatch.context() as clock_patch:
            clock_patch.setattr("investment_engine.financials.datetime", ReviewClock)
            store.review(
                fact.fact_id, reviewer_id="synthetic-reviewer", review_note="Fixture source checked",
                scope_verified=True, unit_verified=True,
            )
    return fact.fact_id


@pytest.mark.parametrize(("entity_id", "asset_class"), [("601118.SH", "stock"), ("510300", "etf")])
def test_full_research_pipeline_and_audit(api, monkeypatch, tmp_path, entity_id, asset_class):
    source_id = seed_demo(tmp_path, entity_id, asset_class, reviewed=True)
    if asset_class == "stock":
        source_id = seed_stock_financials(tmp_path, monkeypatch, reviewed=True)
    stub = configure_stub(monkeypatch, source_id)
    result = api.post("/v1/research/analyze", headers=HEADERS, json={
        "entity_id": entity_id, "asset_class": asset_class, "as_of": AS_OF, "experiment": WINDOWS,
    })
    assert result.status_code == 200, result.text
    report = result.json()
    assert report["release"] == {"version": "0.8.0", "git_sha": "a" * 40}
    id_field = "fact_id" if asset_class == "stock" else "observation_id"
    assert report["confirmed_facts"][0][id_field] == source_id
    assert report["market_expectations"] == []
    assert report["valuation"]["status"] == ("not_evaluable" if asset_class == "stock" else "calculated")
    assert report["portfolio_risk"]["status"] == "unknown"
    assert report["hypothesis_results"][0]["status"] == "supported"
    assert report["agents"]["status"] == "completed", report["agents"]
    assert stub.calls == list(ROLES)
    experiment = report["strategy_experiment"]
    assert experiment["status"] == "completed", experiment
    assert experiment["is_benchmark_metrics"] and experiment["oos_benchmark_metrics"]
    assert len(experiment["data"]["dataset_fingerprint"]) == 64
    assert experiment["costs"]["total_bps_per_side"] == 8
    assert len(experiment["input_bars"]) > 200
    for section in (report, report["agents"], report["decision_report"], experiment):
        assert section["orders_enabled"] is False
        assert section["eligible_for_trading"] is False
        assert section["action"] == "no_trade"
    audit = report.pop("audit")
    url = f"/v1/reports/{audit['report_id']}"
    assert api.get(url).status_code == 401
    assert api.get(url, headers=HEADERS).json()["report"] == report


def test_unreviewed_evidence_never_calls_model(api, monkeypatch, tmp_path):
    source_id = seed_demo(tmp_path, "601118.SH", "stock", reviewed=True)
    seed_stock_financials(tmp_path, monkeypatch, reviewed=False)
    stub = configure_stub(monkeypatch, source_id)
    response = api.post("/v1/research/analyze", headers=HEADERS, json={
        "entity_id": "601118.SH", "as_of": AS_OF,
    })
    assert response.status_code == 200, response.text
    data = response.json()
    assert [record["observation_id"] for record in data["confirmed_facts"]] == [source_id]
    assert data["status"] == "insufficient_evidence"
    assert data["valuation"]["status"] == "not_evaluable"
    assert data["agents"]["status"] == "insufficient_evidence"
    assert stub.calls == []


def test_request_cannot_override_policy_or_use_future_data(api, monkeypatch, tmp_path):
    seed_demo(tmp_path, "510300", "etf", reviewed=True)
    future = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
    request = {"entity_id": "510300", "asset_class": "etf"}
    assert api.post("/v1/research/analyze", json=request).status_code == 401
    for disallowed in ({"as_of": future}, {"model": "fixture"}, {"reviewed_by_human": True},
                       {"orders_enabled": True}, {"as_of": AS_OF, "experiment": {
                           **WINDOWS, "oos_end": "2026-01-02",
                       }}):
        assert api.post(
            "/v1/research/analyze", headers=HEADERS, json={**request, **disallowed},
        ).status_code == 422
    response = api.post("/v1/research/analyze", headers=HEADERS, json={
        **request, "as_of": "2025-12-30T12:30:00+00:00",
    })
    assert response.status_code == 200
    earlier = response.json()
    assert earlier["confirmed_facts"] == []
    assert earlier["hypothesis_results"][0]["status"] == "not_evaluable"
    assert earlier["agents"]["model_audit"]["totals"]["calls"] == 0
    assert api.get("/openapi.json").json()["info"]["version"] == "0.8.0"


def test_confirmed_portfolio_breach_reaches_the_decision_gate(api, monkeypatch):
    def read_risk(*, as_of, entity_id, asset_class):
        assert as_of is None and entity_id == "601118" and asset_class == "stock"
        return {
            "status": "partial", "as_of": AS_OF, "limit_breaches": ["max_single_weight_exceeded"],
            "reasons": ["verified_sector_or_equity_history_unavailable"],
            "orders_enabled": False, "eligible_for_trading": False,
        }

    monkeypatch.setattr("investment_engine.research_analysis.portfolio_risk", read_risk)
    response = api.post("/v1/research/analyze", headers=HEADERS, json={"entity_id": "601118.SH"})
    assert response.status_code == 200, response.text
    report = response.json()
    assert report["as_of"] == AS_OF
    assert report["status"] == "risk_blocked"
    assert report["decision_report"]["risk_flags"][-1] == "limit_breach:max_single_weight_exceeded"
    assert "verified_sector_or_equity_history_unavailable" in report["open_questions"]["portfolio_risk"]
