import pytest

from investment_engine.report_store import ReportStore


def test_report_store_saves_and_lists_reports(tmp_path):
    store = ReportStore(tmp_path)
    saved = store.save("research", "strategy-123", {"decision": "research_only"})
    assert saved["report_id"]
    items = store.list(kind="research")
    assert len(items) == 1
    assert items[0]["report_id"] == saved["report_id"]
    assert items[0]["subject"] == "strategy-123"


def test_report_store_rejects_path_traversal(tmp_path):
    store = ReportStore(tmp_path)
    with pytest.raises(ValueError):
        store.save("../secret", "strategy", {})


def test_report_store_limits_list_size(tmp_path):
    store = ReportStore(tmp_path)
    with pytest.raises(ValueError):
        store.list(limit=500)
