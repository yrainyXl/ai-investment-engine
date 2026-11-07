from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from investment_engine.providers import (
    CNINFO_ANNOUNCEMENT_QUERY, CNINFO_TOP_SEARCH, AkShareRubberInventoryProvider,
    CNInfoDisclosureProvider, ProviderError,
)


class FakeCNInfoProvider(CNInfoDisclosureProvider):
    def __init__(self, responses):
        super().__init__()
        self.responses = responses
        self.requests = []

    def _request_bytes(self, url, *, form=None, referer=None):
        self.requests.append((url, form))
        return b"<html>search page</html>"

    def _request_json(self, url, *, form=None, referer=None):
        self.requests.append((url, form))
        if url == CNINFO_TOP_SEARCH:
            return self.responses["company"]
        if url == CNINFO_ANNOUNCEMENT_QUERY:
            return self.responses["announcements"]
        raise AssertionError(url)


def test_cninfo_provider_resolves_orgid_and_indexes_official_documents():
    provider = FakeCNInfoProvider({
        "company": [{"code": "gssh0601118", "zwjc": "海南橡胶"}],
        "announcements": {
            "totalpages": 1,
            "announcements": [{
                "announcementId": "1226000000",
                "announcementTitle": "海南橡胶：2026年第三季度报告",
                "announcementTime": 1793268000000,
                "adjunctUrl": "finalpage/2026-10-29/1226000000.PDF",
                "adjunctType": "定期报告",
                "secName": "海南橡胶",
            }],
        },
    })
    documents = provider.fetch(
        symbol="601118", start_date="2026-01-01", end_date="2026-11-07",
    )
    assert len(documents) == 1
    item = documents[0]
    assert item["symbol"] == "601118"
    assert item["source_name"] == "cninfo.official_disclosure_index"
    assert item["source_url"] == "https://static.cninfo.com.cn/finalpage/2026-10-29/1226000000.PDF"
    assert item["published_at"].startswith("2026-10-29")
    assert item["content_status"] == "metadata_only_pdf_not_parsed"
    assert "gssh0601118" in provider.requests[-1][1]["stock"]


def test_cninfo_provider_rejects_ambiguous_orgid():
    provider = FakeCNInfoProvider({
        "company": [{"code": "org-a"}, {"code": "org-b"}],
        "announcements": {"totalpages": 0, "announcements": []},
    })
    with pytest.raises(ProviderError, match="unique CNINFO orgId"):
        provider.fetch(symbol="601118", start_date="2026-01-01", end_date="2026-11-07")


def test_cninfo_provider_rejects_invalid_symbol_and_page_bounds():
    provider = FakeCNInfoProvider({"company": [], "announcements": {}})
    with pytest.raises(ProviderError, match="six-digit"):
        provider.fetch(symbol="601118.SH")
    with pytest.raises(ProviderError, match="max_pages"):
        provider.fetch(symbol="601118", max_pages=0)


def test_akshare_inventory_is_labeled_as_delivery_warehouse_proxy(monkeypatch):
    class Frame:
        def to_dict(self, orient):
            assert orient == "records"
            return [
                {"日期": "2026-11-05", "库存": 146940, "收盘价": 15500},
                {"日期": "2026-11-06", "库存": 146680, "收盘价": 15460},
            ]

    calls = []
    fake = SimpleNamespace(
        futures_inventory_99=lambda **kwargs: (calls.append(kwargs) or Frame())
    )
    monkeypatch.setitem(__import__("sys").modules, "akshare", fake)
    observations = AkShareRubberInventoryProvider().fetch(
        as_of=datetime(2026, 11, 7, 0, 0, tzinfo=timezone.utc)
    )
    assert calls == [{"symbol": "天然橡胶"}]
    assert len(observations) == 2
    latest = observations[-1]
    assert latest.value == 146680
    assert latest.metadata["scope_status"] == "unverified"
    assert latest.metadata["price_column_present"] is True
    assert observations[0].metadata["change"] is None
    assert latest.unit == "source_native_unverified"
    assert latest.published_at is None
    assert latest.unit_verified is False
    assert "unknown statistical scope" in latest.series_definition
    assert latest.eligibility(
        as_of=datetime(2026, 11, 7, 12, 0, tzinfo=timezone.utc)
    )["eligible_for_assessment"] is False


def test_akshare_inventory_rejects_empty_and_duplicate_dates(monkeypatch):
    class Empty:
        def to_dict(self, orient):
            return []
    monkeypatch.setitem(__import__("sys").modules, "akshare",
                        SimpleNamespace(futures_inventory_99=lambda **kwargs: Empty()))
    with pytest.raises(ProviderError, match="no natural-rubber"):
        AkShareRubberInventoryProvider().fetch()

    class Duplicate:
        def to_dict(self, orient):
            return [
                {"日期": "2026-11-05", "库存": 10, "增减": 0},
                {"日期": "2026-11-05", "库存": 11, "增减": 1},
            ]
    monkeypatch.setitem(__import__("sys").modules, "akshare",
                        SimpleNamespace(futures_inventory_99=lambda **kwargs: Duplicate()))
    with pytest.raises(ProviderError, match="duplicate"):
        AkShareRubberInventoryProvider().fetch()
