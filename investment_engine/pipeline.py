"""One decision run: read → hypothesize → quant diagnostic → propose → veto → export.

Does NOT call OTW write endpoints, touch balances, or submit orders.
"""
from __future__ import annotations
import hashlib
import json
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

from .analysis import SH, evaluate_risk, flatten_tiptap, parse_time, read_bars, review_evidence
from .otw_client import OTWReadClient, OTWError

ROOT = Path(__file__).resolve().parents[1]


def run_pipeline(*, source: str, as_of: datetime, strategy_id: str = "", client: OTWReadClient | None = None,
                 evidence_path: str | Path | None = None, bars_path: str | Path | None = None) -> dict[str, Any]:
    if as_of.tzinfo is None:
        raise ValueError("as_of must include an explicit timezone, e.g. +08:00")
    if as_of > datetime.now(timezone.utc) + timedelta(minutes=2):
        raise ValueError("as_of is in the future")
    if source == "fixture":
        context = json.loads((ROOT / "fixtures" / "context.synthetic.json").read_text(encoding="utf8"))
        thesis = json.loads((ROOT / "fixtures" / "thesis.synthetic.json").read_text(encoding="utf8"))
    elif source == "otw":
        if not client or not strategy_id:
            raise ValueError("OTW mode requires a client and strategy_id")
        context = client.strategy_context(strategy_id)
        holdings = context.get("account", {}).get("holdings", [])
        asset_id = holdings[0].get("id") if holdings else None
        thesis = client.asset_thesis(strategy_id, asset_id) if asset_id else None
    else:
        raise ValueError("source must be 'fixture' or 'otw'")

    strategy = context.get("strategy", {})
    sid = strategy.get("id") or strategy_id
    account = context.get("account", {})
    holdings = account.get("holdings", [])
    if source == "otw" and sid != strategy_id:
        raise OTWError("Unexpected strategy ID returned by OTW")
    if not sid or not isinstance(holdings, list):
        raise ValueError("Missing strategy ID or holdings")
    thesis_content = thesis.get("document", {}).get("content", {}) if thesis else {}
    thesis_updated_at = thesis.get("document", {}).get("updated_at") if thesis else None
    evidence_input = json.loads(Path(evidence_path).read_text(encoding="utf8")) if evidence_path else {}
    research = review_evidence(evidence_input, as_of)
    quant = read_bars(bars_path, as_of)
    held = sum(float(h.get("quantity") or 0) for h in holdings)

    if held > 0 and research["hypothesis"] == "weakened_or_falsified":
        proposal = "sell_candidate"
        rationale = "Existing holding with a challenged core thesis; exit consideration requires separate review."
    elif held == 0 and research["hypothesis"] == "conditionally_supported" and quant.get("trend") == "up":
        proposal = "buy_candidate"
        rationale = "All four evidence gates marked supporting and MA20>MA60; further validation required."
    elif held > 0:
        proposal = "hold"
        rationale = "No independently validated change requiring an order."
    else:
        proposal = "no_trade"
        rationale = "Insufficient verified evidence and/or history for a transaction."
    risk = evaluate_risk(proposal, context, as_of, quant)
    decision = "no_trade"
    # v0.1 reports all non-executed proposals as no_trade; proposal remains separately auditable.
    scheduled_at = as_of.isoformat()
    digest = hashlib.sha256(f"v0.1|{sid}|{scheduled_at}".encode()).hexdigest()[:16]
    key = f"engine-v01-{digest}"
    result = {
        "schema_version": "0.1", "local_run_id": str(uuid4()), "idempotency_key": key,
        "source": source, "strategy_id": sid, "as_of": scheduled_at,
        "account_summary": {"currency": account.get("currency"), "cash": account.get("cash"), "equity": account.get("equity"),
                            "holdings": [{"symbol": h.get("symbol"), "quantity": h.get("quantity"), "asset_id": h.get("id"), "reference_price": h.get("price"), "last_price_at": h.get("last_price_at")} for h in holdings]},
        "thesis": {"available": thesis is not None, "updated_at": thesis_updated_at, "excerpt": flatten_tiptap(thesis_content)[:500]},
        "research": research, "quant": quant, "proposal": proposal,
        "proposal_rationale": rationale, "risk": risk, "decision": decision,
        "execution": {"mode": "read_only_preview", "order_submitted": False, "run_recorded_to_otw": False},
        "limits": ["No LLM integration yet", "CSV data and user evidence are not source-verified", "No executable backtest yet", "No new OTW data or order mutations"],
    }
    result["otw_run_preview"] = {
        "idempotency_key": key,
        "scheduled_at": scheduled_at,
        "decision": decision,
        "confidence": 0,
        "evidence": [f"{name}: {item['status']} (review: {item['review_mode']})" for name, item in research["factors"].items()],
        "market_facts": {"as_of": scheduled_at, "quant_status": quant["status"], "account_source": source},
        "market_expectations": {"priced_market_expectations": research["factors"]["priced_market_expectations"]["status"]},
        "inference": rationale,
        "risk_assessment": {"pretrade_approved": risk["approved"], "reasons": risk["reasons"], "proposal": proposal,
                            "execution_enabled": False, "engine_version": "0.1.0"},
    }
    return result
