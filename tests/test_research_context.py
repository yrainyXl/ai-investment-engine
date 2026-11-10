"""Tests for the schema-v1 research context builder.

Fixtures deliberately use two unrelated industries (baijiu vs lithium battery)
and an ETF so nothing depends on a single hard-coded entity. The builder must
stay read-only and must never promote unreviewed facts.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from investment_engine.evidence import EvidenceError, EvidenceStore, Observation
from investment_engine.financials import FinancialFact, FinancialFactStore
from investment_engine.market_data import Bar, MarketDataStore
from investment_engine.research_context import build_research_context

SHANGHAI = ZoneInfo("Asia/Shanghai")
NOW = datetime.now(timezone.utc).replace(microsecond=0)
# One hour ahead keeps reviews written during the test visible at the cutoff.
AS_OF = max(NOW + timedelta(hours=1), datetime(2026, 11, 10, 12, 0, tzinfo=SHANGHAI))

TOP_LEVEL_KEYS = {
    "schema_version",
    "entity_id",
    "asset_class",
    "as_of",
    "data_status",
    "financial_facts",
    "industry_observations",
    "missing_evidence",
    "provenance_warnings",
}

LIQUOR = "600519"    # Kweichow Moutai (baijiu)
BATTERY = "300750"   # CATL (lithium battery)
ETF = "510300"       # CSI 300 ETF

PUBLISHED = "2026-10-29T18:00:00+08:00"
RETRIEVED = "2026-10-30T09:00:00+08:00"


def make_fact(symbol, metric, value, fact_id, *, published_at=PUBLISHED,
              retrieved_at=RETRIEVED, period_type="quarter"):
    return FinancialFact(
        fact_id=fact_id,
        symbol=symbol,
        issuer_name=f"Issuer {symbol}",
        metric=metric,
        value=value,
        unit="CNY",
        value_scale="yuan",
        unit_basis="report table states 元 / 人民币",
        currency="CNY",
        period_type=period_type,
        period_label="2026-Q3",
        period_start="2026-07-01",
        period_end="2026-09-30",
        statement_scope="consolidated",
        source_document_id=f"cninfo:{symbol}:2026q3",
        source_title="2026年第三季度报告",
        source_name="cninfo.official_disclosure_pdf",
        source_url=f"https://static.cninfo.com.cn/{symbol}-2026q3.pdf",
        published_at=published_at,
        retrieved_at=retrieved_at,
        source_page=3,
        source_section="consolidated_income_statement",
        source_label=metric,
        source_excerpt="reported row excerpt",
        audit_status="unaudited",
    )


def make_observation(*, series_id, subject, metadata, assessment="supporting",
                     reviewed=True, unit_verified=True, value=100.0, unit="CNY/tonne"):
    return Observation.create(
        series_id=series_id,
        subject=subject,
        factor="industry_price_trend",
        metric="spot_price",
        value=value,
        unit=unit,
        effective_at="2026-09-30T00:00:00+08:00",
        published_at="2026-10-08T10:00:00+08:00",
        retrieved_at="2026-10-09T10:00:00+08:00",
        source_name="test.industry.source",
        source_url=f"https://example.com/{series_id}",
        evidence_type="confirmed_fact",
        assessment=assessment,
        reviewed_by_human=reviewed,
        unit_verified=unit_verified,
        series_definition="Synthetic but explicitly dated industry series for tests.",
        metadata=metadata,
    )


def make_bars(symbol, *, asset_type="stock", adjustment="qfq",
              fetched_at="2026-10-12T16:00:00+08:00"):
    exchange = "SSE" if symbol.startswith(("6", "5")) else "SZSE"
    closes = (10.0, 10.1, 10.2, 10.3)
    days = ("2026-09-28", "2026-09-29", "2026-09-30", "2026-10-09")
    bars = []
    for day, close in zip(days, closes):
        bars.append(Bar(
            symbol=symbol, exchange=exchange, asset_type=asset_type, interval="1d",
            timestamp=day, open=close - 0.1, high=close + 0.1, low=close - 0.2,
            close=close, volume=1000.0, amount=10000.0, source="test.market.source",
            fetched_at=fetched_at, adjustment=adjustment, currency="CNY",
            volume_unit="lots",
        ))
    return bars


def seed_two_industries(root: Path):
    facts = FinancialFactStore(root)
    facts.save([
        make_fact(LIQUOR, "revenue", "1000", "liquor-revenue"),
        make_fact(LIQUOR, "net_profit_attributable", "200", "liquor-profit"),
        make_fact(BATTERY, "revenue", "2000", "battery-revenue"),
    ])
    facts.review(
        "liquor-revenue",
        reviewer_id="human-reviewer",
        review_note="Checked the official source page, statement scope and unit.",
        unit_verified=True,
        scope_verified=True,
    )

    EvidenceStore(root).save_observations([
        make_observation(
            series_id="cn.baijiu.wholesale_price",
            subject="Baijiu wholesale price",
            metadata={"entity_ids": [LIQUOR]},
        ),
        make_observation(
            series_id="cn.lithium.carbonate_price",
            subject="Lithium carbonate spot price",
            metadata={"symbol": BATTERY},
            assessment="unassessed",
            reviewed=False,
        ),
        make_observation(
            series_id="cn.rubber.inventory",
            subject="Natural rubber inventory",
            metadata={},
        ),
    ])

    market = MarketDataStore(root)
    market.save(make_bars(LIQUOR))
    market.save(make_bars(BATTERY))


def snapshot_tree(root: Path) -> dict[str, bytes]:
    return {
        str(path.relative_to(root)): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def test_two_industries_stay_isolated_and_reviews_are_not_upgraded(tmp_path):
    root = tmp_path / "store"
    seed_two_industries(root)
    before = snapshot_tree(root)

    liquor = build_research_context(root=root, entity_id=LIQUOR, as_of=AS_OF)

    assert set(liquor) == TOP_LEVEL_KEYS
    assert liquor["schema_version"] == 1
    assert liquor["entity_id"] == LIQUOR
    assert liquor["asset_class"] == "stock"
    assert liquor["as_of"] == AS_OF.isoformat()
    assert liquor["data_status"] == "available"

    liquor_facts = {item["fact_id"]: item for item in liquor["financial_facts"]}
    assert {item["symbol"] for item in liquor["financial_facts"]} == {LIQUOR}
    revenue = liquor_facts["liquor-revenue"]
    assert revenue["eligible_for_assessment"] is True
    assert revenue["reviewed_by_human"] is True
    assert revenue["unit_verified"] is True
    assert revenue["scope_verified"] is True
    assert revenue["reviewer_id"] == "human-reviewer"
    assert revenue["unit"] == "CNY"
    assert revenue["value_scale"] == "yuan"
    assert revenue["value"] == "1000"
    assert revenue["period_label"] == "2026-Q3"
    assert revenue["period_type"] == "quarter"
    assert revenue["period_end"] == "2026-09-30"
    assert revenue["source_url"].startswith("https://")
    assert revenue["audit_status"] == "unaudited"
    assert revenue["assessment"] == "unassessed"
    assert "eligible_for_assessment" in revenue["quality_flags"]

    profit = liquor_facts["liquor-profit"]
    assert profit["eligible_for_assessment"] is False
    assert profit["reviewed_by_human"] is False
    assert {"human_review_pending", "unit_unverified"} <= set(profit["quality_flags"])
    warning_codes = {item["code"] for item in liquor["provenance_warnings"]}
    assert "financial_facts_pending_review" in warning_codes
    assert "financial_facts_unaudited_or_unknown" in warning_codes

    assert [item["series_id"] for item in liquor["industry_observations"]] == [
        "cn.baijiu.wholesale_price",
    ]
    observation = liquor["industry_observations"][0]
    assert observation["eligible_for_assessment"] is True
    assert observation["reviewed_by_human"] is True
    assert observation["unit"] == "CNY/tonne"
    assert "eligible_for_assessment" in observation["quality_flags"]
    # The unlinked rubber series is visible in the store but never attributed here.
    assert "industry_observations_unlinked" in warning_codes

    battery = build_research_context(root=root, entity_id=BATTERY, as_of=AS_OF)
    assert battery["data_status"] == "available"
    assert {item["symbol"] for item in battery["financial_facts"]} == {BATTERY}
    battery_fact = battery["financial_facts"][0]
    assert battery_fact["fact_id"] == "battery-revenue"
    assert battery_fact["eligible_for_assessment"] is False
    assert "human_review_pending" in battery_fact["quality_flags"]
    assert [item["series_id"] for item in battery["industry_observations"]] == [
        "cn.lithium.carbonate_price",
    ]
    battery_obs = battery["industry_observations"][0]
    assert battery_obs["eligible_for_assessment"] is False
    assert battery_obs["assessment"] == "unassessed"
    assert "industry_observations_not_eligible" in {
        item["code"] for item in battery["provenance_warnings"]
    }

    # No cross-industry contamination in either direction.
    assert "cn.lithium.carbonate_price" not in {
        item["series_id"] for item in liquor["industry_observations"]
    }
    assert "cn.baijiu.wholesale_price" not in {
        item["series_id"] for item in battery["industry_observations"]
    }
    assert "liquor-revenue" not in {
        item["fact_id"] for item in battery["financial_facts"]
    }

    # The builder is read-only: the store files are byte-identical afterwards.
    assert snapshot_tree(root) == before


def test_as_of_cutoff_excludes_future_facts_bars_and_reviews(tmp_path):
    root = tmp_path / "store"
    cutoff = datetime(2026, 10, 31, 12, 0, tzinfo=SHANGHAI)
    facts = FinancialFactStore(root)
    facts.save([
        make_fact(
            LIQUOR, "revenue", "1000", "visible-fact",
            published_at="2026-10-01T09:00:00+08:00",
            retrieved_at="2026-10-02T09:00:00+08:00",
        ),
        make_fact(
            LIQUOR, "net_profit_attributable", "200", "future-fact",
            published_at="2026-11-02T09:00:00+08:00",
            retrieved_at="2026-11-03T09:00:00+08:00",
        ),
    ])
    # Review happens now, i.e. after the requested cutoff.
    facts.review(
        "visible-fact",
        reviewer_id="human-reviewer",
        review_note="Verified against the official disclosure.",
        unit_verified=True,
        scope_verified=True,
    )
    market = MarketDataStore(root)
    market.save(make_bars(LIQUOR, fetched_at="2026-11-05T16:00:00+08:00"))

    context = build_research_context(root=root, entity_id=LIQUOR, as_of=cutoff)

    assert context["as_of"] == cutoff.isoformat()
    assert [item["fact_id"] for item in context["financial_facts"]] == ["visible-fact"]
    visible = context["financial_facts"][0]
    assert visible["visible_as_of"] is True
    # The review did not exist at the cutoff, so the store hides it and we keep that.
    assert visible["reviewed_by_human"] is False
    assert visible["reviewed_at"] is None
    assert visible["eligible_for_assessment"] is False
    assert context["data_status"] == "partial"

    codes = {item["code"] for item in context["provenance_warnings"]}
    assert "market_bars_fetched_after_as_of" in codes
    missing = {item["evidence_type"] for item in context["missing_evidence"]}
    assert "market_bars" in missing


def test_unavailable_context_is_explicit_and_creates_no_directories(tmp_path):
    missing_root = tmp_path / "never-created"
    context = build_research_context(root=missing_root, entity_id=LIQUOR, as_of=AS_OF)

    assert context["data_status"] == "unavailable"
    assert context["financial_facts"] == []
    assert context["industry_observations"] == []
    assert {item["evidence_type"] for item in context["missing_evidence"]} == {
        "financial_facts", "industry_observations", "market_bars",
    }
    assert all(item["entity_id"] == LIQUOR for item in context["missing_evidence"])
    assert context["provenance_warnings"] == []
    assert not missing_root.exists()

    empty_root = tmp_path / "empty"
    empty_root.mkdir()
    empty_context = build_research_context(root=empty_root, entity_id=LIQUOR, as_of=AS_OF)
    assert empty_context["data_status"] == "unavailable"
    assert list(empty_root.iterdir()) == []


def test_etf_context_and_quarantine_warning(tmp_path):
    root = tmp_path / "etf"
    market = MarketDataStore(root)
    market.save(make_bars(ETF, asset_type="etf", adjustment="qfq"))
    market.save_quarantine(
        symbol=ETF, asset_type="etf", adjustment="qfq",
        source="test.market.source",
        records=[{"date": "2026-10-08", "reason": "test-only excluded row"}],
    )

    context = build_research_context(
        root=root, entity_id=ETF, as_of=AS_OF, asset_class="etf",
    )
    assert context["asset_class"] == "etf"
    assert context["data_status"] == "available"
    assert context["financial_facts"] == []
    missing = {item["evidence_type"] for item in context["missing_evidence"]}
    assert "market_bars" not in missing
    codes = {item["code"] for item in context["provenance_warnings"]}
    assert "market_data_quarantined_rows" in codes

    # A stock with only facts is partial, not silently complete.
    facts_only = tmp_path / "facts-only"
    FinancialFactStore(facts_only).save([
        make_fact(LIQUOR, "revenue", "1000", "only-revenue"),
    ])
    partial = build_research_context(root=facts_only, entity_id=LIQUOR, as_of=AS_OF)
    assert partial["data_status"] == "partial"
    assert "market_bars" in {
        item["evidence_type"] for item in partial["missing_evidence"]
    }


def test_industry_observations_require_an_explicit_entity_link(tmp_path):
    root = tmp_path / "observations"
    store = EvidenceStore(root)
    store.save_observations([
        make_observation(
            series_id="cn.rubber.inventory",
            subject="Natural rubber inventory",
            metadata={},
        ),
    ])

    context = build_research_context(root=root, entity_id=LIQUOR, as_of=AS_OF)
    assert context["industry_observations"] == []
    assert context["data_status"] == "unavailable"
    missing = next(
        item for item in context["missing_evidence"]
        if item["evidence_type"] == "industry_observations"
    )
    assert "no entity link" in missing["reason"]
    assert "industry_observations_unlinked" in {
        item["code"] for item in context["provenance_warnings"]
    }

    # A subject that exactly matches the entity is linked, with eligibility intact.
    store.save_observations([
        make_observation(
            series_id="cn.custom.entity_series",
            subject=LIQUOR,
            metadata={},
        ),
    ])
    linked = build_research_context(root=root, entity_id=LIQUOR, as_of=AS_OF)
    assert [item["series_id"] for item in linked["industry_observations"]] == [
        "cn.custom.entity_series",
    ]
    assert linked["data_status"] == "partial"


def test_invalid_inputs_fail_fast(tmp_path):
    with pytest.raises(ValueError, match="timezone-aware"):
        build_research_context(
            root=tmp_path, entity_id=LIQUOR,
            as_of=datetime(2026, 11, 10, 12, 0),
        )
    with pytest.raises(ValueError, match="entity_id"):
        build_research_context(root=tmp_path, entity_id="../secrets", as_of=AS_OF)
    with pytest.raises(ValueError, match="entity_id"):
        build_research_context(root=tmp_path, entity_id="", as_of=AS_OF)
    with pytest.raises(ValueError, match="asset_class"):
        build_research_context(
            root=tmp_path, entity_id=LIQUOR, as_of=AS_OF, asset_class="crypto",
        )


def test_corrupt_store_fails_closed_instead_of_faking_unavailable(tmp_path):
    root = tmp_path / "corrupt"
    evidence = root / "evidence"
    evidence.mkdir(parents=True)
    (evidence / "financial_facts.json").write_text("{not valid json", encoding="utf-8")

    with pytest.raises(EvidenceError):
        build_research_context(root=root, entity_id=LIQUOR, as_of=AS_OF)
