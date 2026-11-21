from datetime import datetime, timedelta, timezone
import json

import pytest

from investment_engine.evidence import EvidenceError, EvidenceStore, Observation
from investment_engine.financials import FinancialFact, FinancialFactStore
from investment_engine.market_data import Bar, MarketDataStore
from investment_engine.research_inputs import resolve_research_inputs

CUTOFF = datetime(2025, 4, 3, 9, tzinfo=timezone.utc)
RETRIEVED = "2025-04-03T08:00:00+00:00"


def market(root, *, symbol="601118", kind="stock", adjustment="none", currency="CNY"):
    rows = [
        Bar(
            symbol, "SSE", kind, "1d", timestamp, 10, 11, 9, 10, 700, 7000,
            "fixture.synthetic", fetched, adjustment, currency, "lots",
        )
        for timestamp, fetched in (
            ("2025-04-01", RETRIEVED),
            ("2025-04-02", RETRIEVED),
            ("2025-04-03", RETRIEVED),
            ("2025-04-04", "2025-04-04T09:00:00+00:00"),
        )
    ]
    MarketDataStore(root).save(rows)


def financial(fact_id, metric, value, *, period_type, unit, scale, period_start):
    return FinancialFact(
        fact_id=fact_id, symbol="601118", issuer_name="Synthetic issuer",
        metric=metric, value=value, unit=unit, value_scale=scale, unit_basis="document unit",
        currency="CNY", period_type=period_type, period_label="2024-Q4",
        period_start=period_start, period_end="2024-12-31", statement_scope="consolidated",
        source_document_id="source-v1", source_title="Synthetic report", source_name="fixture.synthetic",
        source_url="https://example.test/financial-report",
        published_at="2025-03-31T07:00:00+00:00", retrieved_at="2025-04-01T08:00:00+00:00",
        source_page=1, source_section="statement", source_label=metric, source_excerpt="fixture",
        audit_status="audited",
    )


def reviewed_store(root, facts, monkeypatch):
    class ReviewClock(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2025, 4, 2, 8, tzinfo=tz)

    store = FinancialFactStore(root)
    store.save(facts)
    monkeypatch.setattr("investment_engine.financials.datetime", ReviewClock)
    for fact in facts:
        store.review(
            fact.fact_id, reviewer_id="fixture-reviewer", review_note="Checked synthetic source",
            unit_verified=True, scope_verified=True,
        )
    return store


def stock(root, as_of=CUTOFF):
    return resolve_research_inputs(root=root, entity_id="601118.SH", asset_class="stock", as_of=as_of)


def test_market_history_cutoff_adjustment_and_invalid_currency(tmp_path):
    market(tmp_path, adjustment="qfq")
    informational = stock(tmp_path)
    assert informational["bars"] == []
    assert informational["market_data"]["adjusted_reference"]["status"] == "available"
    assert informational["valuation"]["status"] == "not_evaluable"

    market(tmp_path, currency="USD")
    invalid = stock(tmp_path)
    assert invalid["bars"] == []
    assert invalid["market_data"]["reasons"] == ["market_quality_failed"]

    market(tmp_path)
    report = stock(tmp_path)
    assert report["context"]["entity_id"] == "601118"
    assert [row.timestamp for row in report["bars"]] == ["2025-04-01", "2025-04-02", "2025-04-03"]
    assert report["market_data"]["excluded_incomplete_or_future"] == 1
    assert report["market_data"]["source"] == "fixture.synthetic"
    assert report["market_data"]["dataset_fingerprint"]

    before_fetch = stock(tmp_path, CUTOFF - timedelta(minutes=90))
    assert before_fetch["bars"] == []
    assert before_fetch["market_data"]["excluded_unknown_at_cutoff"] == 3
    stale = stock(tmp_path, CUTOFF + timedelta(days=15))
    assert len(stale["bars"]) == 4  # Historical experiments may still use these point-in-time bars.
    assert stale["valuation"]["status"] == "not_evaluable"
    assert stale["market_data"]["status"] == "stale"


def test_incomplete_candle_and_corrupt_reference_do_not_enter_experiment(tmp_path):
    market(tmp_path)
    (tmp_path / "stock_601118_qfq.csv").write_text("bad\nrow\n")
    cutoff = datetime(2025, 4, 3, 6, tzinfo=timezone.utc)
    report = stock(tmp_path, cutoff)
    assert report["bars"] == []
    assert report["market_data"]["excluded_incomplete_or_future"] == 2
    assert report["market_data"]["adjusted_reference"]["reasons"] == ["invalid_market_series"]


def test_stock_requires_reviewed_ttm_and_book_value(tmp_path, monkeypatch):
    market(tmp_path)
    facts = [
        financial("eps-ttm", "earnings_per_share", "2", period_type="ttm",
                  unit="CNY/share", scale="yuan_per_share", period_start="2024-01-01"),
        financial("bvps", "book_value_per_share", "5", period_type="point_in_time",
                  unit="CNY/share", scale="yuan_per_share", period_start="2024-12-31"),
        financial("earnings-ytd", "earnings_per_share", "100", period_type="year_to_date",
                  unit="CNY/share", scale="yuan_per_share", period_start="2024-01-01"),
    ]
    store = FinancialFactStore(tmp_path)
    store.save(facts)
    assert stock(tmp_path)["valuation"]["pe"]["reasons"] == ["latest_financial_period_pending_review"]
    reviewed_store(tmp_path, facts[1:], monkeypatch)
    assert stock(tmp_path)["valuation"]["pe"]["status"] == "not_evaluable"

    reviewed_store(tmp_path, facts, monkeypatch)
    valuation = stock(tmp_path)["valuation"]
    assert valuation["status"] == "calculated"
    assert valuation["pe"]["source_ids"] == ["eps-ttm"]
    assert valuation["pe"]["value"] == "5.0"
    assert valuation["pb"]["value"] == "2.0"
    assert valuation["dcf"]["status"] == "not_evaluable"
    assert "reviewer_id" not in stock(tmp_path)["confirmed_facts"][0]

    with pytest.raises(EvidenceError, match="valuation evidence"):
        store.save([financial("fake-ttm", "earnings_per_share", "2", period_type="ttm",
                              unit="CNY/share", scale="yuan_per_share", period_start="2024-10-01")])


def test_etf_nav_and_expectations_have_known_sources_and_review_times(tmp_path):
    market(tmp_path, symbol="510300", kind="etf")
    effective = "2025-04-03T07:00:00+00:00"
    common = dict(
        subject="510300", factor="NAV", unit="CNY/share", effective_at=effective,
        published_at="2025-04-03T07:30:00+00:00", retrieved_at=RETRIEVED,
        source_name="fixture.synthetic", source_url="https://example.test/confirmed-nav",
        reviewed_by_human=True, unit_verified=True,
        series_definition="Synthetic NAV CNY per share", assessment="supporting",
    )
    nav = Observation.create(
        series_id="nav", metric="nav_per_share", value=10,
        metadata={"reviewed_at": "2025-04-03T08:30:00+00:00"}, **common,
    )
    forecast = Observation.create(
        series_id="forecast", metric="expected_nav", value=11, evidence_type="market_expectation",
        metadata={"reviewed_at": "2025-04-03T08:31:00+00:00"}, **common,
    )
    unknown_review = Observation.create(
        series_id="other", metric="expected_nav", value=13, evidence_type="market_expectation", **common,
    )
    store = EvidenceStore(tmp_path)
    store.save_observations([nav, forecast, unknown_review])
    report = resolve_research_inputs(root=tmp_path, entity_id="510300", asset_class="etf", as_of=CUTOFF)
    assert report["valuation"]["status"] == "calculated"
    assert report["valuation"]["source_ids"] == [nav.observation_id]
    assert report["valuation"]["premium_to_nav"] == "0"
    assert [row["observation_id"] for row in report["market_expectations"]] == [forecast.observation_id]
    earlier = resolve_research_inputs(
        root=tmp_path, entity_id="510300", asset_class="etf", as_of=CUTOFF - timedelta(minutes=45),
    )
    assert earlier["valuation"]["status"] == "not_evaluable"
    assert earlier["market_expectations"] == []
    assert not any(row["eligible_for_assessment"] for row in earlier["context"]["industry_observations"])


def test_dcf_requires_reviewed_version_and_same_period_source_facts(tmp_path, monkeypatch):
    market(tmp_path)
    reviewed_store(tmp_path, [
        financial("debt", "net_debt", "100", period_type="point_in_time",
                  unit="CNY", scale="yuan", period_start="2024-12-31"),
        financial("shares", "shares_outstanding", "100", period_type="point_in_time",
                  unit="shares", scale="shares", period_start="2024-12-31"),
    ], monkeypatch)
    path = tmp_path / "evidence" / "dcf_assumptions_601118.json"
    assumptions = {
        "entity_id": "601118", "currency": "CNY", "assumptions_id": "fixture-dcf",
        "source_name": "fixture.synthetic", "source_url": "https://example.test/review",
        "assumptions_as_of": "2025-04-02T08:30:00+00:00", "reviewed_at": "2025-04-03T08:30:00+00:00",
        "reviewed_by_human": True, "forecast_fcf": ["100", "200"], "discount_rate": "0.10",
        "terminal_growth": "0.03", "net_debt_source_id": "debt", "shares_outstanding_source_id": "shares",
    }
    path.write_text(json.dumps([assumptions]))
    assert stock(tmp_path, CUTOFF - timedelta(minutes=45))["valuation"]["dcf"]["status"] == "not_evaluable"
    result = stock(tmp_path)["valuation"]["dcf"]
    assert result["status"] == "calculated"
    assert result["source_ids"] == ("debt", "shares")
    assert result["assumptions"]["source_id"] == "fixture-dcf"
