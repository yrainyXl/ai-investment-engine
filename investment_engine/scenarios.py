"""Explicit what-if scenarios for deterministic investment hypotheses.

Scenario overrides are labeled as assumptions, never as observed market facts.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from decimal import Decimal, InvalidOperation

from .hypotheses import (
    EvaluationResult,
    HypothesisDefinition,
    HypothesisError,
    HypothesisVariable,
    evaluate_hypothesis,
)


@dataclass(frozen=True)
class ScenarioDefinition:
    scenario_id: str
    name: str
    description: str
    overrides: dict[str, str]

    def validate(self) -> None:
        if not self.scenario_id.strip() or not self.name.strip() or not self.description.strip():
            raise HypothesisError("scenario identity and description are required")
        if not self.overrides:
            raise HypothesisError("scenario must override at least one variable")
        for variable_id, value in self.overrides.items():
            if not variable_id.strip():
                raise HypothesisError("scenario variable IDs cannot be empty")
            try:
                number = Decimal(str(value))
            except (InvalidOperation, TypeError, ValueError) as exc:
                raise HypothesisError(f"scenario override for {variable_id} must be decimal") from exc
            if not number.is_finite():
                raise HypothesisError(f"scenario override for {variable_id} must be finite")


@dataclass(frozen=True)
class ScenarioEvaluation:
    scenario_id: str
    name: str
    description: str
    evaluation: EvaluationResult

    def to_dict(self) -> dict:
        return {
            "scenario_id": self.scenario_id,
            "name": self.name,
            "description": self.description,
            "evaluation": self.evaluation.to_dict(),
        }


@dataclass(frozen=True)
class ScenarioSetResult:
    hypothesis_id: str
    baseline: EvaluationResult
    scenarios: tuple[ScenarioEvaluation, ...]
    as_of: str

    def to_dict(self) -> dict:
        return {
            "hypothesis_id": self.hypothesis_id,
            "baseline": self.baseline.to_dict(),
            "scenarios": [item.to_dict() for item in self.scenarios],
            "as_of": self.as_of,
            "note": "Scenario overrides are assumptions, not observed facts or forecasts.",
        }


def evaluate_scenarios(
    definition: HypothesisDefinition,
    inputs: dict[str, HypothesisVariable],
    scenarios: tuple[ScenarioDefinition, ...],
    *,
    as_of: str,
) -> ScenarioSetResult:
    """Evaluate a baseline and explicit overrides using the same safe evaluator."""
    baseline = evaluate_hypothesis(definition, inputs, as_of=as_of)
    seen: set[str] = set()
    results: list[ScenarioEvaluation] = []
    for scenario in scenarios:
        scenario.validate()
        if scenario.scenario_id in seen:
            raise HypothesisError(f"duplicate scenario_id: {scenario.scenario_id}")
        seen.add(scenario.scenario_id)
        unknown = set(scenario.overrides) - set(definition.variables)
        if unknown:
            raise HypothesisError(f"scenario references unknown variables: {', '.join(sorted(unknown))}")
        adjusted = dict(inputs)
        for variable_id, value in scenario.overrides.items():
            if variable_id not in inputs:
                raise HypothesisError(f"scenario cannot override missing variable: {variable_id}")
            original = inputs[variable_id]
            adjusted[variable_id] = replace(
                original,
                value=str(value),
                source_id=f"scenario:{scenario.scenario_id}:{variable_id}",
                published_at=as_of,
                effective_at=as_of,
                retrieved_at=as_of,
                reviewed_by_human=False,
                input_kind="scenario_assumption",
            )
        evaluation = evaluate_hypothesis(definition, adjusted, as_of=as_of)
        results.append(ScenarioEvaluation(
            scenario_id=scenario.scenario_id,
            name=scenario.name,
            description=scenario.description,
            evaluation=evaluation,
        ))
    return ScenarioSetResult(
        hypothesis_id=definition.hypothesis_id,
        baseline=baseline,
        scenarios=tuple(results),
        as_of=as_of,
    )
