from investment_engine.market_cli import match_quarter_report_documents


def test_match_cninfo_ordinal_quarter_report_title():
    docs = [
        {"document_id": "q3", "title": "海南橡胶：2026年第三季度报告"},
        {"document_id": "old", "title": "海南橡胶：2025年第三季度报告"},
        {"document_id": "q2", "title": "海南橡胶：2026年第二季度报告"},
    ]
    matches = match_quarter_report_documents(docs, "2026", "3")
    assert [item["document_id"] for item in matches] == ["q3"]


def test_match_legacy_non_ordinal_quarter_report_title():
    docs = [{"document_id": "legacy", "title": "2026年三季度报告"}]
    assert match_quarter_report_documents(docs, "2026", "3") == docs


def test_quarter_report_matching_does_not_match_other_quarters_or_years():
    docs = [
        {"title": "2026年第四季度报告"},
        {"title": "2025年第三季度报告"},
        {"title": "2026年第三季度业绩预告"},
    ]
    assert match_quarter_report_documents(docs, "2026", "3") == []
