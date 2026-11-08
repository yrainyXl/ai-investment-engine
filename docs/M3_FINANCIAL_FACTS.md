# M3 Financial Facts v1：季度报告结构化提取

## Scope

First increment supports machine-readable Chinese quarterly reports indexed by the existing CNINFO disclosure provider. It does not use OCR, does not parse annual/half-year reports yet, and does not infer company segment economics.

The first validation target is `601118 / 2026-Q3`.

## Data path

1. `CNInfoDisclosureProvider` indexes report metadata and its official static PDF URL.
2. `CNInfoFinancialReportProvider` downloads only HTTPS URLs on `static.cninfo.com.cn`, checks PDF magic bytes, enforces a 30 MiB limit and a 100-page limit, and extracts text using pypdf.
3. The deterministic parser requires a quarter title, a main financial indicators section, consolidated income statement and consolidated cash flow statement. Each of those tables must explicitly declare `单位：元`.
4. Facts are stored in `/data/evidence/financial_facts.json`. The original PDF and extracted full text are not persisted.
5. `GET /v1/fundamentals/facts` is authenticated and read-only.

## Fields and accounting semantics

Each fact includes:

- symbol, issuer, canonical metric and exact decimal-string value;
- currency `CNY`, scale `yuan`, and the report's unit declaration;
- `period_type`: `quarter` or `year_to_date`;
- `period_start`, `period_end`, and `period_label` (for example `2026-Q3`);
- `statement_scope=consolidated` (parent-only statements are not mixed);
- disclosure ID/title, source URL, publication time, retrieval time;
- 1-based source page, source section and original row label;
- `audit_status`, parser version and extraction status.

Values are stored as decimal strings to avoid losing precision on large CNY figures.

## Initial metrics

Quarterly main-indicator table:
- revenue
- profit before tax
- net profit attributable to listed-company shareholders
- net profit attributable excluding non-recurring items

Year-to-date consolidated statements:
- revenue and cost of revenue
- operating profit, profit before tax and net profit
- net profit attributable to parent and minority shareholders
- operating cash flow
- year-to-date net profit attributable excluding non-recurring items from the summary table

The parser does not calculate gross margin or estimate price pass-through. Those belong to a separate calculation layer with explicit formulas and source-period alignment.

## Human review and safety

All parser output remains:
- `extraction_status=machine_extracted_pending_human_review`
- `reviewed_by_human=false`
- `unit_verified=false`
- `assessment=unassessed`
- `eligible_for_assessment=false`

A report title, page, row label, unit and statement scope must be manually checked before a fact can be used as thesis evidence. A machine-extracted number is not automatically a reviewed fact. The API exposes facts read-only; this increment adds no approval/write route.

## Commands

After `sync-disclosures` has populated the local document index:

```bash
python -m investment_engine.market_cli sync-financials --symbol 601118 --period 2026-Q3
```

The command requires exactly one matching disclosure. If there are duplicates, pass its exact `--document-id`; it does not silently select an ambiguous document.

Read extracted facts:

```bash
curl -H "Authorization: Bearer $ENGINE_API_TOKEN" \
  "http://127.0.0.1:8018/v1/fundamentals/facts?symbol=601118&period=2026-Q3"
```

## Known limitations

- Only quarterly report layout and consolidated scope are supported in this increment.
- Textless/scanned PDFs fail explicitly; OCR is not enabled.
- The parser is a narrow label-and-column parser, not a general-purpose table understanding system.
- No segment revenue, volume, realized selling price, raw-material procurement cost, self-produced/external-purchased ratio or rubber-specific gross margin is extracted yet.
- No analyst consensus/market expectation data is extracted.
- No automated human approval or thesis update occurs.
