"""Point-in-time research inputs from the existing Engine data stores."""
from __future__ import annotations

from datetime import date, datetime, time, timezone
from decimal import Decimal, DecimalException, localcontext
import json
from pathlib import Path
import re
from typing import Any
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from .evidence import EvidenceStore
from .market_data import (
    Bar,
    MarketDataError,
    MarketDataStore,
    completed_daily_bars,
    validate_bars,
)
from .research_context import build_research_context
from .strategy_experiments import dataset_fingerprint
from .valuation import DCFInput, ValuationError, calculate_dcf

SHANGHAI = ZoneInfo("Asia/Shanghai")
MARKET_MAX_AGE_DAYS = 7
FINANCIAL_MAX_AGE_DAYS = 190
_SYMBOL = re.compile(r"(\d{6})(?:\.(SH|SZ|BJ))?", re.ASCII)


def _timestamp(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value)
        return parsed if parsed.utcoffset() is not None else None
    except ValueError:
        return None


def _day(value: Any) -> date | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = date.fromisoformat(value)
        return parsed if value == parsed.isoformat() else None
    except ValueError:
        return None


def _decimal(value: Any) -> Decimal | None:
    try:
        number = Decimal(str(value))
        return number if number.is_finite() else None
    except (ValueError, DecimalException):
        return None


def normalize_research_symbol(entity_id: str) -> str:
    """Map exchange-qualified mainland codes to the stores' bare symbols."""
    match = _SYMBOL.fullmatch(entity_id.strip().upper())
    if not match:
        raise ValueError("entity_id must be a six-digit mainland stock or ETF code")
    symbol, suffix = match.groups()
    expected = (
        "SH" if symbol.startswith(("5", "6")) else
        "SZ" if symbol.startswith(("0", "1", "2", "3")) else
        "BJ" if symbol.startswith(("4", "8", "9")) else None
    )
    if suffix and suffix != expected:
        raise ValueError("entity_id exchange suffix does not match the code")
    return symbol


def _market_series(
    store: MarketDataStore, symbol: str, asset_class: str, adjustment: str, as_of: datetime,
) -> tuple[list[Bar], dict[str, Any]]:
    summary: dict[str, Any] = {
        "adjustment": adjustment,
        "status": "unavailable",
        "reasons": [],
    }
    try:
        rows = store.load(symbol, asset_class, adjustment)
        completed = completed_daily_bars(rows, as_of=as_of)
        visible: list[Bar] = []
        excluded_unavailable = 0
        for row in completed:
            day = _day(row.timestamp)
            fetched = _timestamp(row.fetched_at)
            if day is None or fetched is None or fetched > as_of:
                excluded_unavailable += 1
                continue
            if fetched < datetime.combine(day, time(15, 5), SHANGHAI):
                excluded_unavailable += 1
                continue
            visible.append(row)
    except (MarketDataError, OSError, ValueError, KeyError, TypeError):
        summary["reasons"].append("invalid_market_series")
        return [], summary

    summary["excluded_incomplete_or_future"] = len(rows) - len(completed)
    summary["excluded_unknown_at_cutoff"] = excluded_unavailable
    if not visible:
        summary["reasons"].append("no_completed_bars_visible_as_of")
        return [], summary

    cutoff = as_of.astimezone(SHANGHAI).date()
    quality = validate_bars(visible, as_of=cutoff)
    tags = {(bar.exchange, bar.source, bar.currency, bar.volume_unit) for bar in visible}
    if len(tags) != 1 or any(
        bar.symbol != symbol or bar.asset_type != asset_class
        or bar.adjustment != adjustment or bar.interval != "1d"
        or bar.exchange not in {"SSE", "SZSE", "BSE"}
        or not bar.source.strip() or bar.currency != "CNY"
        or bar.volume_unit not in {"lots", "shares", "units", "provider_units"}
        for bar in visible
    ):
        quality["valid"] = False
        quality["errors"].append("inconsistent price identity, source, interval, currency or volume unit")

    try:
        sidecar = store.read_quarantine(symbol, asset_class, adjustment)
        recorded = _timestamp(sidecar.get("recorded_at"))
        if recorded is not None and recorded <= as_of and sidecar.get("excluded_count", 0) > 0:
            quality["warnings"].append("source_rows_quarantined")
    except (MarketDataError, OSError, ValueError, TypeError):
        quality["warnings"].append("invalid_quality_sidecar")

    summary["quality"] = quality
    if not quality["valid"]:
        summary["reasons"].append("market_quality_failed")
        return [], summary

    last_date = date.fromisoformat(visible[-1].timestamp)
    age = (cutoff - last_date).days
    summary.update({
        "status": "available" if age <= MARKET_MAX_AGE_DAYS else "stale",
        "age_calendar_days": age,
        "source": visible[-1].source,
        "exchange": visible[-1].exchange,
        "currency": visible[-1].currency,
        "volume_unit": visible[-1].volume_unit,
        "price_observed_at": datetime.combine(last_date, time(15), SHANGHAI).isoformat(),
        "retrieved_at": max(datetime.fromisoformat(bar.fetched_at) for bar in visible).isoformat(),
        "dataset_fingerprint": dataset_fingerprint(visible),
        "latest_close": visible[-1].close,
    })
    if age > MARKET_MAX_AGE_DAYS:
        summary["reasons"].append("latest_close_is_stale")
    if adjustment != "none":
        summary["reasons"].append("adjusted_prices_are_informational")
    if visible[-1].volume_unit == "provider_units":
        summary["quality"]["warnings"].append("provider_volume_scale_not_normalized")
    return visible, summary


def _source_valid(record: dict, id_key: str) -> bool:
    url = record.get("source_url")
    if not isinstance(url, str):
        return False
    try:
        parsed = urlsplit(url)
    except ValueError:
        return False
    return bool(
        record.get(id_key) and record.get("source_name")
        and parsed.scheme == "https" and parsed.hostname
        and parsed.username is None and parsed.password is None
    )


def _financial_visible(record: dict, as_of: datetime) -> bool:
    start = _day(record.get("period_start"))
    end = _day(record.get("period_end"))
    published = _timestamp(record.get("published_at"))
    retrieved = _timestamp(record.get("retrieved_at"))
    return bool(
        start and end and start <= end <= as_of.astimezone(SHANGHAI).date()
        and published and retrieved and published <= retrieved <= as_of
        and end <= published.astimezone(SHANGHAI).date()
        and _source_valid(record, "fact_id")
        and record.get("source_document_id")
        and record.get("metric") and record.get("unit") and record.get("currency")
        and _decimal(record.get("value")) is not None
    )


def _observation_review_visible(record: dict, as_of: datetime) -> bool:
    metadata = record.get("metadata") or {}
    reviewed = _timestamp(metadata.get("reviewed_at")) if isinstance(metadata, dict) else None
    retrieved = _timestamp(record.get("retrieved_at"))
    published = _timestamp(record.get("published_at"))
    effective = _timestamp(record.get("effective_at"))
    return bool(
        reviewed and retrieved and published and effective
        and max(published, effective) <= retrieved <= reviewed <= as_of
        and record.get("reviewed_by_human") is True
        and record.get("unit_verified") is True
    )


def _fact_ratio(
    facts: list[dict], bars: list[Bar], market: dict, as_of: datetime,
    *, metric: str, period_type: str,
) -> dict:
    base = {"status": "not_evaluable", "value": None, "source_ids": []}
    if not bars or market["status"] != "available":
        return {**base, "reasons": ["fresh_unadjusted_close_required"]}
    visible = [
        fact for fact in facts
        if fact.get("metric") == metric and fact.get("period_type") == period_type
        and fact.get("statement_scope") == "consolidated"
    ]
    if not visible:
        return {**base, "reasons": [f"reviewed_{metric}_{period_type}_required"]}

    # A newer pending or conflicting record blocks use of an older version.
    newest_end = max(fact["period_end"] for fact in visible)
    candidates = [fact for fact in visible if fact["period_end"] == newest_end]
    if any(fact.get("eligible_for_assessment") is not True for fact in candidates):
        return {**base, "reasons": ["latest_financial_period_pending_review"]}
    if any(
        fact["unit"] != "CNY/share" or fact["currency"] != "CNY"
        or fact.get("value_scale") != "yuan_per_share"
        for fact in candidates
    ):
        return {**base, "reasons": ["unverified_per_share_currency_or_scale"]}
    if (as_of.astimezone(SHANGHAI).date() - date.fromisoformat(newest_end)).days > FINANCIAL_MAX_AGE_DAYS:
        return {**base, "reasons": ["financial_period_is_stale"]}
    if period_type == "ttm" and any(
        not 364 <= (date.fromisoformat(fact["period_end"]) - date.fromisoformat(fact["period_start"])).days <= 365
        for fact in candidates
    ):
        return {**base, "reasons": ["invalid_ttm_period"]}
    if period_type == "point_in_time" and any(fact["period_start"] != newest_end for fact in candidates):
        return {**base, "reasons": ["invalid_point_in_time_period"]}
    values = {_decimal(fact["value"]) for fact in candidates}
    if len(values) != 1:
        return {**base, "reasons": ["conflicting_financial_values"]}
    value = next(iter(values))
    if value is None or value <= 0:
        return {**base, "reasons": ["positive_per_share_denominator_required"]}
    try:
        with localcontext() as context:
            context.prec = 28
            ratio = Decimal(str(bars[-1].close)) / value
    except DecimalException:
        return {**base, "reasons": ["ratio_arithmetic_out_of_range"]}
    return {
        "status": "calculated",
        "value": str(ratio),
        "interpretation": "observed_market_multiple",
        "source_ids": sorted({fact["fact_id"] for fact in candidates}),
        "price_source": market["source"],
        "price_observed_at": market["price_observed_at"],
        "price_dataset_fingerprint": market["dataset_fingerprint"],
        "denominator_period_end": newest_end,
        "reasons": [],
    }


def _etf_valuation(observations: list[dict], bars: list[Bar], market: dict) -> dict:
    unavailable = {"status": "not_evaluable", "premium_to_nav": None}
    if not bars or market["status"] != "available":
        return {**unavailable, "reasons": ["fresh_unadjusted_close_required"]}
    candidates = [
        observation for observation in observations
        if observation.get("metric") in {"nav_per_share", "etf_nav"}
        and observation["evidence_type"] == "confirmed_fact"
        and observation["unit"] in {"CNY/share", "CNY/unit"}
        and _timestamp(observation["effective_at"]).astimezone(SHANGHAI).date().isoformat() == bars[-1].timestamp
    ]
    if not candidates or any(not item["reviewed_by_human"] for item in candidates):
        return {**unavailable, "reasons": ["reviewed_nav_on_close_session_required"]}
    values = {_decimal(item["value"]) for item in candidates}
    if len(values) != 1:
        return {**unavailable, "reasons": ["conflicting_nav_values"]}
    nav = next(iter(values))
    if nav is None or nav <= 0:
        return {**unavailable, "reasons": ["positive_nav_required"]}
    try:
        premium = Decimal(str(bars[-1].close)) / nav - 1
    except DecimalException:
        return {**unavailable, "reasons": ["nav_arithmetic_out_of_range"]}
    return {
        "status": "calculated",
        "premium_to_nav": str(premium),
        "source_ids": sorted({item["observation_id"] for item in candidates}),
        "price_source": market["source"],
        "price_observed_at": market["price_observed_at"],
        "price_dataset_fingerprint": market["dataset_fingerprint"],
        "reasons": [],
    }


def _dcf_assumptions(root: str | Path, symbol: str, as_of: datetime) -> dict | None:
    path = Path(root).expanduser() / "evidence" / f"dcf_assumptions_{symbol}.json"
    if not path.is_file():
        return None
    with path.open(encoding="utf-8") as handle:
        payload = handle.read(65_537)
    if len(payload) > 65_536:
        raise ValueError("DCF assumptions store is too large")
    raw = json.loads(payload)
    if not isinstance(raw, list) or not all(isinstance(item, dict) for item in raw):
        raise ValueError("invalid DCF assumptions store")
    visible = []
    for item in raw:
        if item.get("entity_id") != symbol:
            continue
        created = _timestamp(item.get("assumptions_as_of"))
        if created and created <= as_of:
            visible.append((created, item))
    if not visible:
        return None
    newest = max(created for created, _ in visible)
    current = [item for created, item in visible if created == newest]
    if len(current) != 1:
        raise ValueError("ambiguous DCF assumptions version")
    return current[0]


def _dcf(
    root: str | Path, symbol: str, facts: list[dict], as_of: datetime,
) -> dict:
    unavailable = {"status": "not_evaluable", "value_per_share": None}
    try:
        assumptions = _dcf_assumptions(root, symbol, as_of)
        if assumptions is None:
            return {**unavailable, "reasons": ["reviewed_dcf_assumptions_required"]}
        assumed_at = _timestamp(assumptions.get("assumptions_as_of"))
        reviewed_at = _timestamp(assumptions.get("reviewed_at"))
        if (
            not _source_valid(assumptions, "assumptions_id")
            or assumptions.get("reviewed_by_human") is not True
            or reviewed_at is None or not assumed_at <= reviewed_at <= as_of
            or assumptions.get("currency") != "CNY"
        ):
            return {**unavailable, "reasons": ["dcf_assumptions_provenance_or_review_invalid"]}
        forecast = assumptions.get("forecast_fcf")
        if not isinstance(forecast, list) or not 1 <= len(forecast) <= 20:
            return {**unavailable, "reasons": ["dcf_forecast_horizon_invalid"]}

        inputs = {}
        for field, unit, scale in (("net_debt", "CNY", "yuan"), ("shares_outstanding", "shares", "shares")):
            matching = [
                record for record in facts
                if record.get("metric") == field
                and record.get("period_type") == "point_in_time"
                and record.get("statement_scope") == "consolidated"
            ]
            input_id = assumptions.get(f"{field}_source_id")
            candidates = [
                record for record in matching
                if record["fact_id"] == input_id
                and record["eligible_for_assessment"]
                and record["currency"] == "CNY" and record["unit"] == unit
                and record.get("value_scale") == scale and record["period_start"] == record["period_end"]
                and _timestamp(record["retrieved_at"]) <= assumed_at
                and _timestamp(record["reviewed_at"]) <= reviewed_at
            ]
            if len(candidates) != 1:
                return {**unavailable, "reasons": [f"reviewed_{field}_source_fact_required"]}
            fact = candidates[0]
            cutoff_day = as_of.astimezone(SHANGHAI).date()
            if (
                (cutoff_day - date.fromisoformat(fact["period_end"])).days > FINANCIAL_MAX_AGE_DAYS
                or any(item["period_end"] > fact["period_end"] for item in matching)
            ):
                return {**unavailable, "reasons": [f"{field}_statement_period_is_stale"]}
            inputs[field] = fact
        debt = inputs["net_debt"]
        shares = inputs["shares_outstanding"]
        if debt["period_end"] != shares["period_end"]:
            return {**unavailable, "reasons": ["dcf_statement_periods_do_not_match"]}

        def effective_at(fact: dict) -> str:
            return datetime.combine(date.fromisoformat(fact["period_end"]), time.max, SHANGHAI).isoformat()

        result = calculate_dcf(
            DCFInput(
                entity_id=symbol, currency="CNY", forecast_fcf=tuple(forecast),
                discount_rate=assumptions["discount_rate"], terminal_growth=assumptions["terminal_growth"],
                net_debt=debt["value"], shares_outstanding=shares["value"],
                net_debt_source_id=debt["fact_id"], shares_source_id=shares["fact_id"],
                net_debt_published_at=debt["published_at"], net_debt_effective_at=effective_at(debt),
                net_debt_retrieved_at=debt["retrieved_at"], net_debt_reviewed_at=debt["reviewed_at"],
                shares_published_at=shares["published_at"], shares_effective_at=effective_at(shares),
                shares_retrieved_at=shares["retrieved_at"], shares_reviewed_at=shares["reviewed_at"],
                assumptions_as_of=assumptions["assumptions_as_of"],
                assumptions_reviewed_at=assumptions["reviewed_at"],
                assumptions_reviewed_by_human=True, source_facts_reviewed_by_human=True,
                unit_verified=True, scope_verified=True,
            ),
            as_of=as_of.isoformat(),
        ).to_dict()
    except (OSError, ValuationError, ValueError, TypeError, KeyError, DecimalException):
        return {**unavailable, "reasons": ["dcf_input_invalid_or_unavailable"]}

    return {
        **result,
        "interpretation": "reviewed_forecast_scenario",
        "assumptions": {
            "source_id": assumptions["assumptions_id"],
            "source_name": assumptions["source_name"],
            "source_url": assumptions["source_url"],
            "as_of": assumptions["assumptions_as_of"],
            "reviewed_at": assumptions["reviewed_at"],
        },
    }


def _valuation(
    root: str | Path, symbol: str, asset_class: str, facts: list[dict], observations: list[dict],
    bars: list[Bar], market: dict, as_of: datetime,
) -> dict:
    base = {"entity_id": symbol, "as_of": as_of.isoformat()}
    if asset_class == "etf":
        return {**base, **_etf_valuation(observations, bars, market)}
    methods = {
        "pe": _fact_ratio(facts, bars, market, as_of, metric="earnings_per_share", period_type="ttm"),
        "pb": _fact_ratio(facts, bars, market, as_of, metric="book_value_per_share", period_type="point_in_time"),
        "dcf": _dcf(root, symbol, facts, as_of),
    }
    calculated = any(method["status"] == "calculated" for method in methods.values())
    return {
        **base,
        **methods,
        "status": "calculated" if calculated else "not_evaluable",
        "reasons": [] if calculated else [
            f"{name}:{reason}"
            for name, method in methods.items()
            for reason in method["reasons"]
        ],
    }


def resolve_research_inputs(
    *, root: str | Path, entity_id: str, asset_class: str, as_of: datetime,
) -> dict[str, Any]:
    """Resolve stock/ETF observations and independent, attributable valuation inputs."""
    if as_of.utcoffset() is None or as_of > datetime.now(timezone.utc):
        raise ValueError("as_of must be timezone-aware and cannot be in the future")
    if asset_class not in {"stock", "etf"}:
        raise ValueError("asset_class must be stock or etf")
    symbol = normalize_research_symbol(entity_id)
    store = MarketDataStore(root)
    bars, market = _market_series(store, symbol, asset_class, "none", as_of)
    _, adjusted = _market_series(store, symbol, asset_class, "qfq", as_of)

    # Context reuse retains the site's existing evidence linkage and financial
    # review rules. Resolve the actual market series separately for this entry point.
    context = build_research_context(
        root=root, entity_id=symbol, as_of=as_of, asset_class=asset_class, include_market=False,
    )
    facts = []
    for record in context["financial_facts"]:
        if not _financial_visible(record, as_of):
            continue
        fact = {k: v for k, v in record.items() if k not in {"reviewer_id", "review_note"}}
        if fact["eligible_for_assessment"]:
            review = _timestamp(fact.get("reviewed_at"))
            retrieved = _timestamp(fact.get("retrieved_at"))
            if review is None or retrieved is None or not retrieved <= review <= as_of:
                fact["eligible_for_assessment"] = False
                fact["eligibility_reason"] = "review must occur after retrieval and no later than as_of"
        facts.append(fact)
    observations = []
    for record in context["industry_observations"]:
        if not _source_valid(record, "observation_id"):
            continue
        value = {k: v for k, v in record.items() if k != "metadata"}
        reviewed = _observation_review_visible(record, as_of)
        value["reviewed_by_human"] = reviewed
        value["reviewed_at"] = record["metadata"]["reviewed_at"] if reviewed else None
        value["eligible_for_assessment"] = reviewed and record["eligible_for_assessment"]
        if not reviewed:
            value["assessment"] = "unassessed"
            value["quality_flags"] = sorted({
                flag for flag in value["quality_flags"] if flag != "eligible_for_assessment"
            } | {"observation_review_timestamp_required_by_as_of"})
            value["reasons"] = [*record["reasons"], "observation_review_timestamp_required_by_as_of"]
        observations.append(value)
    context["financial_facts"] = facts
    context["industry_observations"] = observations
    context["missing_evidence"] = [
        item for item in context["missing_evidence"] if item["evidence_type"] != "market_bars"
    ]
    context["provenance_warnings"] = [
        item for item in context["provenance_warnings"] if not item["code"].startswith("market_")
    ]
    if not bars or market["status"] != "available":
        context["missing_evidence"].append({
            "evidence_type": "market_bars",
            "entity_id": symbol,
            "reason": "; ".join(market["reasons"]),
        })
    if not any(fact["eligible_for_assessment"] for fact in facts) and asset_class == "stock":
        context["missing_evidence"].append({
            "evidence_type": "reviewed_financial_facts",
            "entity_id": symbol,
            "reason": "no financial facts cleared review, scope, unit and point-in-time checks",
        })
    has_reviewed_facts = any(fact.get("eligible_for_assessment") is True for fact in facts)
    context["data_status"] = (
        "available" if market["status"] == "available" and (has_reviewed_facts or asset_class == "etf")
        else "partial" if bars or facts or observations
        else "unavailable"
    )

    disclosures = EvidenceStore(root).list_documents(symbol=symbol, as_of=as_of, limit=2000)
    return {
        "context": context,
        "bars": bars,
        "market_data": {**market, "adjusted_reference": adjusted},
        "valuation": _valuation(root, symbol, asset_class, facts, observations, bars, market, as_of),
        "disclosures": [record for record in disclosures if _source_valid(record, "document_id")],
        "confirmed_facts": [
            *[fact for fact in facts if fact["eligible_for_assessment"]],
            *[
                observation for observation in observations
                if observation["reviewed_by_human"] and observation["evidence_type"] == "confirmed_fact"
            ],
        ],
        "market_expectations": [
            observation for observation in observations
            if observation["evidence_type"] == "market_expectation" and observation["reviewed_by_human"]
        ],
    }
