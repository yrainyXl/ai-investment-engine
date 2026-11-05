from datetime import datetime, timezone

from investment_engine.research import assess_hypotheses


AS_OF = datetime(2026, 11, 5, tzinfo=timezone.utc)


def test_inference_never_counts_as_confirmed_fact():
    result = assess_hypotheses({
        "inferences": [{"factor": "actual_supply_change", "title": "supply",
                        "detail": "supply may fall", "source_url": "https://example.com",
                        "published_at": "2026-11-01T00:00:00Z",
                        "effective_at": "2026-11-01T00:00:00Z",
                        "retrieved_at": "2026-11-02T00:00:00Z",
                        "reviewed_by_human": True}]
    }, as_of=AS_OF)
    assert result["checks"]["actual_supply_change"]["status"] == "unknown"
    assert "actual_supply_change" in result["missing_factors"]


def test_evidence_retrieved_after_as_of_is_not_eligible():
    result = assess_hypotheses({
        "facts": [{"factor": "actual_supply_change", "title": "supply",
                   "detail": "confirmed supply decline", "source_url": "https://example.com",
                   "published_at": "2026-11-01T00:00:00Z",
                   "effective_at": "2026-11-01T00:00:00Z",
                   "retrieved_at": "2026-11-06T00:00:00Z",
                   "reviewed_by_human": True}]
    }, as_of=AS_OF)
    assert result["evidence_register"]["confirmed_facts"][0]["accepted_for_assessment"] is False


def test_price_evidence_does_not_fill_inventory_or_profit_gates():
    result = assess_hypotheses({"facts": [{
        "factor": "actual_supply_change", "title": "supply",
        "detail": "confirmed", "source_url": "https://example.com",
        "published_at": "2026-11-01T00:00:00Z",
        "effective_at": "2026-11-01T00:00:00Z",
        "retrieved_at": "2026-11-02T00:00:00Z", "reviewed_by_human": True,
    }]}, as_of=AS_OF)
    assert result["checks"]["multiweek_inventory_trend"]["missing"] is True
    assert result["checks"]["profit_pass_through"]["missing"] is True
