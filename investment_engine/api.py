"""Authenticated localhost-only research API. No OTW write methods exist."""
from __future__ import annotations

import os
from datetime import datetime, timezone
from hmac import compare_digest
from typing import Any, Literal
from zoneinfo import ZoneInfo

from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from .analysis import parse_time
from .backtest import BacktestError, run_backtest
from .evidence import EvidenceError, EvidenceStore
from .financials import FinancialFactStore
from .market_data import MarketDataError, MarketDataService, MarketDataStore, completed_daily_bars
from .otw_client import OTWReadClient, OTWError
from .pipeline import run_pipeline
from .research import build_research_snapshot
from .report_store import ReportStore
from .hypotheses import HypothesisDefinition, HypothesisError, HypothesisVariable, evaluate_hypothesis
from .scenarios import ScenarioDefinition, evaluate_scenarios

app = FastAPI(title="AI Investment Engine", version="0.4.0")


class PreviewRequest(BaseModel):
    source: Literal["fixture", "otw"] = "fixture"
    strategy_id: str | None = None
    as_of: str | None = None


class MarketSyncRequest(BaseModel):
    symbol: str = Field(min_length=1, max_length=24)
    asset_type: Literal["stock", "etf", "future"] = "stock"
    start_date: str = "20180101"
    end_date: str | None = None
    adjustment: Literal["none", "qfq", "hfq", "continuous"] = "qfq"


class BacktestRequest(BaseModel):
    symbol: str = Field(min_length=1, max_length=24)
    asset_type: Literal["stock", "etf", "future"] = "stock"
    adjustment: Literal["none", "qfq", "hfq", "continuous"] | None = None
    strategy: Literal["sma_trend", "buy_and_hold"] = "sma_trend"
    fast_period: int = Field(default=20, ge=2, le=200)
    slow_period: int = Field(default=60, ge=3, le=500)
    fee_bps: float = Field(default=10.0, ge=0, le=500)
    slippage_bps: float = Field(default=5.0, ge=0, le=500)
    as_of: str | None = None


class ResearchRequest(BaseModel):
    strategy_id: str | None = None
    symbol: str | None = None
    futures_symbol: str | None = None
    as_of: str | None = None
    evidence: dict[str, Any] | None = None


class HypothesisDefinitionPayload(BaseModel):
    hypothesis_id: str = Field(min_length=1, max_length=128)
    entity_id: str = Field(min_length=1, max_length=128)
    strategy_id: str = Field(min_length=1, max_length=128)
    claim: str = Field(min_length=1, max_length=2000)
    operation: Literal["sum", "difference", "product", "ratio", "growth", "margin", "weighted_sum"]
    variables: list[str] = Field(min_length=1, max_length=32)
    target: str
    target_unit: str = Field(min_length=1, max_length=64)
    direction: Literal["above", "below", "equal"]
    tolerance: str = "0"
    weights: list[str] = Field(default_factory=list)
    require_human_review: bool = True
    max_age_days: int | None = Field(default=None, ge=0, le=36500)


class HypothesisFactReferencePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    variable_id: str = Field(min_length=1, max_length=128)
    fact_id: str = Field(min_length=1, max_length=128)


class FinancialFactReviewPayload(BaseModel):
    reviewer_id: str = Field(min_length=1, max_length=128)
    review_note: str = Field(min_length=1, max_length=2000)
    unit_verified: bool
    scope_verified: bool
    confirm_source_checked: bool = False


class ScenarioDefinitionPayload(BaseModel):
    scenario_id: str = Field(min_length=1, max_length=128)
    name: str = Field(min_length=1, max_length=128)
    description: str = Field(min_length=1, max_length=2000)
    overrides: dict[str, str]


class HypothesisEvaluationRequest(BaseModel):
    hypothesis: HypothesisDefinitionPayload
    fact_refs: list[HypothesisFactReferencePayload] = Field(min_length=1, max_length=32)
    as_of: str | None = None


class HypothesisScenariosRequest(HypothesisEvaluationRequest):
    scenarios: list[ScenarioDefinitionPayload] = Field(min_length=1, max_length=20)


def _authorize(authorization: str | None, *, otw_access: bool = False) -> None:
    configured_token = os.getenv("ENGINE_API_TOKEN", "")
    if not configured_token:
        raise HTTPException(503, "API disabled without ENGINE_API_TOKEN")
    expected_header = f"Bearer {configured_token}"
    if not authorization or not compare_digest(authorization, expected_header):
        raise HTTPException(401, "Authentication required")


def _otw_client() -> OTWReadClient:
    return OTWReadClient(
        os.getenv("OTW_BASE_URL", ""), os.getenv("OTW_API_TOKEN", ""),
        transport=os.getenv("OTW_TRANSPORT", "rest"),
    )


def _store() -> MarketDataStore:
    return MarketDataStore(os.getenv("ENGINE_DATA_DIR", "data"))


def _reports() -> ReportStore:
    return ReportStore(os.getenv("ENGINE_DATA_DIR", "data"))


def _resolve_financial_fact_refs(fact_refs, *, entity_id: str, as_of: datetime):
    store = FinancialFactStore(os.getenv("ENGINE_DATA_DIR", "data"))
    resolved = {}
    for ref in fact_refs:
        if ref.variable_id in resolved:
            raise HypothesisError(f"duplicate variable_id: {ref.variable_id}")
        fact = store.get_by_id(ref.fact_id, as_of=as_of)
        if fact is None:
            raise HypothesisError(f"financial fact not found or not visible as of: {ref.fact_id}")
        if str(fact.get("symbol")) != entity_id:
            raise HypothesisError(f"fact {ref.fact_id} belongs to a different entity")
        period_end = str(fact["period_end"])
        resolved[ref.variable_id] = HypothesisVariable(
            variable_id=ref.variable_id,
            metric_id=str(fact["metric"]),
            value=str(fact["value"]),
            unit=str(fact["currency"]),
            period_type=str(fact["period_type"]),
            period_start=str(fact["period_start"]),
            period_end=period_end,
            source_id=str(fact["fact_id"]),
            published_at=str(fact["published_at"]),
            effective_at=f"{period_end}T23:59:59+08:00",
            retrieved_at=str(fact["retrieved_at"]),
            reviewed_by_human=fact.get("reviewed_by_human") is True,
            unit_verified=fact.get("unit_verified") is True,
            scope_verified=fact.get("scope_verified") is True,
        )
    return resolved


def _as_of(value: str | None) -> datetime:
    parsed = parse_time(value) if value else datetime.now(ZoneInfo('Asia/Shanghai'))
    if parsed is None or parsed.tzinfo is None:
        raise HTTPException(422, "as_of must be an RFC3339 timestamp with timezone")
    if parsed > datetime.now(timezone.utc):
        raise HTTPException(422, "as_of cannot be in the future")
    return parsed


@app.get("/healthz")
def healthz():
    return {"status": "ok", "mode": "read_only_preview", "orders_enabled": False, "version": "0.4.0"}


@app.get("/v1/strategies")
def list_strategies(authorization: str | None = Header(default=None)):
    _authorize(authorization, otw_access=True)
    try:
        strategies = _otw_client().list_strategies()
        return {
            "strategies": [
                {key: s.get(key) for key in ("id", "name", "status", "portfolio_id", "ai_provider", "currency")}
                for s in strategies
            ],
            "count": len(strategies),
        }
    except (OTWError, ValueError, OSError) as exc:
        raise HTTPException(502, "Could not list OTW strategies") from exc


@app.post("/v1/analysis/preview")
def preview(req: PreviewRequest, authorization: str | None = Header(default=None)):
    _authorize(authorization, otw_access=req.source == "otw")
    as_of = _as_of(req.as_of)
    try:
        client = _otw_client() if req.source == "otw" else None
        return run_pipeline(
            source=req.source, as_of=as_of, strategy_id=req.strategy_id or "", client=client,
            evidence_path=os.getenv("ENGINE_EVIDENCE_PATH") or None,
            bars_path=os.getenv("ENGINE_BARS_PATH") or None,
        )
    except OTWError as exc:
        if str(exc).startswith("Multiple active paper strategies"):
            raise HTTPException(409, "Multiple active strategies: choose strategy_id from GET /v1/strategies") from exc
        raise HTTPException(422, "OTW preview failed: OTWError") from exc
    except (ValueError, OSError) as exc:
        raise HTTPException(422, f"Preview failed: {type(exc).__name__}") from exc


@app.get("/v1/industry/observations")
def list_industry_observations(series_id: str | None = None, as_of: str | None = None,
                               limit: int = 500,
                               authorization: str | None = Header(default=None)):
    """Read locally synced industry observations with point-in-time eligibility metadata."""
    _authorize(authorization)
    try:
        cutoff = _as_of(as_of) if as_of else None
        items = EvidenceStore(os.getenv("ENGINE_DATA_DIR", "data")).list_observations(
            as_of=cutoff, series_id=series_id, limit=limit,
        )
        return {"observations": items, "count": len(items)}
    except EvidenceError as exc:
        raise HTTPException(422, str(exc)) from exc


@app.get("/v1/fundamentals/disclosures")
def list_fundamental_disclosures(symbol: str | None = None, as_of: str | None = None,
                                 limit: int = 200,
                                 authorization: str | None = Header(default=None)):
    """Read CNINFO disclosure index metadata; this endpoint does not parse PDF facts."""
    _authorize(authorization)
    if not symbol:
        raise HTTPException(422, "symbol is required")
    try:
        cutoff = _as_of(as_of) if as_of else None
        items = EvidenceStore(os.getenv("ENGINE_DATA_DIR", "data")).list_documents(
            symbol=symbol, as_of=cutoff, limit=limit,
        )
        return {"documents": items, "count": len(items),
                "content_status": "metadata_only_pdf_not_parsed"}
    except EvidenceError as exc:
        raise HTTPException(422, str(exc)) from exc


@app.get("/v1/fundamentals/facts")
def list_financial_facts(symbol: str | None = None, period: str | None = None,
                         as_of: str | None = None, limit: int = 500,
                         authorization: str | None = Header(default=None)):
    """Read machine-extracted financial facts; all remain pending human review."""
    _authorize(authorization)
    if not symbol:
        raise HTTPException(422, "symbol is required")
    try:
        cutoff = _as_of(as_of) if as_of else None
        items = FinancialFactStore(os.getenv("ENGINE_DATA_DIR", "data")).list(
            symbol=symbol, period_label=period, as_of=cutoff, limit=limit,
        )
        return {"facts": items, "count": len(items),
                "eligibility_note": "Machine-extracted values are not thesis evidence until a human verifies the statement scope, period, row mapping and units."}
    except EvidenceError as exc:
        raise HTTPException(422, str(exc)) from exc


@app.get("/v1/market-data")
def list_market_data(authorization: str | None = Header(default=None)):
    _authorize(authorization)
    return {"series": _store().list_series()}


@app.post("/v1/market-data/sync")
def sync_market_data(req: MarketSyncRequest, authorization: str | None = Header(default=None)):
    _authorize(authorization)
    end_date = req.end_date or datetime.now(ZoneInfo('Asia/Shanghai')).date().isoformat()
    try:
        return MarketDataService(store=_store()).sync(
            symbol=req.symbol, asset_type=req.asset_type, start_date=req.start_date,
            end_date=end_date, adjustment=req.adjustment,
        )
    except MarketDataError as exc:
        raise HTTPException(422, str(exc)) from exc
    except Exception as exc:
        raise HTTPException(502, f"Market data provider failed: {type(exc).__name__}") from exc


@app.get("/v1/market-data/{symbol}")
def get_market_data(symbol: str, asset_type: Literal["stock", "etf", "future"] = "stock",
                    adjustment: Literal["none", "qfq", "hfq", "continuous"] | None = None,
                    start_date: str | None = None, end_date: str | None = None,
                    authorization: str | None = Header(default=None)):
    _authorize(authorization)
    adjustment = adjustment or ("continuous" if asset_type == "future" else "qfq")
    try:
        bars = _store().load(symbol, asset_type, adjustment)
    except MarketDataError as exc:
        raise HTTPException(422, str(exc)) from exc
    if start_date:
        bars = [x for x in bars if x.timestamp >= start_date]
    if end_date:
        bars = [x for x in bars if x.timestamp <= end_date]
    return {
        "symbol": symbol, "asset_type": asset_type, "adjustment": adjustment,
        "bar_count": len(bars),
        "market_data_quality": _store().read_quarantine(symbol, asset_type, adjustment),
        "bars": [x.__dict__ for x in bars],
    }


@app.post("/v1/backtests/run")
def backtest(req: BacktestRequest, authorization: str | None = Header(default=None)):
    _authorize(authorization)
    as_of = _as_of(req.as_of)
    try:
        adjustment = req.adjustment or ("continuous" if req.asset_type == "future" else "qfq")
        bars = _store().load(req.symbol, req.asset_type, adjustment)
        bars = completed_daily_bars(bars, as_of=as_of)
        report = run_backtest(
            bars, strategy=req.strategy, fast_period=req.fast_period, slow_period=req.slow_period,
            fee_bps=req.fee_bps, slippage_bps=req.slippage_bps, as_of=as_of.date(),
        )
        report["data"]["adjustment"] = adjustment
        report["market_data_quality"] = _store().read_quarantine(req.symbol, req.asset_type, adjustment)
        report["as_of"] = as_of.isoformat()
        report["saved_report"] = _reports().save("backtests", req.symbol, report)
        return report
    except (MarketDataError, BacktestError) as exc:
        raise HTTPException(422, str(exc)) from exc


@app.post("/v1/research/preview")
def research_preview(req: ResearchRequest, authorization: str | None = Header(default=None)):
    _authorize(authorization, otw_access=True)
    as_of = _as_of(req.as_of)
    try:
        client = _otw_client()
        strategy_id = client.resolve_strategy_id(req.strategy_id)
        context = client.strategy_context(strategy_id)
        holdings = context.get("account", {}).get("holdings", [])
        symbol = req.symbol
        if not symbol:
            symbols = sorted({str(x.get("symbol")) for x in holdings if x.get("symbol")})
            if len(symbols) != 1:
                raise HTTPException(409, "Specify symbol when the OTW strategy has zero or multiple holdings")
            symbol = symbols[0]
        matched = next((x for x in holdings if str(x.get("symbol")) == symbol), None)
        asset_id = matched.get("id") if matched else None
        thesis = client.asset_thesis(strategy_id, asset_id) if asset_id else None
        thesis_document = thesis.get("document", {}) if thesis else {}
        thesis_content = thesis_document.get("content", {})
        strategy = context.get("strategy", {})
        account = context.get("account", {})
        account_summary = {
            "currency": account.get("currency"), "cash": account.get("cash"), "equity": account.get("equity"),
            "holdings": [{"symbol": item.get("symbol"), "quantity": item.get("quantity")} for item in holdings],
        }
        stock_bars = _store().load(symbol, "stock", "qfq")
        stock_bars = completed_daily_bars(stock_bars, as_of=as_of)
        futures_bars = _store().load(req.futures_symbol, "future", "continuous") if req.futures_symbol else []
        futures_bars = completed_daily_bars(futures_bars, as_of=as_of)
        report = build_research_snapshot(
            strategy_id=strategy_id, symbol=symbol, thesis_content=thesis_content,
            stock_bars=stock_bars, futures_symbol=req.futures_symbol,
            futures_bars=futures_bars, evidence=req.evidence,
            strategy_name=strategy.get("name"), thesis_updated_at=thesis_document.get("updated_at"),
            account_summary=account_summary, provider_data_summary=EvidenceStore(os.getenv("ENGINE_DATA_DIR", "data")).summary(as_of=as_of), as_of=as_of,
            market_quality={
                "equity": _store().read_quarantine(symbol, "stock", "qfq"),
                "commodity_reference": _store().read_quarantine(req.futures_symbol, "future", "continuous")
                    if req.futures_symbol else {"excluded_count": 0},
            },
        )
        report["saved_report"] = _reports().save("research", strategy_id, report)
        return report
    except OTWError as exc:
        raise HTTPException(422, "OTW research context unavailable") from exc


@app.post("/v1/fundamentals/facts/{fact_id}/review")
def review_financial_fact_endpoint(fact_id: str, req: FinancialFactReviewPayload,
                                   authorization: str | None = Header(default=None)):
    """Record an explicit human review; never called by scheduled ingestion or analysis."""
    _authorize(authorization)
    if not req.confirm_source_checked:
        raise HTTPException(400, "Explicit source-review confirmation is required")
    try:
        reviewed = FinancialFactStore(os.getenv("ENGINE_DATA_DIR", "data")).review(
            fact_id, reviewer_id=req.reviewer_id, review_note=req.review_note,
            unit_verified=req.unit_verified, scope_verified=req.scope_verified,
        )
        saved = _reports().save("fact_review", "manual_review", {
            "fact_id": fact_id, "reviewer_id": req.reviewer_id,
            "review_note": req.review_note, "unit_verified": req.unit_verified,
            "scope_verified": req.scope_verified, "assessment": "unassessed",
        })
        return {
            "fact": {key: reviewed.get(key) for key in (
                "fact_id", "symbol", "metric", "value", "unit", "period_label",
                "statement_scope", "reviewed_by_human", "unit_verified",
                "scope_verified", "reviewed_at", "reviewer_id", "assessment",
            )},
            "report": saved,
            "note": "Review does not automatically mark the fact as supporting or contradicting any thesis.",
        }
    except EvidenceError as exc:
        raise HTTPException(404 if "not found" in str(exc) else 422, str(exc)) from exc


@app.post("/v1/hypotheses/evaluate")
def evaluate_hypothesis_endpoint(req: HypothesisEvaluationRequest,
                                 authorization: str | None = Header(default=None)):
    """Evaluate only facts resolved from the local evidence store; persist a local audit report."""
    _authorize(authorization)
    as_of = _as_of(req.as_of)
    try:
        definition_data = req.hypothesis.model_dump()
        definition_data["variables"] = tuple(definition_data["variables"])
        definition_data["weights"] = tuple(definition_data["weights"])
        definition = HypothesisDefinition(**definition_data)
        inputs = _resolve_financial_fact_refs(
            req.fact_refs, entity_id=definition.entity_id, as_of=as_of,
        )
        result = evaluate_hypothesis(definition, inputs, as_of=as_of.isoformat())
        saved = _reports().save("hypothesis", "evaluation", result.to_dict())
        return {"evaluation": result.to_dict(), "report": saved,
                "side_effects": "local_audit_report_only", "orders_enabled": False}
    except HypothesisError as exc:
        raise HTTPException(422, str(exc)) from exc


@app.post("/v1/hypotheses/scenarios")
def evaluate_hypothesis_scenarios_endpoint(req: HypothesisScenariosRequest,
                                           authorization: str | None = Header(default=None)):
    """Compare local reviewed facts with explicit what-if assumptions; facts are never overwritten."""
    _authorize(authorization)
    as_of = _as_of(req.as_of)
    try:
        definition_data = req.hypothesis.model_dump()
        definition_data["variables"] = tuple(definition_data["variables"])
        definition_data["weights"] = tuple(definition_data["weights"])
        definition = HypothesisDefinition(**definition_data)
        inputs = _resolve_financial_fact_refs(
            req.fact_refs, entity_id=definition.entity_id, as_of=as_of,
        )
        scenarios = tuple(ScenarioDefinition(**item.model_dump()) for item in req.scenarios)
        result = evaluate_scenarios(definition, inputs, scenarios, as_of=as_of.isoformat())
        saved = _reports().save("scenario", "evaluation", result.to_dict())
        return {"scenarios": result.to_dict(), "report": saved,
                "side_effects": "local_audit_report_only", "orders_enabled": False}
    except HypothesisError as exc:
        raise HTTPException(422, str(exc)) from exc


@app.get("/v1/reports")
def list_reports(kind: str | None = None, limit: int = 50,
                 authorization: str | None = Header(default=None)):
    _authorize(authorization)
    try:
        return {"reports": _reports().list(kind=kind, limit=limit)}
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@app.get("/v1/reports/{report_id}")
def get_report(report_id: str, authorization: str | None = Header(default=None)):
    _authorize(authorization)
    try:
        report = _reports().get(report_id)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    if report is None:
        raise HTTPException(404, "Report not found")
    return report
