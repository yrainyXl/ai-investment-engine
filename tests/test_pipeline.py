import json
from datetime import datetime, timezone
from pathlib import Path
import pytest
from investment_engine.analysis import read_bars, review_evidence, flatten_tiptap
from investment_engine.pipeline import run_pipeline
from investment_engine.otw_client import OTWReadClient, OTWError

NOW = datetime(2026, 11, 5, 2, 0, tzinfo=timezone.utc)


def test_fixture_pipeline_denies_execution():
    result = run_pipeline(source="fixture", as_of=NOW)
    assert result["decision"] == "no_trade"
    assert result["research"]["hypothesis"] == "insufficient_evidence"
    assert result["execution"]["order_submitted"] is False
    assert result["execution"]["run_recorded_to_otw"] is False
    assert result["otw_run_preview"]["decision"] == "no_trade"


def test_evidence_future_published_cannot_pass_gate():
    evidence = {"factors": {"actual_supply_change": {
        "state": "supporting", "source_url": "https://example.com", "reviewed_by_human": True,
        "published_at": "2028-01-01T00:00:00Z"}}}
    result = review_evidence(evidence, NOW)
    assert result["factors"]["actual_supply_change"]["status"] == "unknown"


def test_invalid_future_price_series(tmp_path):
    csv_path = tmp_path / "bars.csv"
    csv_path.write_text("date,close\n2027-01-01,5.0\n")
    assert read_bars(csv_path, NOW)["status"] == "invalid"


def test_candidate_trade_still_vetoed(tmp_path):
    from datetime import timedelta
    evidence = {"factors": {name: {"state": "supporting", "source_url": "https://example.com/test",
                                      "reviewed_by_human": True, "published_at": "2026-01-01T00:00:00Z",
                                      "effective_at": "2026-01-01T00:00:00Z",
                                      "retrieved_at": "2026-01-02T00:00:00Z"}
                            for name in ("actual_supply_change", "multiweek_inventory_trend", "profit_pass_through", "priced_market_expectations")}}
    evidence_path = tmp_path / "evidence.json"
    evidence_path.write_text(json.dumps(evidence))
    bars = tmp_path / "bars.csv"
    start = datetime(2026, 6, 1)
    bars.write_text("date,close\n" + "".join(f"{(start + timedelta(days=i)).date()},{5+i*.02:.3f}\n" for i in range(70)))
    result = run_pipeline(source="fixture", as_of=NOW, evidence_path=evidence_path, bars_path=bars)
    assert result["proposal"] == "buy_candidate"
    assert result["risk"]["approved"] is False
    assert result["decision"] == "no_trade"


def test_read_client_refuses_nonlocal_plain_http():
    with pytest.raises(OTWError):
        OTWReadClient("http://example.com", "TEST_TOKEN")


def test_tiptap_extract():
    value = {"type": "doc", "content": [{"type": "paragraph", "content": [{"type": "text", "text": "A"}]}]}
    assert flatten_tiptap(value) == "A"


def test_future_as_of_is_rejected():
    with pytest.raises(ValueError):
        run_pipeline(source="fixture", as_of=datetime(2099, 1, 1, tzinfo=timezone.utc))


def test_otw_dynamic_discovery_and_unique_active():
    payload = {"strategies": [
        {"id": "10000000-0000-4000-8000-000000000001", "name": "Current", "status": "active"},
        {"id": "10000000-0000-4000-8000-000000000003", "name": "Archived", "status": "ended"},
    ]}
    client = OTWReadClient("https://otw.example", "TEST_TOKEN")
    client.get = lambda path: payload
    assert len(client.list_strategies()) == 2
    assert client.resolve_strategy_id() == payload["strategies"][0]["id"]


def test_otw_ambiguous_active_requires_explicit_id():
    first = "10000000-0000-4000-8000-000000000001"
    second = "10000000-0000-4000-8000-000000000003"
    client = OTWReadClient("https://otw.example", "TEST_TOKEN")
    client.get = lambda path: {"strategies": [
        {"id": first, "status": "active"}, {"id": second, "status": "active"}]}
    with pytest.raises(OTWError, match="Multiple active"):
        client.resolve_strategy_id()
    assert client.resolve_strategy_id(second) == second


def test_otw_pipeline_uses_dynamic_selection(monkeypatch):
    client = OTWReadClient("https://otw.example", "TEST_TOKEN")
    fixture = json.loads((Path(__file__).resolve().parents[1] / "fixtures" / "context.synthetic.json").read_text())
    sid = fixture["strategy"]["id"]
    client.get = lambda path: (
        {"strategies": [{"id": sid, "status": "active"}]}
        if path == "/api/paper-trading/strategies" else
        fixture if path.endswith("/context") else
        {"document": {"content": {"type": "doc", "content": []}, "updated_at": "2026-11-01T00:00:00Z"}}
    )
    result = run_pipeline(source="otw", as_of=NOW, client=client)
    assert result["strategy_id"] == sid
    assert result["decision"] == "no_trade"
    assert result["execution"]["order_submitted"] is False


def test_multi_asset_strategy_is_not_silently_reduced_to_first():
    client = OTWReadClient("https://otw.example", "TEST_TOKEN")
    fixture = json.loads((Path(__file__).resolve().parents[1] / "fixtures" / "context.synthetic.json").read_text())
    fixture["account"]["holdings"].append({
        "id": "10000000-0000-4000-8000-000000000004", "symbol": "DEMO002", "quantity": 0
    })
    sid = fixture["strategy"]["id"]
    client.get = lambda path: fixture
    with pytest.raises(OTWError, match="Multi-asset"):
        run_pipeline(source="otw", as_of=NOW, strategy_id=sid, client=client)


def test_legacy_evidence_missing_retrieval_timestamp_is_not_accepted():
    evidence = {"factors": {"actual_supply_change": {
        "state": "supporting", "source_url": "https://example.com/test",
        "reviewed_by_human": True, "published_at": "2026-01-01T00:00:00Z",
        "effective_at": "2026-01-01T00:00:00Z",
    }}}
    result = review_evidence(evidence, NOW)
    assert result["factors"]["actual_supply_change"]["status"] == "unknown"


def test_read_bars_excludes_unfinished_same_day_candle(tmp_path):
    from datetime import timedelta
    start = datetime(2026, 9, 1)
    rows = [(start + timedelta(days=i)).date().isoformat() for i in range(60)]
    rows.append("2026-11-05")
    csv_path = tmp_path / "bars.csv"
    csv_path.write_text("date,close\n" + "".join(f"{day},10.0\n" for day in rows))
    result = read_bars(csv_path, NOW)  # 10:00 Shanghai
    assert result["status"] == "computed"
    assert result["bar_count"] == 60
    assert result["last_bar"] == rows[-2]
