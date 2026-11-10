"""Deterministic decision-report synthesis for the E2E research loop.

This module is a pure function boundary. It consumes the research context built
by the data layer, hypothesis evaluation results, an optional valuation result
and an optional risk report, and produces one research conclusion. It has no
network, OTW, broker, order, clock or storage dependency.

The conclusion is a research artifact, never an order: ``action="no_trade"``,
``orders_enabled=False`` and ``eligible_for_trading=False`` are hard-coded.
``review_ready`` only means that the evidence bundle passed quality control and
may be reviewed by a human.

Fail-closed policy
------------------
* Structurally invalid top-level arguments raise :class:`DecisionReportError`.
* Unknown enum values, malformed records and internally inconsistent records
  never become supporting evidence. They are listed in ``missing_evidence``
  and force ``insufficient_evidence``.
* A record that claims a positive status while still carrying quality-control
  reasons (for example "human review is pending") cannot pass. This prevents
  forged positive text from bypassing the hypothesis/valuation review gates.
* A reliable (``status="assessed"``) risk report with limit breaches yields
  ``risk_blocked``: a hard concentration/drawdown breach outranks incomplete
  evidence because it is independent of the missing evidence.
"""
from __future__ import annotations

from datetime import date, datetime, timezone

SCHEMA_VERSION = "1"

CONTEXT_DATA_STATUSES = frozenset({"available", "partial", "unavailable"})
HYPOTHESIS_STATUSES = frozenset(
    {"supported", "contradicted", "inconclusive", "not_evaluable"}
)
VALUATION_STATUSES = frozenset({"calculated", "not_evaluable"})
RISK_STATUSES = frozenset({"assessed", "not_evaluable"})

DECISION_STATUSES = ("insufficient_evidence", "risk_blocked", "review_ready")


class DecisionReportError(ValueError):
    """Malformed top-level input: the report cannot be attributed safely."""


def synthesize_decision_report(
    *,
    context: dict,
    hypothesis_results: list[dict],
    risk_report: dict | None,
    valuation_result: dict | None = None,
) -> dict:
    """Aggregate already-produced evidence into a fail-closed study conclusion.

    The returned mapping mirrors the sprint contract: it always carries
    ``action="no_trade"``, ``orders_enabled=False`` and
    ``eligible_for_trading=False``. See the module docstring for the
    ``decision_status`` precedence.
    """
    if not isinstance(context, dict):
        raise DecisionReportError("context must be a mapping")
    entity_id = context.get("entity_id")
    if not _is_text(entity_id):
        raise DecisionReportError("context.entity_id is required")
    as_of_text = context.get("as_of")
    cutoff = _timestamp(as_of_text, "context.as_of")

    supporting: list[dict] = []
    contradicting: list[dict] = []
    missing: list[str] = []
    flags: list[str] = []

    blocked = _inspect_context(context, cutoff, missing, flags)
    blocked = _inspect_hypotheses(
        hypothesis_results, entity_id, cutoff, supporting, contradicting, missing
    ) or blocked
    blocked = _inspect_valuation(valuation_result, entity_id, cutoff, missing) or blocked
    breaches, risk_reliable = _inspect_risk(risk_report, cutoff, missing, flags)
    blocked = blocked or not risk_reliable

    decision_status = (
        "risk_blocked"
        if breaches
        else "insufficient_evidence"
        if blocked
        else "review_ready"
    )
    return {
        "schema_version": SCHEMA_VERSION,
        "entity_id": entity_id,
        "as_of": as_of_text,
        "decision_status": decision_status,
        "action": "no_trade",
        "orders_enabled": False,
        "eligible_for_trading": False,
        "supporting_factors": supporting,
        "contradicting_factors": contradicting,
        "missing_evidence": missing,
        "risk_flags": flags,
        "rationale": _rationale(
            decision_status, breaches, missing, supporting, contradicting
        ),
    }


def _inspect_context(
    context: dict, cutoff: datetime, missing: list[str], flags: list[str]
) -> bool:
    """Validate point-in-time evidence quality; return True when blocked."""
    blocked = False
    data_status = context.get("data_status")
    if not _is_text(data_status) or data_status not in CONTEXT_DATA_STATUSES:
        blocked = True
        _append(missing, f"context.data_status_unknown:{data_status!r}")
    elif data_status == "unavailable":
        blocked = True
        _append(missing, "context.data_unavailable")
    elif data_status == "partial":
        # Partial context means at least one core evidence family is missing.
        blocked = True
        _append(missing, "context.data_partial")

    facts = context.get("financial_facts", [])
    if not isinstance(facts, (list, tuple)):
        blocked = True
        _append(missing, "context.financial_facts_malformed")
        facts = []
    reviewed = 0
    for index, fact in enumerate(facts):
        if not isinstance(fact, dict):
            blocked = True
            _append(missing, f"context.financial_fact_malformed:{index}")
            continue
        fact_id = fact.get("fact_id")
        label = fact_id if _is_text(fact_id) else f"index:{index}"
        eligible = fact.get("eligible_for_assessment")
        if not isinstance(eligible, bool):
            blocked = True
            _append(missing, f"context.financial_fact_eligibility_unknown:{label}")
            continue
        if not eligible:
            continue
        reviewed_at = _try_timestamp(fact.get("reviewed_at"))
        if reviewed_at is None or reviewed_at > cutoff:
            # An "eligible" fact without a timely human review is a contradiction.
            blocked = True
            _append(missing, f"context.financial_fact_eligible_without_review:{label}")
            continue
        reviewed += 1

    if reviewed == 0:
        blocked = True
        _append(missing, "reviewed_financial_fact")
    if data_status == "available" and not facts:
        blocked = True
        _append(missing, "context.available_without_financial_facts")
    if data_status == "unavailable" and facts:
        blocked = True
        _append(missing, "context.unavailable_with_financial_facts")

    declared = context.get("missing_evidence") or []
    if not isinstance(declared, (list, tuple)):
        blocked = True
        _append(missing, "context.missing_evidence_malformed")
    else:
        for item in declared:
            if _is_text(item):
                blocked = True
                _append(missing, item)
            elif isinstance(item, dict) and _is_text(item.get("reason")):
                blocked = True
                evidence_type = str(item.get("evidence_type") or "unknown")
                _append(missing, f"{evidence_type}: {item['reason']}")
            else:
                blocked = True
                _append(missing, "context.missing_evidence_malformed")

    warnings = context.get("provenance_warnings") or []
    if isinstance(warnings, (list, tuple)):
        for warning in warnings:
            # Preserve Worker A's structured warnings and do not silently erase
            # their source/quality explanations.
            if _is_text(warning):
                _append(flags, f"provenance_warning:{warning}")
            elif isinstance(warning, dict) and _is_text(warning.get("code")):
                _append(flags, f"provenance_warning:{warning['code']}: {warning.get('message', '')}")
            else:
                _append(flags, "provenance_warning_malformed")
    return blocked


def _inspect_hypotheses(
    hypothesis_results: list[dict],
    entity_id: str,
    cutoff: datetime,
    supporting: list[dict],
    contradicting: list[dict],
    missing: list[str],
) -> bool:
    """Validate hypothesis results and classify them; return True when blocked."""
    if not isinstance(hypothesis_results, (list, tuple)):
        raise DecisionReportError("hypothesis_results must be a sequence of mappings")
    blocked = False
    if not hypothesis_results:
        blocked = True
        _append(missing, "hypothesis_results")
    for index, item in enumerate(hypothesis_results):
        if not isinstance(item, dict):
            blocked = True
            _append(missing, f"hypothesis_result_malformed:{index}")
            continue
        hypothesis_id = item.get("hypothesis_id")
        label = hypothesis_id if _is_text(hypothesis_id) else f"index:{index}"
        if not _is_text(hypothesis_id):
            blocked = True
            _append(missing, f"hypothesis_id_missing:{label}")

        related_entity = item.get("entity_id")
        if _is_text(related_entity) and related_entity != entity_id:
            blocked = True
            _append(missing, f"hypothesis_entity_mismatch:{label}")

        status = item.get("status")
        if not _is_text(status) or status not in HYPOTHESIS_STATUSES:
            blocked = True
            _append(missing, f"hypothesis_status_unknown:{label}:{status!r}")
            continue

        reasons = _as_list(item.get("reasons"))
        result_as_of = _try_timestamp(item.get("as_of"))
        if result_as_of is None:
            blocked = True
            _append(missing, f"hypothesis_as_of_invalid:{label}")
        elif result_as_of > cutoff:
            blocked = True
            _append(missing, f"hypothesis_as_of_after_context:{label}")

        if status == "not_evaluable":
            blocked = True
            _append(missing, f"hypothesis_not_evaluable:{label}")
            for reason in reasons:
                _append(missing, f"hypothesis_reason:{label}:{reason}")
            continue

        if reasons:
            # `hypotheses.py` only returns supported/contradicted/inconclusive
            # with an empty reason list. Non-empty reasons mean the record still
            # carries unresolved quality gates, so it must not count as evidence.
            blocked = True
            _append(missing, f"hypothesis_result_inconsistent:{label}")
            continue
        computed = item.get("computed_value")
        if status in {"supported", "contradicted"} and not _is_text(computed):
            blocked = True
            _append(missing, f"hypothesis_result_inconsistent:{label}")
            continue

        factor = _factor(item)
        if status == "supported":
            supporting.append(factor)
        elif status == "contradicted":
            contradicting.append(factor)
    return blocked


def _inspect_valuation(
    valuation_result: dict | None, entity_id: str, cutoff: datetime, missing: list[str]
) -> bool:
    """Require an evaluated valuation; return True when blocked."""
    if valuation_result is None:
        _append(missing, "valuation_missing")
        return True
    if not isinstance(valuation_result, dict):
        _append(missing, "valuation_malformed")
        return True

    blocked = False
    status = valuation_result.get("status")
    if not _is_text(status) or status not in VALUATION_STATUSES:
        _append(missing, f"valuation_status_unknown:{status!r}")
        return True

    related_entity = valuation_result.get("entity_id")
    if _is_text(related_entity) and related_entity != entity_id:
        blocked = True
        _append(missing, "valuation_entity_mismatch")

    result_as_of = valuation_result.get("as_of")
    if result_as_of is not None:
        parsed = _try_timestamp(result_as_of)
        if parsed is None:
            blocked = True
            _append(missing, "valuation_as_of_invalid")
        elif parsed > cutoff:
            blocked = True
            _append(missing, "valuation_as_of_after_context")

    if status == "not_evaluable":
        blocked = True
        _append(missing, "valuation_not_evaluable")
        for reason in _as_list(valuation_result.get("reasons")):
            _append(missing, f"valuation_reason:{reason}")
    else:
        for reason in _as_list(valuation_result.get("reasons")):
            blocked = True
            _append(missing, f"valuation_result_inconsistent:{reason}")
    return blocked


def _inspect_risk(
    risk_report: dict | None, cutoff: datetime, missing: list[str], flags: list[str]
) -> tuple[list[str], bool]:
    """Return (reliable breaches, risk_report_reliable) without ever trusting a claim."""
    if risk_report is None:
        _append(missing, "risk_report_missing")
        _append(flags, "risk_report_missing")
        return [], False
    if not isinstance(risk_report, dict):
        _append(missing, "risk_report_malformed")
        _append(flags, "risk_report_malformed")
        return [], False

    status = risk_report.get("status")
    if not _is_text(status) or status not in RISK_STATUSES:
        _append(missing, f"risk_status_unknown:{status!r}")
        _append(flags, "risk_status_unknown")
        return [], False
    if status == "not_evaluable":
        _append(missing, "risk_not_evaluable")
        _append(flags, "risk_not_evaluable")
        return [], False

    reliable = True
    if risk_report.get("eligible_for_trading") is True:
        # `risk.py` must never emit an eligible-for-trading report.
        reliable = False
        _append(missing, "risk_eligibility_inconsistent")
        _append(flags, "risk_eligibility_inconsistent")

    report_as_of = risk_report.get("as_of")
    if report_as_of is not None:
        if not _is_text(report_as_of):
            reliable = False
            _append(missing, "risk_as_of_invalid")
        else:
            parsed_date = _date_value(report_as_of)
            if parsed_date is not None:
                if parsed_date > cutoff.date():
                    reliable = False
                    _append(missing, "risk_as_of_after_context")
            else:
                parsed_ts = _try_timestamp(report_as_of)
                if parsed_ts is None:
                    reliable = False
                    _append(missing, "risk_as_of_invalid")
                elif parsed_ts > cutoff:
                    reliable = False
                    _append(missing, "risk_as_of_after_context")

    breaches = risk_report.get("limit_breaches", ())
    if not isinstance(breaches, (list, tuple)):
        _append(missing, "risk_limit_breaches_malformed")
        _append(flags, "risk_limit_breaches_malformed")
        return [], False
    reliable_breaches: list[str] = []
    for breach in breaches:
        if not _is_text(breach):
            reliable = False
            _append(missing, "risk_limit_breaches_malformed")
            _append(flags, "risk_limit_breaches_malformed")
            continue
        reliable_breaches.append(breach)
        _append(flags, f"limit_breach:{breach}")
    return reliable_breaches, reliable


def _factor(item: dict) -> dict:
    """Project an evaluation result into an auditable factor record."""
    sources = item.get("input_source_ids")
    source_ids = [str(source) for source in sources if _is_text(source)] if isinstance(
        sources, (list, tuple)
    ) else []
    return {
        "hypothesis_id": item.get("hypothesis_id"),
        "strategy_id": item.get("strategy_id"),
        "status": item.get("status"),
        "formula": item.get("formula"),
        "computed_value": item.get("computed_value"),
        "target": item.get("target"),
        "target_unit": item.get("target_unit"),
        "direction": item.get("direction"),
        "period_start": item.get("period_start"),
        "period_end": item.get("period_end"),
        "source_ids": source_ids,
    }


def _rationale(
    decision_status: str,
    breaches: list[str],
    missing: list[str],
    supporting: list[dict],
    contradicting: list[dict],
) -> list[str]:
    lines = [
        "decision_status="
        f"{decision_status}; action=no_trade; orders_enabled=false; "
        "eligible_for_trading=false."
    ]
    if decision_status == "risk_blocked":
        lines.append(
            "An assessed hypothetical risk report breached limits: "
            + ", ".join(breaches)
            + "."
        )
        if missing:
            lines.append("Evidence gaps also remain: " + "; ".join(missing) + ".")
    elif decision_status == "insufficient_evidence":
        lines.append("Core evidence is incomplete: " + "; ".join(missing) + ".")
    else:
        lines.append(
            "All core evidence, hypothesis, valuation and risk gates passed; "
            "the report is ready for human review only."
        )
    if supporting:
        lines.append("Supporting evidence: " + ", ".join(_ids(supporting)) + ".")
    if contradicting:
        lines.append("Contradicting evidence: " + ", ".join(_ids(contradicting)) + ".")
    if decision_status == "review_ready":
        lines.append(
            "This research conclusion is not an order and does not enable trading."
        )
    return lines


def _ids(factors: list[dict]) -> list[str]:
    return [str(factor.get("hypothesis_id")) for factor in factors]


def _is_text(value) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _as_list(value) -> list[str]:
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        return [str(item) for item in value if _is_text(item)]
    # An unknown shape is not silently discarded: it becomes a blocking reason.
    return ["malformed reasons field"]


def _append(seq: list[str], value: str) -> None:
    if _is_text(value) and value not in seq:
        seq.append(value)


def _timestamp(value, field: str) -> datetime:
    if not _is_text(value):
        raise DecisionReportError(f"{field} must be a timezone-aware RFC3339 timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise DecisionReportError(
            f"{field} must be a timezone-aware RFC3339 timestamp"
        ) from exc
    if parsed.tzinfo is None:
        raise DecisionReportError(f"{field} must include a timezone")
    return parsed.astimezone(timezone.utc)


def _try_timestamp(value) -> datetime | None:
    if not _is_text(value):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(timezone.utc)


def _date_value(value) -> date | None:
    if not _is_text(value):
        return None
    try:
        return date.fromisoformat(value.strip())
    except ValueError:
        return None
