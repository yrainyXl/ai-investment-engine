"""Optional localhost-only preview API. Start with uvicorn --host 127.0.0.1.

Does not accept arbitrary local file paths from HTTP callers. No OTW writes.
"""
from __future__ import annotations
import os
from hmac import compare_digest
from datetime import datetime, timezone
from typing import Literal
from fastapi import FastAPI, HTTPException, Header
from pydantic import BaseModel
from .analysis import parse_time
from .otw_client import OTWReadClient, OTWError
from .pipeline import run_pipeline

app = FastAPI(title="AI Investment Engine", version="0.1.0")


class PreviewRequest(BaseModel):
    source: Literal["fixture", "otw"] = "fixture"
    strategy_id: str | None = None
    as_of: str | None = None


@app.get("/healthz")
def healthz():
    return {"status": "ok", "mode": "read_only_preview", "orders_enabled": False}


@app.post("/v1/analysis/preview")
def preview(req: PreviewRequest, authorization: str | None = Header(default=None)):
    # The fixture demo can run on a private loopback interface without credentials.
    # Access to OTW-backed data ALWAYS requires a configured engine API token.
    configured_token = os.getenv("ENGINE_API_TOKEN", "")
    if not configured_token and req.source == "otw":
        raise HTTPException(503, "OTW-backed preview is disabled without ENGINE_API_TOKEN")
    if configured_token:
        expected_header = f"Bearer {configured_token}"
        if not authorization or not compare_digest(authorization, expected_header):
            raise HTTPException(401, "Authentication required")
    as_of = parse_time(req.as_of) if req.as_of else datetime.now(timezone.utc)
    if as_of is None or as_of.tzinfo is None:
        raise HTTPException(422, "Invalid as_of time")
    try:
        client = OTWReadClient(os.getenv("OTW_BASE_URL", ""), os.getenv("OTW_API_TOKEN", "")) if req.source == "otw" else None
        return run_pipeline(source=req.source, as_of=as_of,
                            strategy_id=req.strategy_id or os.getenv("OTW_STRATEGY_ID", ""), client=client,
                            evidence_path=os.getenv("ENGINE_EVIDENCE_PATH") or None,
                            bars_path=os.getenv("ENGINE_BARS_PATH") or None)
    except (ValueError, OTWError, OSError) as exc:
        # Avoid returning tokens, base URLs or sensitive upstream response bodies.
        raise HTTPException(422, f"Preview failed: {type(exc).__name__}") from exc
