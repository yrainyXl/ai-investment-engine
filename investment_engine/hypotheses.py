"""Deterministic, point-in-time investment hypothesis evaluation.

No LLM, network, OTW, broker or order dependencies are permitted here.
Numeric inputs are ineligible unless units and statistical scope are verified;
human review is required by default.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation, localcontext
from typing import Literal


Operation = Literal["sum", "difference", "product", "ratio", "growth", "margin", "weighted_sum"]
Direction = Literal["above", "below", "equal"]
Status = Literal["supported", "contradicted", "inconclusive", "not_evaluable"]


class HypothesisError(ValueError):
    """Invalid hypothesis definition or malformed numeric configuration."""


@dataclass(frozen=True)
class HypothesisVariable:
    variable_id: str
    metric_id: str
    value: str
    unit: str
    period_type: str
    period_start: str
    period_end: str
    source_id: str
    published_at: str
    effective_at: str
    retrieved_at: str
    reviewed_by_human: bool = False
    reviewed_at: str | None = None
    unit_verified: bool = False
    scope_verified: bool = False
    input_kind: Literal["observed", "scenario_assumption"] = "observed"

    def validate(self) -> None:
        required = (
            self.variable_id, self.metric_id, self.unit, self.period_type,
            self.source_id, self.published_at, self.effective_at, self.retrieved_at,
        )
        if any(not str(item).strip() for item in required):
            raise HypothesisError("variable identity, unit, period type and provenance are required")
        if self.input_kind not in {"observed", "scenario_assumption"}:
            raise HypothesisError("unsupported input_kind")
        _decimal(self.value, f"{self.variable_id}.value")
        _date(self.period_start, f"{self.variable_id}.period_start")
        _date(self.period_end, f"{self.variable_id}.period_end")
        if self.period_end < self.period_start:
            raise HypothesisError(f"{self.variable_id}: period_end precedes period_start")
        for name in ("published_at", "effective_at", "retrieved_at"):
            _timestamp(getattr(self, name), f"{self.variable_id}.{name}")
        if self.reviewed_at is not None:
            _timestamp(self.reviewed_at, f"{self.variable_id}.reviewed_at")
        if self.reviewed_by_human and self.input_kind == "observed" and not self.reviewed_at:
            raise HypothesisError(f"{self.variable_id}: reviewed_at is required for human-reviewed observations")


@dataclass(frozen=True)
class HypothesisDefinition:
    hypothesis_id: str
    entity_id: str
    strategy_id: str
    claim: str
    operation: Operation
    variables: tuple[str, ...]
    target: str
    target_unit: str
    direction: Direction
    tolerance: str = "0"
    weights: tuple[str, ...] = ()
    require_human_review: bool = True
    max_age_days: int | None = None

    def validate(self) -> None:
        if not all(str(x).strip() for x in (self.hypothesis_id, self.entity_id, self.strategy_id, self.claim)):
            raise HypothesisError("hypothesis, entity, strategy identifiers and claim are required")
        if self.operation not in {"sum", "difference", "product", "ratio", "growth", "margin", "weighted_sum"}:
            raise HypothesisError("unsupported operation")
        if self.direction not in {"above", "below", "equal"}:
            raise HypothesisError("unsupported direction")
        if not self.variables or len(set(self.variables)) != len(self.variables):
            raise HypothesisError("variables must contain unique variable IDs")
        if not self.target_unit:
            raise HypothesisError("target_unit is required")
        _decimal(self.target, "target")
        tolerance = _decimal(self.tolerance, "tolerance")
        if tolerance < 0:
            raise HypothesisError("tolerance cannot be negative")
        if self.max_age_days is not None and self.max_age_days < 0:
            raise HypothesisError("max_age_days cannot be negative")
        if self.operation == "weighted_sum" and len(self.weights) != len(self.variables):
            raise HypothesisError("weighted_sum requires one weight per variable")
        if self.operation != "weighted_sum" and self.weights:
            raise HypothesisError("weights are only valid for weighted_sum")
        for weight in self.weights:
            _decimal(weight, "weight")


@dataclass(frozen=True)
class EvaluationResult:
    hypothesis_id: str
    entity_id: str
    strategy_id: str
    status: Status
    operation: str
    formula: str
    computed_value: str | None
    target: str
    target_unit: str
    direction: str
    tolerance: str
    result_unit: str | None
    input_values: dict[str, str]
    input_source_ids: tuple[str, ...]
    input_kinds: dict[str, str]
    period_start: str | None
    period_end: str | None
    reasons: tuple[str, ...]
    as_of: str

    def to_dict(self) -> dict:
        return asdict(self)


def evaluate_hypothesis(
    definition: HypothesisDefinition,
    inputs: dict[str, HypothesisVariable],
    *,
    as_of: str,
) -> EvaluationResult:
    """Evaluate a hypothesis without executing arbitrary expressions or code."""
    definition.validate()
    cutoff = _timestamp(as_of, "as_of")
    if cutoff > datetime.now(timezone.utc):
        raise HypothesisError("as_of cannot be in the future")

    missing = [key for key in definition.variables if key not in inputs]
    selected = [inputs[key] for key in definition.variables if key in inputs]
    reasons: list[str] = [f"missing required variable: {key}" for key in missing]

    for key, item in zip((x for x in definition.variables if x in inputs), selected):
        item.validate()
        if item.variable_id != key:
            reasons.append(f"{key}: variable_id does not match input key")
        if not item.unit_verified:
            reasons.append(f"{key}: unit is not verified")
        if not item.scope_verified:
            reasons.append(f"{key}: statistical scope is not verified")
        if definition.require_human_review and item.input_kind == "observed":
            if not item.reviewed_by_human:
                reasons.append(f"{key}: human review is pending")
            elif not item.reviewed_at:
                reasons.append(f"{key}: human review timestamp is missing")
            elif _timestamp(item.reviewed_at, f"{key}.reviewed_at") > cutoff:
                reasons.append(f"{key}: human review occurred after as_of")
        for field in ("published_at", "effective_at", "retrieved_at"):
            if _timestamp(getattr(item, field), f"{key}.{field}") > cutoff:
                reasons.append(f"{key}: {field} is after as_of")
        if definition.max_age_days is not None:
            age = (cutoff.date() - _timestamp(item.effective_at, f"{key}.effective_at").date()).days
            if age > definition.max_age_days:
                reasons.append(f"{key}: observation is stale ({age} days > {definition.max_age_days})")

    if selected:
        reasons.extend(_period_reasons(definition.operation, selected))
    if reasons:
        return _result(definition, "not_evaluable", None, None, selected, tuple(dict.fromkeys(reasons)), as_of)

    values = [_decimal(item.value, item.variable_id) for item in selected]
    units = [item.unit for item in selected]
    operation = definition.operation
    formula = ""
    result_unit: str

    with localcontext() as context:
        context.prec = 28
        if operation in {"sum", "difference", "weighted_sum"}:
            if len(set(units)) != 1:
                return _result(definition, "not_evaluable", None, None, selected,
                               ("additive operations require identical units",), as_of)
            result_unit = units[0]
            if operation == "sum":
                formula = " + ".join(definition.variables)
                value = sum(values, Decimal(0))
            elif operation == "difference":
                if len(values) != 2:
                    return _result(definition, "not_evaluable", None, None, selected,
                                   ("difference requires exactly two variables",), as_of)
                formula = f"{definition.variables[0]} - {definition.variables[1]}"
                value = values[0] - values[1]
            else:
                formula = " + ".join(f"{weight}*{name}" for name, weight in zip(definition.variables, definition.weights))
                value = sum((v * _decimal(w, "weight") for v, w in zip(values, definition.weights)), Decimal(0))
        elif operation in {"ratio", "margin", "growth"}:
            if len(values) != 2:
                return _result(definition, "not_evaluable", None, None, selected,
                               (f"{operation} requires exactly two variables",), as_of)
            if units[0] != units[1]:
                return _result(definition, "not_evaluable", None, None, selected,
                               (f"{operation} inputs require identical units",), as_of)
            if values[1] == 0:
                return _result(definition, "not_evaluable", None, None, selected,
                               ("denominator is zero",), as_of)
            result_unit = "ratio"
            formula = f"{definition.variables[0]} / {definition.variables[1]}"
            value = values[0] / values[1]
            if operation == "growth":
                value -= Decimal(1)
                formula = f"({definition.variables[0]} / {definition.variables[1]}) - 1"
        elif operation == "product":
            if len(values) != 2:
                return _result(definition, "not_evaluable", None, None, selected,
                               ("product requires exactly two variables",), as_of)
            result_unit = f"{units[0]}*{units[1]}"
            formula = f"{definition.variables[0]} * {definition.variables[1]}"
            value = values[0] * values[1]
        else:  # guarded by definition.validate()
            raise HypothesisError("unsupported operation")

    if result_unit != definition.target_unit:
        return _result(definition, "not_evaluable", None, result_unit, selected,
                       (f"target_unit mismatch: expected {result_unit}, got {definition.target_unit}",), as_of,
                       formula=formula)

    target = _decimal(definition.target, "target")
    tolerance = _decimal(definition.tolerance, "tolerance")
    if definition.direction == "above":
        if value >= target + tolerance:
            status: Status = "supported"
        elif value < target - tolerance:
            status = "contradicted"
        else:
            status = "inconclusive"
    elif definition.direction == "below":
        if value <= target - tolerance:
            status = "supported"
        elif value > target + tolerance:
            status = "contradicted"
        else:
            status = "inconclusive"
    else:
        status = "supported" if abs(value - target) <= tolerance else "contradicted"

    return _result(definition, status, format(value, "f"), result_unit, selected, (), as_of, formula=formula)


def _period_reasons(operation: str, selected: list[HypothesisVariable]) -> list[str]:
    if operation == "growth":
        if len(selected) != 2:
            return ["growth requires exactly two variables"]
        current, prior = selected
        if current.unit != prior.unit or current.period_type != prior.period_type:
            return ["growth inputs must have the same unit and period type"]
        current_days = (_date(current.period_end, "period_end") - _date(current.period_start, "period_start")).days
        prior_days = (_date(prior.period_end, "period_end") - _date(prior.period_start, "period_start")).days
        if abs(current_days - prior_days) > 1:
            return ["growth inputs must cover periods of equal duration"]
        if current.period_start == prior.period_start and current.period_end == prior.period_end:
            return ["growth requires distinct current and comparison periods"]
        return []
    periods = {(x.period_type, x.period_start, x.period_end) for x in selected}
    if len(periods) > 1:
        return ["input periods do not align"]
    return []


def _result(
    definition: HypothesisDefinition,
    status: Status,
    value: str | None,
    result_unit: str | None,
    selected: list[HypothesisVariable],
    reasons: tuple[str, ...],
    as_of: str,
    *,
    formula: str = "",
) -> EvaluationResult:
    periods = {(x.period_start, x.period_end) for x in selected}
    period_start, period_end = next(iter(periods)) if len(periods) == 1 else (None, None)
    return EvaluationResult(
        hypothesis_id=definition.hypothesis_id, entity_id=definition.entity_id,
        strategy_id=definition.strategy_id, status=status, operation=definition.operation,
        formula=formula, computed_value=value, target=definition.target,
        target_unit=definition.target_unit, direction=definition.direction,
        tolerance=definition.tolerance, result_unit=result_unit,
        input_values={x.variable_id: x.value for x in selected},
        input_source_ids=tuple(x.source_id for x in selected),
        input_kinds={x.variable_id: x.input_kind for x in selected},
        period_start=period_start, period_end=period_end, reasons=reasons, as_of=as_of,
    )


def _decimal(value: str, field: str) -> Decimal:
    try:
        result = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise HypothesisError(f"{field} must be a decimal string") from exc
    if not result.is_finite():
        raise HypothesisError(f"{field} must be finite")
    return result


def _date(value: str, field: str) -> date:
    try:
        return date.fromisoformat(value)
    except (TypeError, ValueError) as exc:
        raise HypothesisError(f"{field} must be YYYY-MM-DD") from exc


def _timestamp(value: str, field: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError) as exc:
        raise HypothesisError(f"{field} must be RFC3339") from exc
    if parsed.tzinfo is None:
        raise HypothesisError(f"{field} must include timezone")
    return parsed.astimezone(timezone.utc)
