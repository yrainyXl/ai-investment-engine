"""Deterministic, point-in-time investment hypothesis evaluation.

This module deliberately has no LLM, network, OTW or order dependencies.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from decimal import Decimal, InvalidOperation, localcontext
from typing import Literal


class HypothesisError(ValueError):
    pass


@dataclass(frozen=True)
class HypothesisVariable:
    variable_id: str
    value: str
    unit: str
    period_start: str
    period_end: str
    source_id: str
    published_at: str
    effective_at: str
    retrieved_at: str
    reviewed_by_human: bool
    unit_verified: bool
    scope_verified: bool = True


@dataclass(frozen=True)
class HypothesisDefinition:
    hypothesis_id: str
    entity_id: str
    strategy_id: str
    claim: str
    operation: Literal["add", "subtract", "multiply", "divide", "ratio", "growth", "margin", "delta", "weighted_sum"]
    variables: tuple[str, ...]
    target: str
    direction: Literal["above", "below", "equal"]
    tolerance: str = "0"
    weights: tuple[str, ...] = ()
    required_review: bool = True


@dataclass(frozen=True)
class EvaluationResult:
    hypothesis_id: str
    entity_id: str
    status: Literal["supported", "contradicted", "inconclusive", "not_evaluable"]
    computed_value: str | None
    target: str
    direction: str
    unit: str | None
    input_source_ids: tuple[str, ...]
    reasons: tuple[str, ...]
    as_of: str


def _decimal(value: str, field: str) -> Decimal:
    try:
        result = Decimal(value)
    except (InvalidOperation, TypeError) as exc:
        raise HypothesisError(f"{field} must be a decimal string") from exc
    if not result.is_finite():
        raise HypothesisError(f"{field} must be finite")
    return result


def evaluate_hypothesis(
    definition: HypothesisDefinition,
    inputs: dict[str, HypothesisVariable],
    *,
    as_of: str,
) -> EvaluationResult:
    """Evaluate only fully reviewed, unit/scope-verified, point-in-time inputs."""
    reasons: list[str] = []
    try:
        cutoff = _decimal_timestamp(as_of)
    except ValueError as exc:
        reasons.append(str(exc))
        cutoff = None

    if not definition.hypothesis_id or not definition.entity_id or not definition.strategy_id:
        reasons.append("hypothesis, entity and strategy identifiers are required")
    if len(definition.variables) < 1 or any(name not in inputs for name in definition.variables):
        reasons.append("one or more required variables are missing")

    selected = [inputs[name] for name in definition.variables if name in inputs]
    if definition.required_review:
        for name, item in zip((n for n in definition.variables if n in inputs), selected):
            if not item.reviewed_by_human:
                reasons.append(f"{name}: human review pending")
            if not item.unit_verified:
                reasons.append(f"{name}: unit not verified")
            if not item.scope_verified:
                reasons.append(f"{name}: statistical/statement scope not verified")
            for label, timestamp in (("published_at", item.published_at), ("effective_at", item.effective_at), ("retrieved_at", item.retrieved_at)):
                try:
                    parsed = _decimal_timestamp(timestamp)
                    if cutoff is not None and parsed > cutoff:
                        reasons.append(f"{name}: {label} is after as_of")
                except ValueError as exc:
                    reasons.append(f"{name}: {label} invalid ({exc})")
    if selected:
        periods = {(x.period_start, x.period_end) for x in selected}
        if len(periods) != 1:
            reasons.append("input periods do not align")
    if reasons:
        return EvaluationResult(definition.hypothesis_id, definition.entity_id, "not_evaluable", None,
                                definition.target, definition.direction, None,
                                tuple(x.source_id for x in selected), tuple(dict.fromkeys(reasons)), as_of)

    values = [_decimal(x.value, x.variable_id) for x in selected]
    units = [x.unit for x in selected]
    operation = definition.operation
    result_unit: str | None = units[0] if units else None
    with localcontext() as ctx:
        ctx.prec = 28
        if operation in {"add", "subtract", "delta", "weighted_sum"}:
            if len(set(units)) != 1:
                return _not_evaluable(definition, selected, as_of, "additive operations require identical units")
            if operation == "add":
                value = sum(values, Decimal(0))
            elif operation == "subtract" or operation == "delta":
                if len(values) != 2:
                    return _not_evaluable(definition, selected, as_of, "subtract/delta requires exactly two variables")
                value = values[0] - values[1]
            else:
                if len(definition.weights) != len(values):
                    return _not_evaluable(definition, selected, as_of, "weighted_sum requires one weight per variable")
                weights = [_decimal(x, "weight") for x in definition.weights]
                value = sum((v * w for v, w in zip(values, weights)), Decimal(0))
        elif operation in {"divide", "ratio", "growth", "margin"}:
            if len(values) != 2:
                return _not_evaluable(definition, selected, as_of, f"{operation} requires exactly two variables")
            if values[1] == 0:
                return _not_evaluable(definition, selected, as_of, "division by zero")
            if operation in {"divide", "ratio"} and units[0] != units[1]:
                return _not_evaluable(definition, selected, as_of, "ratio inputs must have identical units")
            value = values[0] / values[1]
            result_unit = "ratio"
            if operation == "growth":
                value -= Decimal(1)
        elif operation == "multiply":
            if len(values) != 2:
                return _not_evaluable(definition, selected, as_of, "multiply requires exactly two variables")
            value = values[0] * values[1]
            result_unit = f"{units[0]}*{units[1]}"
        else:
            return _not_evaluable(definition, selected, as_of, "unsupported operation")

    target = _decimal(definition.target, "target")
    tolerance = _decimal(definition.tolerance, "tolerance")
    if tolerance < 0:
        return _not_evaluable(definition, selected, as_of, "tolerance cannot be negative")
    if definition.direction == "above":
        status = "supported" if value >= target - tolerance else "contradicted"
    elif definition.direction == "below":
        status = "supported" if value <= target + tolerance else "contradicted"
    else:
        status = "supported" if abs(value - target) <= tolerance else "contradicted"
    return EvaluationResult(definition.hypothesis_id, definition.entity_id, status, format(value, "f"),
                            definition.target, definition.direction, result_unit,
                            tuple(x.source_id for x in selected), (), as_of)


def _not_evaluable(definition, selected, as_of, reason):
    return EvaluationResult(definition.hypothesis_id, definition.entity_id, "not_evaluable", None,
                            definition.target, definition.direction, None,
                            tuple(x.source_id for x in selected), (reason,), as_of)


def _decimal_timestamp(value: str) -> Decimal:
    from datetime import datetime
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError) as exc:
        raise ValueError("timestamp must be RFC3339") from exc
    if dt.tzinfo is None:
        raise ValueError("timestamp must include timezone")
    return Decimal(str(dt.timestamp()))
