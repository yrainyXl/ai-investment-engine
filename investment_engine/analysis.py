"""Deterministic evidence, quant diagnostics and independent pre-trade vetoes.

No LLM-generated assertion is considered verified evidence in v0.1.
"""
from __future__ import annotations
import csv
import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

SH = ZoneInfo("Asia/Shanghai")
REQUIRED_FACTORS = (
    "actual_supply_change",
    "multiweek_inventory_trend",
    "profit_pass_through",
    "priced_market_expectations",
)


def parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return None if dt.tzinfo is None else dt
    except (TypeError, ValueError):
        return None


def flatten_tiptap(content: Any) -> str:
    texts: list[str] = []
    def visit(node: Any) -> None:
        if isinstance(node, dict):
            if node.get("type") == "text":
                texts.append(str(node.get("text", "")))
            for child in node.get("content", []) if isinstance(node.get("content"), list) else []:
                visit(child)
        elif isinstance(node, list):
            for child in node:
                visit(child)
    visit(content)
    return " ".join(texts)


def review_evidence(raw: dict[str, Any], as_of: datetime) -> dict[str, Any]:
    factors = raw.get("factors", {}) if isinstance(raw, dict) else {}
    if not isinstance(factors, dict):
        factors = {}
    checked: dict[str, Any] = {}
    for name in REQUIRED_FACTORS:
        item = factors.get(name, {})
        if not isinstance(item, dict):
            item = {}
        state = item.get("state", "unknown")
        timestamp = parse_time(item.get("published_at"))
        effective = parse_time(item.get("effective_at"))
        retrieved = parse_time(item.get("retrieved_at"))
        url = item.get("source_url", "")
        declared_verified = item.get("reviewed_by_human") is True
        time_valid = (
            timestamp is not None and effective is not None and retrieved is not None
            and timestamp <= as_of and effective <= as_of and retrieved <= as_of
        )
        acceptable_url = isinstance(url, str) and url.startswith("https://")
        status = state if state in ("supporting", "contradicting") and time_valid and acceptable_url and declared_verified else "unknown"
        checked[name] = {
            "status": status,
            "detail": str(item.get("summary", ""))[:400],
            "source_url": url if acceptable_url else "",
            "published_at": item.get("published_at") if time_valid else None,
            "review_mode": "manually_marked; provenance timestamps checked, source not independently fetched" if status != "unknown" else "unverified or incomplete provenance",
        }
    statuses = [v["status"] for v in checked.values()]
    if "contradicting" in statuses:
        hypothesis = "weakened_or_falsified"
    elif all(s == "supporting" for s in statuses):
        hypothesis = "conditionally_supported"
    else:
        hypothesis = "insufficient_evidence"
    return {"hypothesis": hypothesis, "factors": checked, "provenance_warning": "Evidence is user-supplied; source URLs are not independently fetched. Eligible records require publication, effective and retrieval timestamps no later than as_of."}


def read_bars(path: str | Path | None, as_of: datetime) -> dict[str, Any]:
    if not path:
        return {"status": "unavailable", "reason": "No historical bars provided; OTW histdata datasets are currently empty.", "bar_count": 0, "eligible_for_trading": False}
    try:
        with open(path, encoding="utf-8-sig", newline="") as f:
            rows = list(csv.DictReader(f))
        parsed: list[tuple[str, float]] = []
        for row in rows:
            date = row["date"]
            dt = datetime.fromisoformat(date).date()
            price = float(row["close"])
            if dt > as_of.astimezone(SH).date() or not math.isfinite(price) or price <= 0:
                return {"status": "invalid", "reason": "Future-dated or invalid close found", "eligible_for_trading": False}
            parsed.append((date, price))
        if len(set(d for d, _ in parsed)) != len(parsed) or parsed != sorted(parsed):
            return {"status": "invalid", "reason": "Duplicate or non-monotonic dates", "eligible_for_trading": False}
        if len(parsed) < 60:
            return {"status": "insufficient", "reason": "Need at least 60 completed daily bars", "bar_count": len(parsed), "eligible_for_trading": False}
        closes = [p for _, p in parsed]
        ma20, ma60 = sum(closes[-20:]) / 20, sum(closes[-60:]) / 60
        return {
            "status": "computed", "bar_count": len(parsed), "last_bar": parsed[-1][0],
            "ma20": round(ma20, 4), "ma60": round(ma60, 4),
            "trend": "up" if ma20 > ma60 else "down" if ma20 < ma60 else "flat",
            "reason": "Diagnostic only; no transaction costs, survivorship, point-in-time or out-of-sample backtest yet.",
            "eligible_for_trading": False,  # CSV origin is not independently validated
        }
    except (OSError, ValueError, KeyError) as exc:
        return {"status": "invalid", "reason": str(exc), "eligible_for_trading": False}


def evaluate_risk(proposal: str, context: dict[str, Any], as_of: datetime, quant: dict[str, Any]) -> dict[str, Any]:
    reasons: list[str] = []
    account = context.get("account", {})
    strategy = context.get("strategy", {})
    if proposal in ("buy_candidate", "sell_candidate"):
        reasons.append("V0.1 trading is frozen; no simulation orders may be submitted.")
        reasons.append("Historic experiment audit gap and order-restoration acceptance not yet resolved.")
        if not quant.get("eligible_for_trading", False):
            reasons.append("Price history / signal has not passed data-quality and point-in-time validation.")
        if strategy.get("status") != "active":
            reasons.append("OTW strategy is not active.")
        if account.get("unpriced_holdings"):
            reasons.append("Account includes unpriced holdings.")
        for position in account.get("holdings", []):
            quote = parse_time(position.get("last_price_at"))
            if quote is None:
                reasons.append("No timestamped market quote.")
            elif quote > as_of:
                reasons.append("Quote is newer than decision as_of: possible look-ahead.")
            elif (as_of - quote).total_seconds() > 900:
                reasons.append("Quote is stale beyond 15 minutes for order consideration.")
    return {"approved": False if proposal in ("buy_candidate", "sell_candidate") else True,
            "reasons": reasons,
            "execution_enabled": False,
            "otw_service_side_risk_checks_required": True}
