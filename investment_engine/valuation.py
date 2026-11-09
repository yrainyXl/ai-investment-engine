"""Deterministic valuation arithmetic.

Outputs are model calculations, not price targets or trading instructions.
All monetary inputs must use the same currency and reviewed statement scope.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from decimal import Decimal, InvalidOperation, localcontext
from datetime import datetime, timezone
from typing import Literal


class ValuationError(ValueError):
    pass


@dataclass(frozen=True)
class ValuationMetric:
    metric_id: str
    value: str
    unit: str
    period_end: str
    source_id: str
    published_at: str
    effective_at: str
    retrieved_at: str
    reviewed_by_human: bool = False
    reviewed_at: str | None = None
    unit_verified: bool = False
    scope_verified: bool = False


@dataclass(frozen=True)
class MultipleValuationResult:
    status: Literal["calculated", "not_evaluable"]
    metric_id: str
    multiple_name: str
    metric_value: str
    multiple: str
    implied_value: str | None
    unit: str
    source_ids: tuple[str, ...]
    reasons: tuple[str, ...]
    as_of: str

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class DCFInput:
    entity_id: str
    currency: str
    forecast_fcf: tuple[str, ...]
    discount_rate: str
    terminal_growth: str
    net_debt: str
    shares_outstanding: str
    net_debt_source_id: str
    shares_source_id: str
    net_debt_published_at: str
    net_debt_effective_at: str
    net_debt_retrieved_at: str
    shares_published_at: str
    shares_effective_at: str
    shares_retrieved_at: str
    assumptions_as_of: str
    net_debt_reviewed_at: str | None = None
    shares_reviewed_at: str | None = None
    assumptions_reviewed_at: str | None = None
    assumptions_reviewed_by_human: bool = False
    source_facts_reviewed_by_human: bool = False
    unit_verified: bool = False
    scope_verified: bool = False


@dataclass(frozen=True)
class DCFResult:
    status: Literal["calculated", "not_evaluable"]
    entity_id: str
    currency: str
    enterprise_value: str | None
    equity_value: str | None
    value_per_share: str | None
    present_value_forecast: str | None
    terminal_value: str | None
    discount_rate: str
    terminal_growth: str
    forecast_fcf: tuple[str, ...]
    source_ids: tuple[str, ...]
    reasons: tuple[str, ...]
    as_of: str

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class SensitivityCell:
    discount_rate: str
    terminal_growth: str
    value_per_share: str | None
    status: Literal["calculated", "not_evaluable"]
    reason: str | None = None


def calculate_multiple_valuation(
    metric: ValuationMetric,
    *,
    multiple_name: str,
    multiple: str,
    as_of: str,
    require_human_review: bool = True,
) -> MultipleValuationResult:
    cutoff = _timestamp(as_of, "as_of")
    value = _decimal(metric.value, "metric.value")
    factor = _decimal(multiple, "multiple")
    if not multiple_name.strip() or not metric.metric_id or not metric.source_id:
        raise ValuationError("multiple name, metric identity and source are required")
    try:
        metric_period_end = datetime.fromisoformat(metric.period_end).date()
    except (TypeError, ValueError) as exc:
        raise ValuationError("metric.period_end must be YYYY-MM-DD") from exc
    if metric_period_end > cutoff.date():
        raise ValuationError("metric period_end is after as_of")
    if not metric.unit.endswith("/share"):
        raise ValuationError("multiple valuation requires a per-share metric unit")
    if factor <= 0:
        raise ValuationError("multiple must be positive")
    reasons = _metric_quality_reasons(metric, cutoff, require_human_review=require_human_review)
    if value <= 0:
        reasons.append("per-share metric must be positive for a conventional positive multiple valuation")
    if reasons:
        return MultipleValuationResult(
            "not_evaluable", metric.metric_id, multiple_name, metric.value, multiple,
            None, metric.unit, (metric.source_id,), tuple(reasons), as_of,
        )
    with localcontext() as context:
        context.prec = 28
        implied = value * factor
    return MultipleValuationResult(
        "calculated", metric.metric_id, multiple_name, metric.value, multiple,
        format(implied, "f"), metric.unit, (metric.source_id,), (), as_of,
    )


def calculate_dcf(
    model: DCFInput,
    *,
    as_of: str,
) -> DCFResult:
    cutoff = _timestamp(as_of, "as_of")
    if not model.entity_id.strip() or not model.currency.strip():
        raise ValuationError("entity_id and currency are required")
    if not model.forecast_fcf:
        raise ValuationError("at least one annual FCF forecast is required")
    if not model.net_debt_source_id or not model.shares_source_id:
        raise ValuationError("net debt and shares outstanding source IDs are required")
    cash_flows = [_decimal(x, "forecast_fcf") for x in model.forecast_fcf]
    discount_rate = _decimal(model.discount_rate, "discount_rate")
    terminal_growth = _decimal(model.terminal_growth, "terminal_growth")
    net_debt = _decimal(model.net_debt, "net_debt")
    shares = _decimal(model.shares_outstanding, "shares_outstanding")
    if shares <= 0:
        raise ValuationError("shares_outstanding must be positive")
    if discount_rate <= 0 or discount_rate >= 1:
        raise ValuationError("discount_rate must be between 0 and 1")
    if terminal_growth <= -1 or terminal_growth >= discount_rate:
        raise ValuationError("terminal_growth must be greater than -1 and below discount_rate")

    reasons = list(temporal_reasons)
    if not model.assumptions_reviewed_by_human:
        reasons.append("forecast, discount-rate and terminal-growth assumptions are not human reviewed")
    if not model.source_facts_reviewed_by_human:
        reasons.append("net debt and share-count source facts are not human reviewed")
    if not model.unit_verified:
        reasons.append("currency and share units are not verified")
    if not model.scope_verified:
        reasons.append("statement scope and share-count basis are not verified")
    if reasons:
        return DCFResult(
            "not_evaluable", model.entity_id, model.currency, None, None, None, None, None,
            model.discount_rate, model.terminal_growth, model.forecast_fcf,
            (model.net_debt_source_id, model.shares_source_id), tuple(reasons), as_of,
        )

    with localcontext() as context:
        context.prec = 28
        present_value = sum(
            (cash_flow / ((Decimal(1) + discount_rate) ** year)
             for year, cash_flow in enumerate(cash_flows, start=1)),
            Decimal(0),
        )
        terminal_value = cash_flows[-1] * (Decimal(1) + terminal_growth) / (discount_rate - terminal_growth)
        discounted_terminal = terminal_value / ((Decimal(1) + discount_rate) ** len(cash_flows))
        enterprise_value = present_value + discounted_terminal
        equity_value = enterprise_value - net_debt
        per_share = equity_value / shares
    return DCFResult(
        "calculated", model.entity_id, model.currency, format(enterprise_value, "f"),
        format(equity_value, "f"), format(per_share, "f"), format(present_value, "f"),
        format(terminal_value, "f"), model.discount_rate, model.terminal_growth,
        model.forecast_fcf, (model.net_debt_source_id, model.shares_source_id), (), as_of,
    )


def dcf_sensitivity(
    model: DCFInput,
    *,
    as_of: str,
    discount_rates: tuple[str, ...],
    terminal_growth_rates: tuple[str, ...],
) -> tuple[SensitivityCell, ...]:
    """Return a WACC x terminal-growth grid; invalid pairs are explicitly skipped."""
    cells: list[SensitivityCell] = []
    for wacc_text in discount_rates:
        for growth_text in terminal_growth_rates:
            wacc = _decimal(wacc_text, "discount_rate")
            growth = _decimal(growth_text, "terminal_growth")
            if wacc <= 0 or wacc >= 1 or growth <= -1 or growth >= wacc:
                cells.append(SensitivityCell(
                    wacc_text, growth_text, None, "not_evaluable",
                    "terminal growth must be below a positive discount rate below 1",
                ))
                continue
            candidate = DCFInput(
                entity_id=model.entity_id, currency=model.currency,
                forecast_fcf=model.forecast_fcf, discount_rate=wacc_text,
                terminal_growth=growth_text, net_debt=model.net_debt,
                shares_outstanding=model.shares_outstanding,
                net_debt_source_id=model.net_debt_source_id,
                shares_source_id=model.shares_source_id,
                net_debt_published_at=model.net_debt_published_at,
                net_debt_effective_at=model.net_debt_effective_at,
                net_debt_retrieved_at=model.net_debt_retrieved_at,
                shares_published_at=model.shares_published_at,
                shares_effective_at=model.shares_effective_at,
                shares_retrieved_at=model.shares_retrieved_at,
                assumptions_as_of=model.assumptions_as_of,
                net_debt_reviewed_at=model.net_debt_reviewed_at,
                shares_reviewed_at=model.shares_reviewed_at,
                assumptions_reviewed_at=model.assumptions_reviewed_at,
                assumptions_reviewed_by_human=model.assumptions_reviewed_by_human,
                source_facts_reviewed_by_human=model.source_facts_reviewed_by_human,
                unit_verified=model.unit_verified, scope_verified=model.scope_verified,
            )
            result = calculate_dcf(candidate, as_of=as_of)
            cells.append(SensitivityCell(
                wacc_text, growth_text, result.value_per_share, result.status,
                "; ".join(result.reasons) if result.reasons else None,
            ))
    return tuple(cells)


def _metric_quality_reasons(
    metric: ValuationMetric,
    as_of: datetime,
    *,
    require_human_review: bool,
) -> list[str]:
    reasons: list[str] = []
    if require_human_review and not metric.reviewed_by_human:
        reasons.append("source metric is not human reviewed")
    elif require_human_review and not metric.reviewed_at:
        reasons.append("source metric review timestamp is missing")
    elif require_human_review and _timestamp(metric.reviewed_at, "metric.reviewed_at") > as_of:
        reasons.append("source metric was reviewed after as_of")
    if not metric.unit_verified:
        reasons.append("source metric unit is not verified")
    if not metric.scope_verified:
        reasons.append("source metric scope is not verified")
    for field in ("published_at", "effective_at", "retrieved_at"):
        if _timestamp(getattr(metric, field), field) > as_of:
            reasons.append(f"{field} is after as_of")
    return reasons


def _decimal(value: str, field: str) -> Decimal:
    try:
        number = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValuationError(f"{field} must be a decimal string") from exc
    if not number.is_finite():
        raise ValuationError(f"{field} must be finite")
    return number


def _timestamp(value: str, field: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError) as exc:
        raise ValuationError(f"{field} must be RFC3339") from exc
    if parsed.tzinfo is None:
        raise ValuationError(f"{field} must include timezone")
    parsed = parsed.astimezone(timezone.utc)
    if parsed > datetime.now(timezone.utc):
        raise ValuationError(f"{field} cannot be in the future")
    return parsed
