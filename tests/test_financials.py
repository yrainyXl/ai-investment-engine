from datetime import datetime, timezone
from io import BytesIO
from types import SimpleNamespace

import pytest

from investment_engine.financials import (
    CNInfoFinancialReportProvider,
    FinancialFactStore,
    FinancialParseError,
    parse_quarterly_report_pages,
    _compact,
    NUMBER_RE,
)


DOCUMENT = {
    "document_id": "cninfo:601118:20261029-q3",
    "symbol": "601118",
    "issuer_name": "海南天然橡胶产业集团股份有限公司",
    "title": "海南橡胶：2026年第三季度报告",
    "published_at": "2026-10-29T18:00:00+08:00",
    "source_url": "https://static.cninfo.com.cn/finalpage/2026-10-29/601118-q3.pdf",
}

SUMMARY_PAGE = """
海南天然橡胶产业集团股份有限公司 2026年第三季度报告
第三季度财务报表是否经审计 □是 √否
一、主要财务数据
单位：元 币种：人民币
项目 本报告期 本报告期比上年同期增减变动幅度(%) 年初至报告期末 年初至报告期末比上年同期增减变动幅度(%)
营业收入 8,639,374,848.21 -16.14 23,762,244,302.46 -22.28
利润总额 -72,192,335.54 不适用 -300,167,373.65 不适用
归属于上市公司股东的净利润 -81,184,834.30 不适用 -211,664,225.55 不适用
归属于上市公司股东的扣除非经常性损益的净利润 -103,738,200.27 不适用 -392,953,177.71 不适用
经营活动产生的现金流量净额 不适用 不适用 1,349,976,384.61 -16.50
二、股东信息
"""

STATEMENTS_PAGE = """
合并利润表
2026年1—9月
单位：元 币种：人民币 审计类型：未经审计
项目 2026年前三季度（1-9月） 2025年前三季度（1-9月）
一、营业总收入 23,762,244,302.46 30,574,470,082.36
其中：营业收入 23,762,244,302.46 30,574,470,082.36
二、营业总成本 24,781,991,049.50 31,503,920,534.37
其中：营业成本 22,974,588,686.99 29,660,405,418.39
三、营业利润（亏损以“-”号填列） -421,883,756.86 -425,404,169.96
四、利润总额（亏损总额以“-”号填列） -300,167,373.65 -380,918,205.39
五、净利润（净亏损以“-”号填列） -369,303,785.56 -432,309,708.26
归属于母公司股东的净利润（净亏损以“-”号填列） -211,664,225.55 -275,178,641.18
少数股东损益（净亏损以“-”号填列） -157,639,560.01 -157,131,067.08
合并现金流量表
2026年1—9月
单位：元 币种：人民币
经营活动产生的现金流量净额 1,349,976,384.61 1,616,762,229.05
母公司资产负债表
"""


def parsed_facts():
    return parse_quarterly_report_pages(
        [SUMMARY_PAGE, STATEMENTS_PAGE],
        document=DOCUMENT,
        retrieved_at=datetime(2026, 11, 7, 12, 0, tzinfo=timezone.utc),
    )


def test_parser_extracts_quarter_and_ytd_facts_with_period_and_scope():
    facts = parsed_facts()
    by_key = {(item.metric, item.period_type): item for item in facts}

    assert by_key[("revenue", "quarter")].value == "8639374848.21"
    assert by_key[("revenue", "quarter")].period_start == "2026-07-01"
    assert by_key[("revenue", "quarter")].period_end == "2026-09-30"
    assert by_key[("revenue", "year_to_date")].value == "23762244302.46"
    assert by_key[("cost_of_revenue", "year_to_date")].value == "22974588686.99"
    assert by_key[("net_profit_attributable", "quarter")].value == "-81184834.30"
    assert by_key[("net_profit_attributable", "year_to_date")].value == "-211664225.55"
    assert by_key[("net_profit_attributable_ex_nonrecurring", "year_to_date")].value == "-392953177.71"
    assert by_key[("operating_cash_flow", "year_to_date")].value == "1349976384.61"
    assert by_key[("cost_of_revenue", "year_to_date")].statement_scope == "consolidated"
    assert by_key[("cost_of_revenue", "year_to_date")].source_page == 2
    assert by_key[("cost_of_revenue", "year_to_date")].audit_status == "unaudited"


def test_extracted_facts_are_never_auto_approved():
    facts = parsed_facts()
    assert facts
    assert all(item.reviewed_by_human is False for item in facts)
    assert all(item.unit_verified is False for item in facts)
    assert all(item.assessment == "unassessed" for item in facts)
    assert all(item.extraction_status == "machine_extracted_pending_human_review" for item in facts)


def test_parser_rejects_wrong_title_and_missing_statement_sections():
    with pytest.raises(FinancialParseError, match="quarterly reports"):
        parse_quarterly_report_pages(
            [SUMMARY_PAGE, STATEMENTS_PAGE],
            document={**DOCUMENT, "title": "海南橡胶：2026年年度报告"},
        )
    with pytest.raises(FinancialParseError, match="consolidated income statement"):
        parse_quarterly_report_pages(
            [SUMMARY_PAGE, "没有合并利润表"],
            document=DOCUMENT,
        )


def test_financial_fact_store_preserves_provenance_and_point_in_time(tmp_path):
    store = FinancialFactStore(tmp_path)
    facts = parsed_facts()
    saved = store.save(facts)
    assert saved["saved_count"] == len(facts)
    assert saved["storage_file"] == "evidence/financial_facts.json"

    as_of = datetime(2026, 11, 7, 12, 0, tzinfo=timezone.utc)
    items = store.list(symbol="601118", period_label="2026-Q3", as_of=as_of)
    assert len(items) == len(facts)
    assert all(item["visible_as_of"] is True for item in items)
    assert all(item["eligible_for_assessment"] is False for item in items)
    assert all(item["source_url"].startswith("https://") for item in items)

    before_publication = datetime(2026, 10, 1, 0, 0, tzinfo=timezone.utc)
    assert store.list(symbol="601118", as_of=before_publication) == []


def test_pdf_provider_rejects_non_cninfo_hosts_before_network():
    provider = CNInfoFinancialReportProvider(opener=object())
    with pytest.raises(FinancialParseError, match="official CNINFO"):
        provider.fetch_and_parse({**DOCUMENT, "source_url": "https://example.com/report.pdf"})


def test_pdf_provider_rejects_non_pdf_response():
    class Response:
        def __enter__(self):
            return self
        def __exit__(self, *args):
            return False
        def read(self, size):
            return b"not a pdf"

    class Opener:
        def open(self, request, timeout):
            return Response()

    provider = CNInfoFinancialReportProvider(opener=Opener())
    with pytest.raises(FinancialParseError, match="did not return a PDF"):
        provider.fetch_and_parse(DOCUMENT)


def test_pdf_provider_uses_text_pages_and_never_approves(monkeypatch):
    class Response:
        def __enter__(self):
            return self
        def __exit__(self, *args):
            return False
        def read(self, size):
            return b"%PDF-fake"

    class Opener:
        def open(self, request, timeout):
            return Response()

    class FakeReader:
        def __init__(self, stream, strict=False):
            assert stream.read(5) == b"%PDF-"
            self.pages = [
                SimpleNamespace(extract_text=lambda: SUMMARY_PAGE),
                SimpleNamespace(extract_text=lambda: STATEMENTS_PAGE),
            ]

    monkeypatch.setitem(__import__("sys").modules, "pypdf", SimpleNamespace(PdfReader=FakeReader))
    facts = CNInfoFinancialReportProvider(opener=Opener()).fetch_and_parse(DOCUMENT)
    assert len(facts) >= 10
    assert all(not item.reviewed_by_human and not item.unit_verified for item in facts)


def test_compact_preserves_numeric_column_boundaries():
    normalized = _compact("营业收入 23,762,244,302.46 30,574,470,082.36")
    assert NUMBER_RE.findall(normalized) == ["23,762,244,302.46", "30,574,470,082.36"]
