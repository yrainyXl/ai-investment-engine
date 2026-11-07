"""Conservative extraction of quarterly financial facts from CNINFO PDFs.

The parser is intentionally narrow: Chinese A-share quarterly reports with
machine-readable text, explicit report period and explicit CNY/yuan units.
Every extracted fact remains pending human review.
"""
from __future__ import annotations

import hashlib
import json
from decimal import Decimal, InvalidOperation
import re
from calendar import monthrange
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
from urllib.request import HTTPCookieProcessor, Request, build_opener
from http.cookiejar import CookieJar

from .evidence import EvidenceError, _parse_aware

PARSER_VERSION = "cninfo-quarterly-v1"
ALLOWED_PDF_HOSTS = {"static.cninfo.com.cn"}
NUMBER_RE = re.compile(r"不适用|[-−]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?")
QUARTERS = {"一": 1, "二": 2, "三": 3, "四": 4, "1": 1, "2": 2, "3": 3, "4": 4}


class FinancialParseError(RuntimeError):
    pass


def _compact(value: str) -> str:
    return re.sub(r"\s+", "", str(value or "")).replace("−", "-")


def _parse_number(token: str) -> str | None:
    if token == "不适用":
        return None
    try:
        value = Decimal(token.replace(",", "").replace("−", "-"))
    except (TypeError, InvalidOperation) as exc:
        raise FinancialParseError("invalid numeric token in report") from exc
    if not value.is_finite():
        raise FinancialParseError("non-finite numeric token in report")
    return format(value, "f")


def _period_from_title(title: str) -> tuple[int, int]:
    match = re.search(r"(20\d{2})年(?:第)?([一二三四1234])季度报告", title)
    if not match:
        raise FinancialParseError("only titled Chinese quarterly reports are supported")
    return int(match.group(1)), QUARTERS[match.group(2)]


def _page_for_offset(offset: int, ranges: list[tuple[int, int, int]]) -> int | None:
    for start, end, page in ranges:
        if start <= offset < end:
            return page
    return None


def _values_after(text: str, label: str, *, limit: int = 4) -> tuple[int, list[str | None]]:
    position = text.find(label)
    if position < 0:
        raise FinancialParseError(f"required report row not found: {label}")
    tail = text[position + len(label):]
    tokens = NUMBER_RE.findall(tail)
    if len(tokens) < limit:
        raise FinancialParseError(f"not enough numeric columns after report row: {label}")
    return position, [_parse_number(token) for token in tokens[:limit]]


@dataclass(frozen=True)
class FinancialFact:
    fact_id: str
    symbol: str
    issuer_name: str
    metric: str
    value: str
    unit: str
    value_scale: str
    unit_basis: str
    currency: str
    period_type: str
    period_label: str
    period_start: str
    period_end: str
    statement_scope: str
    source_document_id: str
    source_title: str
    source_name: str
    source_url: str
    published_at: str
    retrieved_at: str
    source_page: int
    source_section: str
    source_label: str
    source_excerpt: str
    audit_status: str
    extraction_status: str = "machine_extracted_pending_human_review"
    reviewed_by_human: bool = False
    unit_verified: bool = False
    assessment: str = "unassessed"
    evidence_type: str = "confirmed_fact"
    parser_version: str = PARSER_VERSION

    def validate(self) -> None:
        required = (
            self.fact_id, self.symbol, self.metric, self.unit, self.value_scale, self.unit_basis, self.currency,
            self.period_label, self.period_start, self.period_end,
            self.source_document_id, self.source_title, self.source_name,
            self.source_url, self.source_section, self.source_label,
        )
        if any(not value for value in required):
            raise EvidenceError("financial fact is missing required identity or provenance")
        if self.period_type not in {"quarter", "year_to_date"}:
            raise EvidenceError("unsupported financial fact period_type")
        if self.statement_scope not in {"consolidated"}:
            raise EvidenceError("unsupported financial statement scope")
        if self.evidence_type != "confirmed_fact":
            raise EvidenceError("parsed financial statements must be confirmed_fact records")
        try:
            numeric_value = Decimal(self.value)
        except (InvalidOperation, TypeError) as exc:
            raise EvidenceError("financial fact value must be a decimal string") from exc
        if not numeric_value.is_finite():
            raise EvidenceError("financial fact value must be finite")
        if not self.source_url.startswith("https://"):
            raise EvidenceError("financial fact source_url must use HTTPS")
        if _parse_aware(self.published_at) is None or _parse_aware(self.retrieved_at) is None:
            raise EvidenceError("financial fact timestamps must include timezone")
        if self.source_page < 1:
            raise EvidenceError("source_page must be a positive 1-based page number")
        if self.assessment not in {"supporting", "contradicting", "unassessed"}:
            raise EvidenceError("unsupported financial fact assessment")


def parse_quarterly_report_pages(
    pages: list[str],
    *,
    document: dict[str, Any],
    retrieved_at: datetime | None = None,
) -> list[FinancialFact]:
    """Parse a limited set of consolidated quarterly financial statement rows."""
    if not pages or not any(len(_compact(page)) > 100 for page in pages):
        raise FinancialParseError("PDF contains no extractable text; OCR is not enabled")
    title = str(document.get("title") or "")
    year, quarter = _period_from_title(title)
    symbol = str(document.get("symbol") or "")
    document_id = str(document.get("document_id") or "")
    published_at = str(document.get("published_at") or "")
    source_url = str(document.get("source_url") or "")
    source_name = "cninfo.official_disclosure_pdf"
    issuer = str(document.get("issuer_name") or "海南天然橡胶产业集团股份有限公司")
    if not re.fullmatch(r"\d{6}", symbol) or not document_id:
        raise FinancialParseError("source document symbol or document_id is missing")
    if _parse_aware(published_at) is None:
        raise FinancialParseError("source document publication timestamp is invalid")
    if not source_url.startswith("https://"):
        raise FinancialParseError("source document URL must use HTTPS")

    combined = ""
    page_ranges: list[tuple[int, int, int]] = []
    for page_number, page in enumerate(pages, start=1):
        normalized = _compact(page)
        start = len(combined)
        combined += normalized
        page_ranges.append((start, len(combined), page_number))

    quarter_start_month = (quarter - 1) * 3 + 1
    quarter_end_month = quarter * 3
    quarter_start = f"{year}-{quarter_start_month:02d}-01"
    quarter_end = f"{year}-{quarter_end_month:02d}-{monthrange(year, quarter_end_month)[1]:02d}"
    ytd_start = f"{year}-01-01"
    period_label = f"{year}-Q{quarter}"
    retrieved = (retrieved_at or datetime.now(timezone.utc)).isoformat()
    if _parse_aware(retrieved) is None:
        raise FinancialParseError("retrieved_at must include timezone")
    audit_status = "unaudited" if "未经审计" in combined else "unknown"

    summary_start = combined.find("一、主要财务数据")
    summary_end = combined.find("二、股东信息", summary_start + 1)
    if summary_start < 0 or summary_end < 0:
        raise FinancialParseError("main financial data summary section not found")
    summary = combined[summary_start:summary_end]
    if not re.search(r"单位[:：]元", summary):
        raise FinancialParseError("summary table does not explicitly declare yuan units")

    facts: list[FinancialFact] = []

    def add(metric: str, value: str | None, *, period_type: str, section: str,
            label: str, offset: int, page_text: str, scope: str = "consolidated") -> None:
        if value is None:
            return
        start_date, end_date = (
            (quarter_start, quarter_end) if period_type == "quarter"
            else (ytd_start, quarter_end)
        )
        key = "|".join((document_id, metric, period_type, scope, str(value)))
        fact_id = hashlib.sha256(key.encode("utf-8")).hexdigest()[:32]
        fact = FinancialFact(
            fact_id=fact_id, symbol=symbol, issuer_name=issuer, metric=metric,
            value=value, unit="CNY", value_scale="yuan", unit_basis="report table states 元 / 人民币", currency="CNY", period_type=period_type,
            period_label=period_label, period_start=start_date, period_end=end_date,
            statement_scope=scope, source_document_id=document_id,
            source_title=title, source_name=source_name, source_url=source_url,
            published_at=published_at, retrieved_at=retrieved,
            source_page=_page_for_offset(offset, page_ranges) or 1,
            source_section=section, source_label=label,
            source_excerpt=page_text[:240], audit_status=audit_status,
        )
        fact.validate()
        facts.append(fact)

    # Main financial indicators: quarterly values are first column; YTD values third.
    summary_rows = [
        ("revenue", "营业收入"),
        ("profit_before_tax", "利润总额"),
        ("net_profit_attributable", "归属于上市公司股东的净利润"),
        ("net_profit_attributable_ex_nonrecurring", "归属于上市公司股东的扣除非经常性损益的净利润"),
    ]
    for metric, label in summary_rows:
        offset, values = _values_after(summary, label, limit=4)
        add(metric, values[0], period_type="quarter", section="main_financial_indicators",
            label=label, offset=summary_start + offset, page_text=summary[offset:offset + 240])
        if metric == "net_profit_attributable_ex_nonrecurring":
            add(metric, values[2], period_type="year_to_date", section="main_financial_indicators",
                label=label, offset=summary_start + offset, page_text=summary[offset:offset + 240])

    # Detailed consolidated income statement. Do not mix it with the parent-only statement.
    income_start = combined.find("合并利润表")
    income_end = combined.find("合并现金流量表", income_start + 1)
    if income_start < 0 or income_end < 0:
        raise FinancialParseError("consolidated income statement section not found")
    income = combined[income_start:income_end]
    if not re.search(r"单位[:：]元", income):
        raise FinancialParseError("consolidated income statement does not explicitly declare yuan units")
    income_rows = [
        ("revenue", "营业收入"),
        ("cost_of_revenue", "营业成本"),
        ("operating_profit", "营业利润"),
        ("profit_before_tax", "利润总额"),
        ("net_profit", "五、净利润"),
        ("net_profit_attributable", "归属于母公司股东的净利润"),
        ("minority_net_profit", "少数股东损益"),
    ]
    for metric, label in income_rows:
        offset, values = _values_after(income, label, limit=2)
        add(metric, values[0], period_type="year_to_date", section="consolidated_income_statement",
            label=label, offset=income_start + offset, page_text=income[offset:offset + 240])

    # Cash flow statement has the same two-column YTD comparative layout.
    cash_start = combined.find("合并现金流量表", income_end)
    cash_end = combined.find("母公司资产负债表", cash_start + 1)
    if cash_start < 0 or cash_end < 0:
        raise FinancialParseError("consolidated cash flow statement section not found")
    cash = combined[cash_start:cash_end]
    if not re.search(r"单位[:：]元", cash):
        raise FinancialParseError("consolidated cash flow statement does not explicitly declare yuan units")
    offset, values = _values_after(cash, "经营活动产生的现金流量净额", limit=2)
    add("operating_cash_flow", values[0], period_type="year_to_date",
        section="consolidated_cash_flow_statement",
        label="经营活动产生的现金流量净额", offset=cash_start + offset,
        page_text=cash[offset:offset + 240])

    # Prevent duplicate metric/scope/period facts when a report repeats a summary row.
    unique: dict[tuple[str, str, str, str], FinancialFact] = {}
    for fact in facts:
        key = (fact.metric, fact.period_type, fact.statement_scope, fact.source_section)
        unique[key] = fact
    return list(unique.values())


class CNInfoFinancialReportProvider:
    """Download and parse one CNINFO-hosted quarterly report PDF."""

    def __init__(self, *, timeout: float = 25.0, max_bytes: int = 30 * 1024 * 1024,
                 opener: Any | None = None):
        self.timeout = timeout
        self.max_bytes = max_bytes
        self.opener = opener or build_opener(HTTPCookieProcessor(CookieJar()))

    def fetch_and_parse(self, document: dict[str, Any]) -> list[FinancialFact]:
        source_url = str(document.get("source_url") or "")
        parsed = urlparse(source_url)
        if parsed.scheme != "https" or parsed.hostname not in ALLOWED_PDF_HOSTS:
            raise FinancialParseError("PDF source host is not an allowed official CNINFO static host")
        if not str(document.get("title") or "").endswith("报告") or "季度报告" not in str(document.get("title") or ""):
            raise FinancialParseError("selected document is not a quarterly report")
        request = Request(source_url, headers={
            "User-Agent": "Mozilla/5.0 (compatible; AIInvestmentEngine/0.3)",
            "Accept": "application/pdf,*/*;q=0.8",
            "Referer": "https://www.cninfo.com.cn/",
        })
        try:
            with self.opener.open(request, timeout=self.timeout) as response:
                final_url = response.geturl() if hasattr(response, "geturl") else source_url
                final_host = urlparse(final_url).hostname
                if final_host not in ALLOWED_PDF_HOSTS:
                    raise FinancialParseError("CNINFO PDF redirected outside the official static host")
                pdf_bytes = response.read(self.max_bytes + 1)
        except Exception as exc:
            raise FinancialParseError(f"CNINFO PDF download failed: {type(exc).__name__}") from exc
        if len(pdf_bytes) > self.max_bytes:
            raise FinancialParseError("quarterly report PDF exceeds configured size limit")
        if not pdf_bytes.startswith(b"%PDF-"):
            raise FinancialParseError("CNINFO source did not return a PDF")
        try:
            from pypdf import PdfReader
        except ImportError as exc:
            raise FinancialParseError("pypdf is not installed; install the fundamentals extra") from exc
        try:
            reader = PdfReader(BytesIO(pdf_bytes), strict=False)
            if len(reader.pages) > 100:
                raise FinancialParseError("PDF page count exceeds configured limit")
            pages = [page.extract_text() or "" for page in reader.pages]
        except FinancialParseError:
            raise
        except Exception as exc:
            raise FinancialParseError(f"PDF text extraction failed: {type(exc).__name__}") from exc
        return parse_quarterly_report_pages(pages, document=document)


class FinancialFactStore:
    def __init__(self, root: str | Path | None = None):
        self.root = Path(root or "data").expanduser()
        self.directory = self.root / "evidence"
        self.directory.mkdir(parents=True, exist_ok=True)
        self.path = self.directory / "financial_facts.json"

    def _read(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        try:
            items = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise EvidenceError("Could not read financial_facts.json") from exc
        if not isinstance(items, list) or not all(isinstance(item, dict) for item in items):
            raise EvidenceError("Invalid financial_facts.json")
        return items

    def save(self, facts: list[FinancialFact]) -> dict[str, Any]:
        existing = self._read()
        merged = {str(item.get("fact_id")): item for item in existing}
        for fact in facts:
            fact.validate()
            merged[fact.fact_id] = asdict(fact)
        ordered = sorted(merged.values(), key=lambda x: (
            x.get("period_end", ""), x.get("published_at", ""), x.get("metric", ""), x.get("fact_id", "")
        ))
        temporary = self.path.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(ordered, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        temporary.replace(self.path)
        return {"saved_count": len(facts), "total_count": len(ordered),
                "storage_file": str(self.path.relative_to(self.root))}

    def list(self, *, symbol: str | None = None, period_label: str | None = None,
             as_of: datetime | None = None, limit: int = 500) -> list[dict[str, Any]]:
        if limit < 1 or limit > 5000:
            raise EvidenceError("limit must be between 1 and 5000")
        output = []
        for item in self._read():
            if symbol and item.get("symbol") != symbol:
                continue
            if period_label and item.get("period_label") != period_label:
                continue
            published = _parse_aware(item.get("published_at"))
            retrieved = _parse_aware(item.get("retrieved_at"))
            visible = bool(published and retrieved and as_of and published <= as_of and retrieved <= as_of)
            if as_of is not None and not visible:
                continue
            value = dict(item)
            value["visible_as_of"] = visible if as_of is not None else None
            eligible = bool(
                value.get("reviewed_by_human") is True
                and value.get("unit_verified") is True
                and value.get("assessment") in {"supporting", "contradicting"}
                and visible
            )
            value["eligible_for_assessment"] = eligible
            value["eligibility_reason"] = (
                "eligible after human review and point-in-time checks" if eligible else
                "machine extraction requires human verification of statement scope, row mapping, unit and period"
                if not item.get("reviewed_by_human") or not item.get("unit_verified") else
                "financial fact needs an explicit supporting/contradicting thesis assessment"
            )
            output.append(value)
        return output[-limit:]

    def summary(self, *, as_of: datetime) -> dict[str, Any]:
        items = self.list(as_of=as_of, limit=5000)
        return {
            "financial_fact_count": len(items),
            "reviewed_financial_fact_count": sum(bool(x.get("reviewed_by_human")) for x in items),
            "financial_fact_metrics": sorted({str(x.get("metric")) for x in items}),
        }
