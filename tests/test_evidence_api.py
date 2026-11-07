from datetime import datetime, timezone

from fastapi.testclient import TestClient

from investment_engine.api import app
from investment_engine.evidence import EvidenceStore, Observation


client = TestClient(app)


def test_industry_observation_endpoint_is_authenticated_and_read_only(monkeypatch, tmp_path):
    monkeypatch.setenv("ENGINE_API_TOKEN", "test-api-token")
    monkeypatch.setenv("ENGINE_DATA_DIR", str(tmp_path))
    store = EvidenceStore(tmp_path)
    item = Observation.create(
        series_id="test.series", subject="Natural rubber",
        factor="multiweek_inventory_trend", metric="inventory", value=100,
        unit="tonne", effective_at="2026-11-05T00:00:00+08:00",
        published_at="2026-11-05T15:00:00+08:00",
        retrieved_at="2026-11-06T00:00:00+08:00",
        source_name="test.source", source_url="https://example.com/source",
        assessment="supporting", reviewed_by_human=True, unit_verified=True,
        series_definition="Test series",
    )
    store.save_observations([item])

    assert client.get("/v1/industry/observations").status_code == 401
    response = client.get(
        "/v1/industry/observations?as_of=2026-11-07T12:00:00%2B08:00",
        headers={"Authorization": "Bearer test-api-token"},
    )
    assert response.status_code == 200
    assert response.json()["count"] == 1
    assert response.json()["observations"][0]["eligible_for_assessment"] is True


def test_disclosure_endpoint_is_authenticated_and_filters_as_of(monkeypatch, tmp_path):
    monkeypatch.setenv("ENGINE_API_TOKEN", "test-api-token")
    monkeypatch.setenv("ENGINE_DATA_DIR", str(tmp_path))
    store = EvidenceStore(tmp_path)
    store.save_documents([{
        "document_id": "cninfo:601118:1", "symbol": "601118", "title": "Q3 report",
        "published_at": "2026-10-29T18:00:00+08:00", "source_name": "cninfo",
        "source_url": "https://static.cninfo.com.cn/report.pdf",
    }])
    assert client.get("/v1/fundamentals/disclosures").status_code == 401
    response = client.get(
        "/v1/fundamentals/disclosures?symbol=601118&as_of=2026-11-07T12:00:00%2B08:00",
        headers={"Authorization": "Bearer test-api-token"},
    )
    assert response.status_code == 200
    assert response.json()["count"] == 1
    assert response.json()["content_status"] == "metadata_only_pdf_not_parsed"
