"""Evidence-first thesis assessment; price observations are not fundamental proof."""
from __future__ import annotations

import math
from datetime import date, datetime
from statistics import pstdev
from typing import Any, Sequence

from .analysis import REQUIRED_FACTORS, flatten_tiptap, parse_time
from .market_data import Bar


def summarize_price_series(bars: Sequence[Bar]) -> dict[str, Any]:
    rows = list(bars)
    if not rows:
        return {"status": "unavailable", "reason": "No stored market data"}
    closes = [float(x.close) for x in rows]
    returns = [closes[i] / closes[i - 1] - 1 for i in range(1, len(closes))]
    def change(period: int) -> float | None:
        if len(closes) <= period:
            return None
        return closes[-1] / closes[-period - 1] - 1
    def ma(period: int) -> float | None:
        return sum(closes[-period:]) / period if len(closes) >= period else None
    vol = pstdev(returns[-20:]) * math.sqrt(252) if len(returns) >= 20 else None
    return {
        "status": "available",
        "symbol": rows[0].symbol,
        "last_bar": rows[-1].timestamp,
        "last_close": closes[-1],
        "bar_count": len(rows),
        "return_20d": round(change(20), 6) if change(20) is not None else None,
        "return_60d": round(change(60), 6) if change(60) is not None else None,
        "ma20": round(ma(20), 4) if ma(20) is not None else None,
        "ma60": round(ma(60), 4) if ma(60) is not None else None,
        "realized_volatility_20d_annualized": round(vol, 6) if vol is not None else None,
        "trend": (
            "up" if ma(20) is not None and ma(60) is not None and ma(20) > ma(60)
            else "down" if ma(20) is not None and ma(60) is not None and ma(20) < ma(60)
            else "insufficient_history"
        ),
        "source": rows[0].source,
        "adjustment": rows[0].adjustment,
        "fetched_at": rows[-1].fetched_at,
    }


def _typed_evidence(items: Any, *, as_of: datetime, kind: str) -> list[dict[str, Any]]:
    if not isinstance(items, list):
        return []
    output: list[dict[str, Any]] = []
    for raw in items:
        if not isinstance(raw, dict):
            continue
        published = parse_time(raw.get("published_at"))
        effective = parse_time(raw.get("effective_at")) or published
        retrieved = parse_time(raw.get("retrieved_at"))
        url = raw.get("source_url")
        reviewed = raw.get("reviewed_by_human") is True
        valid = bool(
            published and effective and retrieved and published <= as_of and effective <= as_of and retrieved <= as_of
            and isinstance(url, str) and url.startswith("https://") and reviewed
        )
        output.append({
            "type": kind,
            "factor": str(raw.get("factor", "")),
            "assessment": raw.get("assessment") if raw.get("assessment") in ("supporting", "contradicting") else "unassessed",
            "title": str(raw.get("title", ""))[:160],
            "detail": str(raw.get("detail", ""))[:800],
            "source_url": url if isinstance(url, str) and url.startswith("https://") else None,
            "published_at": published.isoformat() if published else None,
            "effective_at": effective.isoformat() if effective else None,
            "retrieved_at": retrieved.isoformat() if retrieved else None,
            "confidence": raw.get("confidence") if isinstance(raw.get("confidence"), (int, float)) else None,
            "accepted_for_assessment": valid,
            "reason": "human-reviewed source with point-in-time dates" if valid else "unverified, missing provenance, or published/effective after as_of",
        })
    return output


def assess_hypotheses(evidence: dict[str, Any] | None, *, as_of: datetime) -> dict[str, Any]:
    raw = evidence or {}
    facts = _typed_evidence(raw.get("facts"), as_of=as_of, kind="confirmed_fact")
    expectations = _typed_evidence(raw.get("expectations"), as_of=as_of, kind="market_expectation")
    inferences = _typed_evidence(raw.get("inferences"), as_of=as_of, kind="inference")
    factors = raw.get("factors", {}) if isinstance(raw.get("factors"), dict) else {}
    checks: dict[str, Any] = {}
    requirements = {
        "actual_supply_change": "confirmed_fact",
        "multiweek_inventory_trend": "confirmed_fact",
        "profit_pass_through": "confirmed_fact",
        "priced_market_expectations": "market_expectation",
    }
    for name in REQUIRED_FACTORS:
        needed = requirements[name]
        eligible = [item for item in (facts if needed == "confirmed_fact" else expectations)
                    if item["accepted_for_assessment"] and item.get("factor") == name]
        # Explicit factor records remain supported for backwards compatibility.
        explicit = factors.get(name, {}) if isinstance(factors, dict) else {}
        if isinstance(explicit, dict):
            published = parse_time(explicit.get("published_at"))
            url = explicit.get("source_url")
            valid = (
                explicit.get("reviewed_by_human") is True and published is not None
                and published <= as_of and isinstance(url, str) and url.startswith("https://")
                and explicit.get("state") in ("supporting", "contradicting")
            )
        else:
            valid = False
        typed_assessments = [item["assessment"] for item in eligible if item["assessment"] in ("supporting", "contradicting")]
        typed_state = "contradicting" if "contradicting" in typed_assessments else (
            "supporting" if typed_assessments and all(x == "supporting" for x in typed_assessments) else "unknown"
        )
        state = explicit.get("state") if valid else typed_state
        checks[name] = {
            "required_evidence_type": needed,
            "status": state if state in ("supporting", "contradicting") else "unknown",
            "evidence_count": len(eligible) + (1 if valid else 0),
            "missing": not valid and not eligible,
            "note": "Price/technical indicators cannot establish this fundamental claim." if name != "priced_market_expectations" else "Price movement alone cannot reveal what the market had already priced in.",
        }
    states = [item["status"] for item in checks.values()]
    overall = "weakened_or_falsified" if "contradicting" in states else (
        "conditionally_supported" if all(x == "supporting" for x in states) else "insufficient_evidence"
    )
    return {
        "overall": overall,
        "checks": checks,
        "evidence_register": {"confirmed_facts": facts, "market_expectations": expectations, "inferences": inferences},
        "rules": [
            "Confirmed facts, market expectations and analyst inferences are stored separately.",
            "Only human-reviewed evidence with HTTPS source and publication/effective timestamps not later than as_of is eligible.",
            "Inferences never count as confirmed facts or market expectations.",
            "OHLCV and futures prices are observations, not proof of physical supply, inventory or company profit pass-through.",
        ],
        "missing_factors": [name for name, item in checks.items() if item["missing"]],
    }


def build_research_snapshot(*, strategy_id: str, symbol: str, thesis_content: Any,
                             stock_bars: Sequence[Bar], futures_symbol: str | None = None,
                             futures_bars: Sequence[Bar] = (), evidence: dict[str, Any] | None = None,
                             strategy_name: str | None = None, thesis_updated_at: str | None = None,
                             account_summary: dict[str, Any] | None = None,
                             as_of: datetime) -> dict[str, Any]:
    stock_summary = summarize_price_series(stock_bars)
    futures_summary = summarize_price_series(futures_bars)
    return {
        "schema_version": "0.2",
        "strategy_id": strategy_id,
        "strategy_name": strategy_name,
        "symbol": symbol,
        "as_of": as_of.isoformat(),
        "account_summary": account_summary or {},
        "thesis": {
            "available": bool(thesis_content),
            "updated_at": thesis_updated_at,
            "excerpt": flatten_tiptap(thesis_content)[:1200],
        },
        "market_observations": {
            "equity": stock_summary,
            "commodity_reference": {
                "symbol": futures_symbol,
                **futures_summary,
                "interpretation": "Continuous futures series is a market reference, not the company's realized purchase/sales price.",
            } if futures_symbol else {"status": "not_requested"},
        },
        "hypothesis_assessment": assess_hypotheses(evidence, as_of=as_of),
        "decision_readiness": {
            "status": "research_only",
            "trade_eligible": False,
            "reasons": [
                "This phase supplies market observations and evidence bookkeeping, not an LLM-generated investment conclusion.",
                "Fundamental supply, inventory, company earnings and priced expectations require their own dated source records.",
                "No order or OTW write operation is available in this service.",
            ],
        },
    }
