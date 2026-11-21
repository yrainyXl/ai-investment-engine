from copy import deepcopy
from datetime import datetime, timedelta, timezone
from io import BytesIO
import json

import pytest

from investment_engine.research_portfolio import portfolio_risk

STRATEGY_ID = "10000000-0000-4000-8000-000000000001"
PORTFOLIO_ID = "10000000-0000-4000-8000-000000000002"
STRATEGY_PATH = "/api/paper-trading/strategies"
CONTEXT_PATH = f"{STRATEGY_PATH}/{STRATEGY_ID}/context"


@pytest.fixture
def otw(monkeypatch):
    monkeypatch.setenv("ENGINE_OTW_RESEARCH_BASE_URL", "https://otw.example.test")
    monkeypatch.setenv("ENGINE_OTW_RESEARCH_TOKEN", "fixture-test-token")
    monkeypatch.setenv("ENGINE_OTW_RESEARCH_STRATEGIES", json.dumps({"stock:601118": STRATEGY_ID}))
    now = datetime.now(timezone.utc)
    listed = {"id": STRATEGY_ID, "portfolio_id": PORTFOLIO_ID, "status": "active"}
    body = {
        "strategy": {**listed, "currency": "CNY", "max_position_pct": 0.5},
        "portfolio": {"id": PORTFOLIO_ID, "currency": "CNY"},
        "account": {
            "calculated_at": f"{now.date().isoformat()} {now.hour}:{now.minute:02d}:{now.second:02d}.000000000 +00:00:00",
            "cash": 20, "equity": 100, "market_value": 80, "currency": "CNY",
            "market_value_complete": True, "unpriced_holdings": [],
            "holdings": [{
                "portfolio_id": PORTFOLIO_ID, "symbol": "601118.SH", "asset_class": "stock",
                "currency": "CNY", "quantity": 10, "price": 8, "market_value": 80,
                "last_price_at": (now - timedelta(minutes=1)).isoformat(),
            }],
        },
    }
    calls = []

    class FakeOpener:
        def open(self, request, timeout):
            calls.append((request.get_method(), request.selector))
            assert request.host == "otw.example.test"
            assert request.get_header("Authorization") == "Bearer fixture-test-token"
            assert request.data is None and request.get_method() == "GET"
            assert timeout == 5
            payload = {
                STRATEGY_PATH: {"strategies": [listed]},
                CONTEXT_PATH: body,
            }[request.selector]
            return BytesIO(json.dumps(payload).encode())

    def fake_build_opener(*handlers):
        assert len(handlers) == 1 and type(handlers[0]).__name__ == "_NoRedirects"
        return FakeOpener()

    monkeypatch.setattr("investment_engine.otw_client.build_opener", fake_build_opener)
    return body, calls


def assess(as_of=None):
    return portfolio_risk(entity_id="601118.SH", asset_class="stock", as_of=as_of)


def test_otw_reads_only_get_and_computes_verified_concentration(otw):
    _, calls = otw
    report = assess()
    assert calls == [("GET", STRATEGY_PATH), ("GET", CONTEXT_PATH)]
    assert report["status"] == "partial"
    assert report["asset_weight"] == "0.8"
    assert report["cash_weight"] == "0.2"
    assert report["holding_weights"] == [{"asset_class": "stock", "symbol": "601118", "weight": "0.8"}]
    assert report["limit_breaches"] == ["max_single_weight_exceeded"]
    assert report["sector_exposure"]["status"] == "unknown"
    assert report["max_drawdown"]["status"] == "unknown"
    assert report["orders_enabled"] is False
    assert report["eligible_for_trading"] is False


def test_historical_and_unconfigured_do_not_call_otw(otw, monkeypatch):
    _, calls = otw
    assert assess(datetime.now(timezone.utc) - timedelta(days=1))["reasons"] == [
        "historical_account_snapshot_unavailable"
    ]
    monkeypatch.delenv("ENGINE_OTW_RESEARCH_TOKEN")
    assert assess()["reasons"] == ["otw_rest_credentials_unavailable"]
    assert calls == []


@pytest.mark.parametrize(("change", "reason"), [
    (("strategy", "portfolio_id", "wrong-portfolio"), "strategy_portfolio_mismatch"),
    (("account", "unpriced_holdings", [{"fixture": "missing quote"}]), "account_contains_unpriced_holdings"),
    (("holding", "currency", "USD"), "mixed_holding_currencies"),
    (("holding", "last_price_at", "2020-01-01T00:00:00Z"), "holding_quote_missing_stale_or_after_snapshot"),
    (("holding", "last_price_at", "2999-01-01T00:00:00Z"), "holding_quote_missing_stale_or_after_snapshot"),
    (("holding", "price", None), "holding_market_value_or_price_invalid"),
    (("holding", "market_value", 79), "position_value_does_not_reconcile_with_quote"),
    (("account", "equity", 110), "cash_and_holdings_do_not_reconcile_with_equity"),
    (("account", "calculated_at", "2999-01-01T00:00:00Z"), "account_snapshot_not_available_as_of"),
])
def test_account_risk_fails_closed(otw, change, reason):
    body, _ = otw
    part, field, value = change
    if part == "holding":
        body["account"]["holdings"][0][field] = deepcopy(value)
    else:
        body[part][field] = deepcopy(value)
    report = assess()
    assert report["status"] == "unknown"
    assert report["reasons"] == [reason]
    assert "holding_weights" not in report and report["orders_enabled"] is False
