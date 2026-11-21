"""Load trusted model configuration and run the existing evidence-gated agents."""

from __future__ import annotations

import os
import re
from decimal import Decimal, InvalidOperation
from typing import Any, Mapping, Sequence
from urllib.parse import urlsplit, urlunsplit

from .agent_runtime import (
    PROMPT_TEMPLATE_VERSION, ROLE_ORDER, AgentRuntimeConfig, RolePolicy, run_research_agents,
)
from .model_client import OpenAICompatibleClient, chat_completions_url

_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}\Z")


def _required(env: Mapping[str, str], name: str) -> str:
    value = env.get(f"ENGINE_LLM_{name}", "").strip()
    if not value:
        raise ValueError("incomplete model configuration")
    return value


def _identifier(value: str) -> str:
    if not _IDENTIFIER.fullmatch(value):
        raise ValueError("invalid model configuration identifier")
    return value


def _positive_decimal(value: str) -> Decimal:
    result = Decimal(value)
    if not result.is_finite() or not Decimal(0) < result <= Decimal("1000000"):
        raise ValueError("invalid model budget or price")
    return result


def _bounded_int(value: str, maximum: int) -> int:
    result = int(value)
    if not 0 < result <= maximum:
        raise ValueError("model configuration value outside limits")
    return result


def _settings(
    env: Mapping[str, str],
) -> tuple[str, AgentRuntimeConfig, OpenAICompatibleClient]:
    provider = _identifier(_required(env, "PROVIDER"))
    endpoint = chat_completions_url(_required(env, "BASE_URL"))
    api_key = _required(env, "API_KEY")
    if any(character.isspace() or not character.isascii() for character in api_key):
        raise ValueError("invalid model credential")
    budget = _positive_decimal(_required(env, "MAX_COST_USD"))
    roles = []
    for role in ROLE_ORDER:
        prefix = role.upper()
        model = env.get(f"ENGINE_LLM_{prefix}_MODEL") or _required(env, "MODEL")
        version = env.get(f"ENGINE_LLM_{prefix}_MODEL_VERSION")
        input_price = (
            env.get(f"ENGINE_LLM_{prefix}_INPUT_COST_PER_MILLION")
            or _required(env, "INPUT_COST_PER_MILLION")
        )
        output_price = (
            env.get(f"ENGINE_LLM_{prefix}_OUTPUT_COST_PER_MILLION")
            or _required(env, "OUTPUT_COST_PER_MILLION")
        )
        roles.append(RolePolicy(
            role=role,
            model=_identifier(model),
            model_version=_identifier(version) if version else None,
            prompt_version=PROMPT_TEMPLATE_VERSION,
            max_input_tokens=_bounded_int(env.get(f"ENGINE_LLM_{prefix}_MAX_INPUT_TOKENS",
                                                    env.get("ENGINE_LLM_MAX_INPUT_TOKENS", "24000")), 120_000),
            max_output_tokens=_bounded_int(env.get(f"ENGINE_LLM_{prefix}_MAX_OUTPUT_TOKENS",
                                                     env.get("ENGINE_LLM_MAX_OUTPUT_TOKENS", "1200")), 8_000),
            timeout_seconds=_bounded_int(env.get(f"ENGINE_LLM_{prefix}_TIMEOUT_SECONDS",
                                                  env.get("ENGINE_LLM_TIMEOUT_SECONDS", "20")), 60),
            input_cost_per_million=_positive_decimal(input_price),
            output_cost_per_million=_positive_decimal(output_price),
        ))
    config = AgentRuntimeConfig(
        roles=tuple(roles),
        max_total_calls=len(roles),
        max_total_input_tokens=sum(role.max_input_tokens for role in roles),
        max_total_output_tokens=sum(role.max_output_tokens for role in roles),
        max_total_cost=budget,
    )
    return provider, config, OpenAICompatibleClient(
        endpoint=endpoint,
        api_key=api_key,
        policies=config.roles_by_role,
        max_cost=budget,
        max_input_tokens=config.max_total_input_tokens,
        max_output_tokens=config.max_total_output_tokens,
    )


_CONTEXT_FIELDS = ("schema_version", "entity_id", "asset_class", "as_of", "data_status")
_RECORD_FIELDS = (
    "source_id", "fact_id", "source_document_id", "observation_id", "symbol",
    "series_id", "metric", "value", "unit", "currency", "period_type", "period_start",
    "period_end", "statement_scope", "effective_at", "published_at", "retrieved_at",
    "reviewed_at", "source_name", "assessment", "evidence_type", "reviewed_by_human",
    "unit_verified", "scope_verified", "eligible_for_assessment",
)


def _public_record(record: Mapping[str, Any]) -> dict[str, Any]:
    projected = {
        key: record[key]
        for key in _RECORD_FIELDS
        if isinstance(record.get(key), (str, int, float, bool))
    }
    source = record.get("source_url")
    if isinstance(source, str):
        try:
            parsed = urlsplit(source)
            if parsed.scheme == "https" and parsed.hostname and parsed.username is None:
                projected["source_url"] = urlunsplit(("https", parsed.netloc, parsed.path, "", ""))
        except ValueError:
            pass
    return projected


def _public_context(context: Mapping[str, Any]) -> dict[str, Any]:
    # Model input is restricted to market research fields from server-owned
    # stores. No account objects, analyst notes, reviewer names or URL queries.
    projected = {key: context.get(key) for key in _CONTEXT_FIELDS}
    for family in ("financial_facts", "industry_observations"):
        projected[family] = [
            _public_record(record) for record in context.get(family, []) if isinstance(record, Mapping)
        ]
    projected["missing_evidence"] = [
        {"evidence_type": gap["evidence_type"]}
        for gap in context.get("missing_evidence", [])
        if isinstance(gap, Mapping) and isinstance(gap.get("evidence_type"), str)
    ]
    projected["provenance_warnings"] = [
        {"code": item["code"]}
        for item in context.get("provenance_warnings", [])
        if isinstance(item, Mapping) and isinstance(item.get("code"), str)
    ]
    return projected


def execute_agents(
    context: Mapping[str, Any],
    *,
    hypotheses: Sequence[Mapping[str, Any]] = (),
) -> dict[str, Any]:
    """Execute a single research run using only the service's environment."""

    provider = None
    config = None
    client = None
    if os.getenv("ENGINE_LLM_ENABLED") == "true":
        try:
            provider, config, client = _settings(os.environ)
            availability = "configured"
        except (ValueError, InvalidOperation):
            availability = "invalid_or_incomplete_configuration"
    else:
        availability = "disabled"

    result = run_research_agents(
        context=_public_context(context), hypothesis_results=hypotheses, client=client, config=config,
    )
    result["model_audit"]["gateway"] = {
        "availability": availability,
        "provider": provider,
        "reserved_cost_usd": str(client.reserved_cost) if client else None,
        "preflight_budget_rejections": client.skipped_for_budget if client else 0,
    }
    return result
