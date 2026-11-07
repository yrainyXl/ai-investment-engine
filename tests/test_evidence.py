from datetime import datetime, timezone

import pytest

from investment_engine.evidence import EvidenceError, EvidenceStore, Observation


AS_OF = datetime(2026, 11, 7, 12, 0, tzinfo=timezone.utc)


def make_observation(**overrides):
    values = {
        "series_id": "rubber.test.series",
        "subject": "Natural rubber",
        "factor": "multiweek_inventory_trend",
        "metric": "inventory",
        "value": 120000,
        "unit": "tonne",
        "effective_at": "2026-11-05T00:00:00+08:00",
        "published_at": "2026-11-05T15:00:00+08:00",
        "retrieved_at": "2026-11-06T00:00:00+08:00",
        "source_name": "test.official",
        "source_url": "https://example.com/source",
        "assessment": "supporting",
        "reviewed_by_human": True,
        "unit_verified": True,
        "series_definition": "A test inventory series with a verified scope and unit.",
    }
    values.update(overrides)
    return Observation.create(**values)


def test_observation_is_eligible_only_after_all_provenance_gates():
    item = make_observation()
    assert item.eligibility(as_of=AS_OF)["eligible_for_assessment"] is True

    unreviewed = make_observation(reviewed_by_human=False)
    assert unreviewed.eligibility(as_of=AS_OF)["eligible_for_assessment"] is False

    unknown_unit = make_observation(unit_verified=False)
    assert unknown_unit.eligibility(as_of=AS_OF)["eligible_for_assessment"] is False

    missing_publication = make_observation(published_at=None)
    result = missing_publication.eligibility(as_of=AS_OF)
    assert result["eligible_for_assessment"] is False
    assert "publication timestamp is unknown" in result["reasons"]


def test_future_retrieval_and_inference_are_never_eligible():
    future = make_observation(retrieved_at="2026-11-08T00:00:00+08:00")
    assert future.eligibility(as_of=AS_OF)["eligible_for_assessment"] is False

    inference = make_observation(evidence_type="inference")
    assert inference.eligibility(as_of=AS_OF)["eligible_for_assessment"] is False


def test_observation_requires_https_and_timezone():
    with pytest.raises(EvidenceError, match="HTTPS"):
        make_observation(source_url="http://example.com/source")
    with pytest.raises(EvidenceError, match="timezone"):
        make_observation(effective_at="2026-11-05T00:00:00")


def test_store_upserts_and_reports_point_in_time_eligibility(tmp_path):
    store = EvidenceStore(tmp_path)
    first = make_observation()
    saved = store.save_observations([first])
    assert saved["total_count"] == 1
    assert saved["storage_file"] == "evidence/industry_observations.json"

    # A changed value without a source revision id is retained as a separate revision.
    revised = make_observation(value=118000)
    assert revised.observation_id != first.observation_id
    assert store.save_observations([revised])["total_count"] == 2

    items = store.list_observations(as_of=AS_OF)
    assert len(items) == 2
    assert {item["value"] for item in items} == {120000, 118000}
    assert all(item["visible_as_of"] is True for item in items)
    assert all(item["eligible_for_assessment"] is True for item in items)


def test_store_keeps_records_but_marks_unknown_publication_not_visible_as_of(tmp_path):
    store = EvidenceStore(tmp_path)
    item = make_observation(published_at=None, reviewed_by_human=False, unit_verified=False)
    store.save_observations([item])
    result = store.list_observations(as_of=AS_OF)[0]
    assert result["visible_as_of"] is False
    assert result["eligible_for_assessment"] is False


def test_documents_are_filtered_by_publication_time(tmp_path):
    store = EvidenceStore(tmp_path)
    docs = [
        {
            "document_id": "cninfo:601118:1", "symbol": "601118", "title": "2026 Q3 report",
            "published_at": "2026-10-29T18:00:00+08:00", "source_name": "cninfo",
            "source_url": "https://static.cninfo.com.cn/report.pdf",
        },
        {
            "document_id": "cninfo:601118:2", "symbol": "601118", "title": "Future report",
            "published_at": "2026-11-09T18:00:00+08:00", "source_name": "cninfo",
            "source_url": "https://static.cninfo.com.cn/future.pdf",
        },
    ]
    assert store.save_documents(docs)["total_count"] == 2
    visible = store.list_documents(symbol="601118", as_of=AS_OF)
    assert [item["document_id"] for item in visible] == ["cninfo:601118:1"]


def test_documents_reject_non_https_sources(tmp_path):
    store = EvidenceStore(tmp_path)
    with pytest.raises(EvidenceError, match="HTTPS"):
        store.save_documents([{
            "document_id": "x", "symbol": "601118", "title": "report",
            "published_at": "2026-10-29T18:00:00+08:00", "source_name": "cninfo",
            "source_url": "http://example.com/report.pdf",
        }])
