"""Optional localhost-only preview API. Start with uvicorn --host 127.0.0.1.

No OTW writes. No arbitrary file paths from HTTP callers.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone
from hmac import compare_digest
from typing import Literal

from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel

from .analysis import parse_time
from .otw_client import OTWReadClient, OTWError
from .pipeline import run_pipeline

app = FastAPI(title="AI Investment Engine", version="0.1.1")


class PreviewRequest(BaseModel):
    source: Literal["fixture", "otw"] = "fixture"
    strategy_id: str | None = None
    as_of: str | None = None


def _authorize(authorization: str | None, *, otw_access: bool) -> None:
    configured_token = os.getenv("ENGINE_API_TOKEN", "")
    if otw_access and not configured_token:
        raise HTTPException(503, "OTW access disabled without ENGINE_API_TOKEN")
    if configured_token:
        expected_header = f"Bearer {configured_token}"
        if not authorization or not compare_digest(authorization, expected_header):
            raise HTTPException(401, "Authentication required")


def _otw_client() -> OTWReadClient:
    return OTWReadClient(os.getenv("OTW_BASE_URL", ""), os.getenv("OTW_API_TOKEN", ""))


@app.get("/healthz")
def healthz():
    return {"status": "ok", "mode": "read_only_preview", "orders_enabled": False}


@app.get("/v1/strategies")
def list_strategies(authorization: str | None = Header(default=None)):
    """Authenticated dynamic discovery. Does not activate or run a strategy."""
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
    as_of = parse_time(req.as_of) if req.as_of else datetime.now(timezone.utc)
    if as_of is None or as_of.tzinfo is None:
        raise HTTPException(422, "Invalid as_of time")
    try:
        client = _otw_client() if req.source == "otw" else None
        return run_pipeline(
            source=req.source, as_of=as_of,
            strategy_id=req.strategy_id or "", client=client,
            evidence_path=os.getenv("ENGINE_EVIDENCE_PATH") or None,
            bars_path=os.getenv("ENGINE_BARS_PATH") or None,
        )
    except OTWError as exc:
        if str(exc).startswith("Multiple active paper strategies"):
            raise HTTPException(409, "Multiple active strategies: choose strategy_id from GET /v1/strategies") from exc
        raise HTTPException(422, "OTW preview failed: OTWError") from exc
    except (ValueError, OSError) as exc:
        raise HTTPException(422, f"Preview failed: {type(exc).__name__}") from exc
