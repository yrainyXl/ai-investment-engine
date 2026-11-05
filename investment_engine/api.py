"""Authenticated localhost-only research API. No OTW write methods exist."""
from __future__ import annotations

import os
from datetime import datetime, timezone
from hmac import compare_digest
from typing import Any, Literal
from zoneinfo import ZoneInfo

from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel, Field

from .analysis import parse_time
from .backtest import BacktestError, run_backtest
from .market_data import MarketDataError, MarketDataService, MarketDataStore, completed_daily_bars
from .otw_client import OTWReadClient, OTWError
from .pipeline import run_pipeline
from .research import build_research_snapshot
from .report_store import ReportStore

app = FastAPI(title="AI Investment Engine", version="0.2.0")


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


def _as_of(value: str | None) -> datetime:
    parsed = parse_time(value) if value else datetime.now(ZoneInfo('Asia/Shanghai'))
    if parsed is None or parsed.tzinfo is None:
        raise HTTPException(422, "as_of must be an RFC3339 timestamp with timezone")
    if parsed > datetime.now(timezone.utc):
        raise HTTPException(422, "as_of cannot be in the future")
    return parsed


@app.get("/healthz")
def healthz():
    return {"status": "ok", "mode": "read_only_preview", "orders_enabled": False, "version": "0.2.0"}


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
        "bar_count": len(bars), "bars": [x.__dict__ for x in bars],
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
            account_summary=account_summary, as_of=as_of,
        )
        report["saved_report"] = _reports().save("research", strategy_id, report)
        return report
    except OTWError as exc:
        raise HTTPException(422, "OTW research context unavailable") from exc


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
