"""Read-only, point-in-time research context assembly for downstream research.

The builder is side-effect free: it never writes, reviews, syncs or deletes
anything in the evidence/market stores and never calls OTW. Every field is
loaded from the local stores and filtered to the requested ``as_of``.

Schema version 1 contract::

    build_research_context(
        *, root: str | Path, entity_id: str, as_of: datetime,
        asset_class: str = "stock",
    ) -> dict

    {
      "schema_version": 1,
      "entity_id": str,
      "asset_class": "stock" | "etf" | "future",
      "as_of": <ISO-8601 timestamp with timezone>,
      "data_status": "available" | "partial" | "unavailable",
      "financial_facts": [...],
      "industry_observations": [...],
      "missing_evidence": [{"evidence_type", "entity_id", "reason"}, ...],
      "provenance_warnings": [{"code", "message"}, ...],
    }

Eligibility is copied, never upgraded. ``eligible_for_assessment`` and every
review flag come from the stores unchanged, and each record keeps its unit,
period, source, audit status and assessment. A derived ``quality_flags`` list
is added for consumers that need a quick gate.

Industry observations are attributed to an entity only when the stored record
links it explicitly: ``subject`` or ``series_id`` equal to ``entity_id``, or an
``metadata`` entity reference (``entity_id``/``entity_ids``/``symbol``/
``symbols``/``asset_id``/``asset_ids``). Visible but unlinked observations are
reported through ``provenance_warnings`` instead of being silently assigned to
the entity.

``data_status`` describes data presence, not eligibility: for stocks a context
is ``"available"`` when both a visible financial fact and a visible price bar
exist; for ETFs/futures it is ``"available"`` when a visible price bar exists;
one or two present categories is ``"partial"``; nothing is ``"unavailable"``.
Industry observations are supplementary for this status.
"""

from __future__ import annotations

import re
from datetime import date, datetime
from pathlib import Path
from typing import Any, Iterable, Mapping
from zoneinfo import ZoneInfo

from .evidence import EvidenceStore, _parse_aware
from .financials import FinancialFactStore
from .market_data import MarketDataError, MarketDataStore, completed_daily_bars

SCHEMA_VERSION = 1
SUPPORTED_ASSET_CLASSES = ("stock", "etf", "future")
ENTITY_ID_PATTERN = re.compile(r"[A-Za-z0-9._-]{1,24}")
SHANGHAI = ZoneInfo("Asia/Shanghai")

_ENTITY_REFERENCE_KEYS = (
    "entity_id", "entity_ids", "symbol", "symbols", "asset_id", "asset_ids",
)


def _validate_entity_id(entity_id: str) -> str:
    if not isinstance(entity_id, str) or not ENTITY_ID_PATTERN.fullmatch(entity_id):
        raise ValueError(
            "entity_id must be 1-24 characters of letters, digits, '.', '_' or '-'"
        )
    return entity_id


def _resolve_asset_class(asset_class: str) -> str:
    if not isinstance(asset_class, str):
        raise ValueError("asset_class must be a string")
    normalized = asset_class.strip().lower()
    if normalized not in SUPPORTED_ASSET_CLASSES:
        raise ValueError(
            "asset_class must be one of: " + ", ".join(SUPPORTED_ASSET_CLASSES)
        )
    return normalized


def _fact_quality_flags(fact: Mapping[str, Any]) -> list[str]:
    flags: list[str] = []
    audit_status = str(fact.get("audit_status") or "unknown")
    if audit_status != "audited":
        flags.append(f"audit_status:{audit_status}")
    if fact.get("reviewed_by_human") is not True:
        flags.append("human_review_pending")
    if fact.get("unit_verified") is not True:
        flags.append("unit_unverified")
    if fact.get("scope_verified") is not True:
        flags.append("scope_unverified")
    if str(fact.get("assessment") or "unassessed") == "unassessed":
        flags.append("assessment_unassessed")
    extraction_status = str(fact.get("extraction_status") or "")
    if extraction_status:
        flags.append(f"extraction_status:{extraction_status}")
    if fact.get("eligible_for_assessment") is True:
        flags.append("eligible_for_assessment")
    return sorted(set(flags))


def _observation_quality_flags(observation: Mapping[str, Any]) -> list[str]:
    flags: list[str] = []
    if observation.get("reviewed_by_human") is not True:
        flags.append("human_review_pending")
    if observation.get("unit_verified") is not True:
        flags.append("unit_unverified")
    if str(observation.get("assessment") or "unassessed") not in (
        "supporting", "contradicting",
    ):
        flags.append("assessment_not_supporting_or_contradicting")
    if not observation.get("published_at"):
        flags.append("publication_timestamp_unknown")
    evidence_type = str(observation.get("evidence_type") or "")
    if evidence_type:
        flags.append(f"evidence_type:{evidence_type}")
    if observation.get("eligible_for_assessment") is True:
        flags.append("eligible_for_assessment")
    return sorted(set(flags))


def _iter_entity_references(observation: Mapping[str, Any]) -> Iterable[str]:
    metadata = observation.get("metadata")
    if not isinstance(metadata, Mapping):
        return
    for key in _ENTITY_REFERENCE_KEYS:
        value = metadata.get(key)
        if isinstance(value, str):
            yield value
        elif isinstance(value, (list, tuple, set)):
            for item in value:
                yield str(item)


def _is_linked_to_entity(observation: Mapping[str, Any], entity_id: str) -> bool:
    if str(observation.get("subject") or "") == entity_id:
        return True
    if str(observation.get("series_id") or "") == entity_id:
        return True
    return entity_id in set(_iter_entity_references(observation))


def _financial_facts(
    base: Path, entity_id: str, as_of: datetime,
) -> tuple[list[dict[str, Any]], bool]:
    """Return visible facts for the entity and whether the store file exists."""
    path = base / "evidence" / "financial_facts.json"
    if not path.is_file():
        return [], False
    # FinancialFactStore.list() applies the point-in-time cutoff on both
    # published_at and retrieved_at and downgrades review metadata that did not
    # exist yet at as_of. Review/eligibility fields are copied verbatim.
    facts = FinancialFactStore(base).list(symbol=entity_id, as_of=as_of, limit=5000)
    return facts, True


def _industry_observations(
    base: Path, entity_id: str, as_of: datetime,
) -> tuple[list[dict[str, Any]], int, bool]:
    """Return linked visible observations, unlinked visible count, file presence."""
    path = base / "evidence" / "industry_observations.json"
    if not path.is_file():
        return [], 0, False
    raw = EvidenceStore(base).list_observations(as_of=as_of, limit=5000)
    visible = [item for item in raw if item.get("visible_as_of") is True]
    linked: list[dict[str, Any]] = []
    unlinked = 0
    for item in visible:
        if _is_linked_to_entity(item, entity_id):
            linked.append(item)
        else:
            unlinked += 1
    linked.sort(key=lambda item: (str(item.get("effective_at") or ""),
                                 str(item.get("observation_id") or "")))
    return linked, unlinked, True


def _market_bars(
    base: Path, entity_id: str, asset_class: str, as_of: datetime,
) -> dict[str, Any]:
    """Load completed, fetched-by-as_of bars for the entity's local series."""
    adjustment = "continuous" if asset_class == "future" else "qfq"
    filename = f"{asset_class}_{entity_id}_{adjustment}.csv"
    path = base / filename
    result: dict[str, Any] = {
        "filename": filename,
        "present": path.is_file(),
        "adjustment": adjustment,
        "visible_bars": [],
        "excluded_future_bars": 0,
        "excluded_unfetched_bars": 0,
        "unknown_fetch_time_bars": 0,
        "quality": None,
    }
    if not path.is_file():
        return result

    store = MarketDataStore(base)
    bars = store.load(entity_id, asset_class, adjustment)
    # Drop today's daily candle until the mainland session has closed.
    completed = completed_daily_bars(bars, as_of=as_of)
    as_of_local_date = as_of.astimezone(SHANGHAI).date()
    visible = []
    for bar in completed:
        try:
            bar_date = date.fromisoformat(str(bar.timestamp)[:10])
        except ValueError as exc:
            raise MarketDataError("Invalid market bar timestamp in local store") from exc
        if bar_date > as_of_local_date:
            result["excluded_future_bars"] += 1
            continue
        fetched_at = _parse_aware(bar.fetched_at)
        if fetched_at is None:
            result["unknown_fetch_time_bars"] += 1
            continue
        if fetched_at > as_of:
            result["excluded_unfetched_bars"] += 1
            continue
        visible.append(bar)
    result["visible_bars"] = visible
    result["quality"] = store.read_quarantine(entity_id, asset_class, adjustment)
    return result


def _data_status(
    asset_class: str, *, has_facts: bool, has_observations: bool, has_market: bool,
) -> str:
    complete = has_market and (has_facts if asset_class == "stock" else True)
    if complete:
        return "available"
    if has_facts or has_observations or has_market:
        return "partial"
    return "unavailable"


def _warnings(
    facts: list[dict[str, Any]],
    linked_observations: list[dict[str, Any]],
    unlinked_observations: int,
    market: dict[str, Any],
) -> list[dict[str, Any]]:
    warnings: list[dict[str, Any]] = []

    pending_review = [
        item for item in facts
        if item.get("reviewed_by_human") is not True
        or item.get("unit_verified") is not True
        or item.get("scope_verified") is not True
    ]
    if pending_review:
        warnings.append({
            "code": "financial_facts_pending_review",
            "message": (
                f"{len(pending_review)} of {len(facts)} visible financial fact(s) "
                "are not fully human-verified; their eligibility is unchanged "
                "and none was promoted to eligible."
            ),
        })

    unaudited = [
        item for item in facts
        if str(item.get("audit_status") or "unknown") != "audited"
    ]
    if unaudited:
        warnings.append({
            "code": "financial_facts_unaudited_or_unknown",
            "message": (
                f"{len(unaudited)} visible financial fact(s) come from reports "
                "whose audit status is unaudited or unknown."
            ),
        })

    if unlinked_observations:
        warnings.append({
            "code": "industry_observations_unlinked",
            "message": (
                f"{unlinked_observations} visible industry observation(s) carry no "
                "explicit entity link and were not attributed to this entity; add "
                "metadata entity_id/entity_ids/symbol/symbols to link them."
            ),
        })

    ineligible = [
        item for item in linked_observations
        if item.get("eligible_for_assessment") is not True
    ]
    if ineligible:
        warnings.append({
            "code": "industry_observations_not_eligible",
            "message": (
                f"{len(ineligible)} of {len(linked_observations)} linked industry "
                "observation(s) are not eligible for assessment as of the cutoff; "
                "the store's reasons were preserved per record."
            ),
        })

    if market["excluded_future_bars"]:
        warnings.append({
            "code": "market_bars_after_as_of",
            "message": (
                f"{market['excluded_future_bars']} local bar(s) dated after the "
                "as_of trading date were excluded."
            ),
        })
    if market["excluded_unfetched_bars"]:
        warnings.append({
            "code": "market_bars_fetched_after_as_of",
            "message": (
                f"{market['excluded_unfetched_bars']} local bar(s) were fetched "
                "after as_of and therefore were unknown at the requested cutoff."
            ),
        })
    if market["unknown_fetch_time_bars"]:
        warnings.append({
            "code": "market_bars_without_fetch_time",
            "message": (
                f"{market['unknown_fetch_time_bars']} local bar(s) have no usable "
                "fetched_at timestamp and were excluded from the point-in-time view."
            ),
        })
    quality = market.get("quality") or {}
    excluded_rows = quality.get("excluded_count") if isinstance(quality, Mapping) else None
    if isinstance(excluded_rows, int) and excluded_rows > 0:
        warnings.append({
            "code": "market_data_quarantined_rows",
            "message": (
                f"{excluded_rows} source bar(s) for {market['filename']} are held in "
                "the local quarantine sidecar and are not part of the visible series."
            ),
        })
    return warnings


def _missing_evidence(
    *,
    entity_id: str,
    as_of: datetime,
    facts_present: bool,
    has_facts: bool,
    observations_present: bool,
    has_observations: bool,
    unlinked_observations: int,
    market: dict[str, Any],
    has_market: bool,
) -> list[dict[str, Any]]:
    missing: list[dict[str, Any]] = []
    if not has_facts:
        if not facts_present:
            reason = "financial_facts.json is absent from the local evidence store"
        else:
            reason = (
                f"no financial fact for entity {entity_id} is visible as of "
                f"{as_of.isoformat()}"
            )
        missing.append({
            "evidence_type": "financial_facts",
            "entity_id": entity_id,
            "reason": reason,
        })
    if not has_observations:
        if not observations_present:
            reason = "industry_observations.json is absent from the local evidence store"
        elif unlinked_observations:
            reason = (
                "no industry observation is linked to this entity; "
                f"{unlinked_observations} visible observation(s) carry no entity link"
            )
        else:
            reason = (
                "no industry observation is visible as of the requested cutoff"
            )
        missing.append({
            "evidence_type": "industry_observations",
            "entity_id": entity_id,
            "reason": reason,
        })
    if not has_market:
        if not market["present"]:
            reason = (
                f"no local bar series {market['filename']} "
                f"(adjustment={market['adjustment']})"
            )
        else:
            reason = (
                "no completed bar was both dated and fetched by the requested cutoff"
            )
        missing.append({
            "evidence_type": "market_bars",
            "entity_id": entity_id,
            "reason": reason,
        })
    return missing


def build_research_context(
    *,
    root: str | Path,
    entity_id: str,
    as_of: datetime,
    asset_class: str = "stock",
) -> dict[str, Any]:
    """Assemble the schema-v1 research context from local stores only.

    The function performs no writes. Missing data is reported explicitly in
    ``missing_evidence``; no placeholder values are invented. Facts and
    observations are never promoted to eligible here.
    """
    entity = _validate_entity_id(entity_id)
    resolved_asset_class = _resolve_asset_class(asset_class)
    if not isinstance(as_of, datetime) or as_of.tzinfo is None:
        raise ValueError("as_of must be a timezone-aware datetime")
    try:
        base = Path(root).expanduser()
    except TypeError as exc:
        raise ValueError("root must be a filesystem path") from exc
    if isinstance(root, str) and not root.strip():
        raise ValueError("root must not be empty")

    facts_raw, facts_present = _financial_facts(base, entity, as_of)
    observations_raw, unlinked, observations_present = _industry_observations(
        base, entity, as_of,
    )
    market = _market_bars(base, entity, resolved_asset_class, as_of)

    facts = [
        {**item, "quality_flags": _fact_quality_flags(item)}
        for item in facts_raw
    ]
    observations = [
        {
            **item,
            "entity_link": "explicit",
            "quality_flags": _observation_quality_flags(item),
        }
        for item in observations_raw
    ]

    has_facts = bool(facts)
    has_observations = bool(observations)
    has_market = bool(market["visible_bars"])

    return {
        "schema_version": SCHEMA_VERSION,
        "entity_id": entity,
        "asset_class": resolved_asset_class,
        "as_of": as_of.isoformat(),
        "data_status": _data_status(
            resolved_asset_class,
            has_facts=has_facts,
            has_observations=has_observations,
            has_market=has_market,
        ),
        "financial_facts": facts,
        "industry_observations": observations,
        "missing_evidence": _missing_evidence(
            entity_id=entity,
            as_of=as_of,
            facts_present=facts_present,
            has_facts=has_facts,
            observations_present=observations_present,
            has_observations=has_observations,
            unlinked_observations=unlinked,
            market=market,
            has_market=has_market,
        ),
        "provenance_warnings": _warnings(
            facts, observations, unlinked, market,
        ),
    }
