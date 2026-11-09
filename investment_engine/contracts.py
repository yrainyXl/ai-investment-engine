"""Provider-neutral contracts for company financials and industry metrics.

These contracts describe data; they do not make investment decisions or approve
facts. Provider adapters (CNINFO, exchange feeds, vendor APIs) map source data
into these stable shapes.
"""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from decimal import Decimal, InvalidOperation
from typing import Any, Literal


class FinancialContractError(ValueError):
    pass


@dataclass(frozen=True)
class CompanyFinancials:
    """One reported financial fact for any listed company or fund issuer."""

    entity_id: str
    entity_type: Literal["equity", "etf", "fund", "issuer"]
    metric: str
    value: str
    currency: str
    scale: str
    period_type: Literal["quarter", "half_year", "year", "year_to_date", "point_in_time"]
    period_start: str
    period_end: str
    statement_scope: Literal["consolidated", "parent", "fund", "unknown"]
    source_id: str
    source_name: str
    source_url: str
    published_at: str
    retrieved_at: str
    unit_basis: str
    entity_name: str | None = None
    source_page: int | None = None
    source_section: str | None = None
    source_label: str | None = None
    source_excerpt: str | None = None
    audit_status: Literal["audited", "unaudited", "reviewed", "unknown"] = "unknown"
    extraction_status: Literal["machine_extracted_pending_human_review", "human_verified"] = "machine_extracted_pending_human_review"

    def validate(self) -> None:
        if not self.entity_id or not self.metric or not self.source_id or not self.source_name:
            raise FinancialContractError("entity, metric and source identity are required")
        if self.entity_type not in {"equity", "etf", "fund", "issuer"}:
            raise FinancialContractError("unsupported entity_type")
        if self.period_type not in {"quarter", "half_year", "year", "year_to_date", "point_in_time"}:
            raise FinancialContractError("unsupported period_type")
        if self.statement_scope not in {"consolidated", "parent", "fund", "unknown"}:
            raise FinancialContractError("unsupported statement_scope")
        if not re.fullmatch(r"20\d{2}-\d{2}-\d{2}", self.period_start):
            raise FinancialContractError("period_start must be YYYY-MM-DD")
        if not re.fullmatch(r"20\d{2}-\d{2}-\d{2}", self.period_end) or self.period_end < self.period_start:
            raise FinancialContractError("period_end must be a valid date after period_start")
        try:
            value = Decimal(self.value)
        except (InvalidOperation, TypeError) as exc:
            raise FinancialContractError("value must be an exact decimal string") from exc
        if not value.is_finite():
            raise FinancialContractError("value must be finite")
        if not self.currency or not self.scale or not self.unit_basis:
            raise FinancialContractError("currency, scale and unit basis are required")
        if not self.source_url.startswith("https://"):
            raise FinancialContractError("source_url must use HTTPS")
        if self.source_page is not None and self.source_page < 1:
            raise FinancialContractError("source_page must be a positive page number")


@dataclass(frozen=True)
class IndustryMetricDefinition:
    """Defines a metric without assuming it applies to every industry."""

    metric_id: str
    industry_id: str
    display_name: str
    unit: str
    description: str
    source_scope: str
    aggregation: Literal["point", "sum", "average", "end_of_period"]
    required_for_decision: bool = False

    def validate(self) -> None:
        for name in ("metric_id", "industry_id", "display_name", "unit", "description", "source_scope"):
            if not getattr(self, name).strip():
                raise FinancialContractError(f"{name} is required")


@dataclass(frozen=True)
class IndustryMetricObservation:
    metric_id: str
    entity_id: str
    value: str
    unit: str
    effective_at: str
    published_at: str | None
    source_id: str
    source_scope: str
    unit_verified: bool = False
    scope_verified: bool = False

    def validate(self) -> None:
        try:
            value = Decimal(self.value)
        except (InvalidOperation, TypeError) as exc:
            raise FinancialContractError("industry metric value must be a decimal string") from exc
        if not value.is_finite() or not self.metric_id or not self.entity_id or not self.source_id:
            raise FinancialContractError("industry metric observation identity/value is invalid")


@dataclass(frozen=True)
class StrategyDefinition:
    """Metadata contract for a reusable strategy implementation."""

    strategy_id: str
    name: str
    version: str
    asset_classes: tuple[str, ...]
    required_inputs: tuple[str, ...]
    parameter_schema: dict[str, Any]
    decision_horizon: str
    risk_model: str
    implementation: str
    enabled: bool = False

    def validate(self) -> None:
        if not all((self.strategy_id, self.name, self.version, self.decision_horizon, self.risk_model, self.implementation)):
            raise FinancialContractError("strategy identity and execution metadata are required")
        if not self.asset_classes or not self.required_inputs:
            raise FinancialContractError("strategy asset classes and required inputs cannot be empty")


class StrategyRegistry:
    """In-memory registry; persistence and execution wiring are separate concerns."""

    def __init__(self, definitions: tuple[StrategyDefinition, ...] = ()):
        self._definitions: dict[str, StrategyDefinition] = {}
        for definition in definitions:
            self.register(definition)

    def register(self, definition: StrategyDefinition) -> None:
        definition.validate()
        if definition.strategy_id in self._definitions:
            raise FinancialContractError(f"duplicate strategy_id: {definition.strategy_id}")
        self._definitions[definition.strategy_id] = definition

    def get(self, strategy_id: str) -> StrategyDefinition:
        try:
            return self._definitions[strategy_id]
        except KeyError as exc:
            raise FinancialContractError(f"unknown strategy_id: {strategy_id}") from exc

    def list(self, *, enabled_only: bool = False) -> list[dict[str, Any]]:
        items = [asdict(item) for item in self._definitions.values()]
        if enabled_only:
            items = [item for item in items if item["enabled"]]
        return sorted(items, key=lambda item: item["strategy_id"])



def company_financial_from_fact(fact: Any) -> CompanyFinancials:
    """Adapt a provider-specific FinancialFact into the shared contract."""
    if hasattr(fact, "validate"):
        fact.validate()
    reviewed = (
        getattr(fact, "reviewed_by_human", False) is True
        and bool(getattr(fact, "reviewed_at", None))
        and getattr(fact, "unit_verified", False) is True
        and getattr(fact, "scope_verified", False) is True
    )
    item = CompanyFinancials(
        entity_id=str(fact.symbol),
        entity_type="equity",
        entity_name=str(getattr(fact, "issuer_name", "") or "") or None,
        metric=str(fact.metric),
        value=str(fact.value),
        currency=str(fact.currency),
        scale=str(fact.value_scale),
        period_type=str(fact.period_type),
        period_start=str(fact.period_start),
        period_end=str(fact.period_end),
        statement_scope=str(fact.statement_scope),
        source_id=str(fact.source_document_id),
        source_name=str(fact.source_name),
        source_url=str(fact.source_url),
        published_at=str(fact.published_at),
        retrieved_at=str(fact.retrieved_at),
        unit_basis=str(fact.unit_basis),
        source_page=int(fact.source_page) if getattr(fact, "source_page", None) else None,
        source_section=getattr(fact, "source_section", None),
        source_label=getattr(fact, "source_label", None),
        source_excerpt=getattr(fact, "source_excerpt", None),
        audit_status=str(getattr(fact, "audit_status", "unknown")),
        extraction_status="human_verified" if reviewed else "machine_extracted_pending_human_review",
    )
    item.validate()
    return item
