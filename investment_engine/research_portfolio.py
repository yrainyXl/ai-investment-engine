"""Read-only OTW portfolio concentration screening for research reports."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal, DecimalException, localcontext
import json
import os
import re
from typing import Any
from urllib.parse import urlsplit

from .otw_client import OTWError, OTWReadClient
from .research_inputs import _decimal, _timestamp, normalize_research_symbol

# OTW's account wrapper emits PostgreSQL-style timestamps with a space and
# sometimes a single-digit hour. Holdings use standard ISO timestamps.
_OTW_DATETIME = re.compile(
    r"^(\d{4}-\d{2}-\d{2}) (\d{1,2}):(\d{2}:\d{2}(?:\.\d+)?) ([+-]\d{2}:\d{2}(?::\d{2})?)$"
)
_MAX_SNAPSHOT_AGE = timedelta(minutes=5)
_MAX_QUOTE_AGE = timedelta(minutes=15)
_MIN_TOLERANCE = Decimal("0.02")


def _account_time(value: Any) -> datetime | None:
    if isinstance(value, str):
        match = _OTW_DATETIME.fullmatch(value)
        if match:
            day, hour, clock, offset = match.groups()
            value = f"{day}T{int(hour):02d}:{clock}{offset}"
    return _timestamp(value)


def _unknown(cutoff: datetime, reason: str) -> dict[str, Any]:
    return {
        "status": "unknown",
        "source": "otw.paper_trading.context",
        "as_of": cutoff.isoformat(),
        "reasons": [reason],
        "sector_exposure": {"status": "unknown", "reason": "verified_sector_classification_unavailable"},
        "max_drawdown": {"status": "unknown", "reason": "point_in_time_equity_history_unavailable"},
        "orders_enabled": False,
        "eligible_for_trading": False,
    }


def _assessment(
    payload: dict, strategy: dict, symbol: str, asset_class: str, cutoff: datetime,
) -> dict:
    strategy_snapshot = payload.get("strategy")
    portfolio = payload.get("portfolio")
    account = payload.get("account")
    if not all(isinstance(block, dict) for block in (strategy_snapshot, portfolio, account)):
        return _unknown(cutoff, "otw_snapshot_malformed")
    portfolio_id = strategy.get("portfolio_id")
    if (
        not isinstance(portfolio_id, str) or not portfolio_id
        or strategy_snapshot.get("id") != strategy.get("id")
        or strategy_snapshot.get("portfolio_id") != portfolio_id
        or portfolio.get("id") != portfolio_id
        or strategy_snapshot.get("status") != "active"
    ):
        return _unknown(cutoff, "strategy_portfolio_mismatch")
    calculated_at = _account_time(account.get("calculated_at"))
    if calculated_at is None or calculated_at > cutoff:
        return _unknown(cutoff, "account_snapshot_not_available_as_of")
    if cutoff - calculated_at > _MAX_SNAPSHOT_AGE:
        return _unknown(cutoff, "account_snapshot_stale")
    currency = account.get("currency")
    if (
        not isinstance(currency, str) or not re.fullmatch(r"[A-Z]{3}", currency)
        or any(block.get("currency") != currency for block in (strategy_snapshot, portfolio))
    ):
        return _unknown(cutoff, "account_currency_mismatch")
    holdings = account.get("holdings")
    unpriced = account.get("unpriced_holdings")
    if not isinstance(holdings, list) or not isinstance(unpriced, list):
        return _unknown(cutoff, "otw_holdings_malformed")
    if unpriced or account.get("market_value_complete") is not True:
        return _unknown(cutoff, "account_contains_unpriced_holdings")
    cash = _decimal(account.get("cash"))
    equity = _decimal(account.get("equity"))
    market_value = _decimal(account.get("market_value"))
    if (
        cash is None or cash < 0 or equity is None or equity <= 0
        or market_value is None or market_value < 0
    ):
        return _unknown(cutoff, "cash_equity_or_market_value_invalid")
    limit = _decimal(strategy_snapshot.get("max_position_pct"))
    if limit is None or not 0 < limit <= 1:
        return _unknown(cutoff, "strategy_position_limit_unavailable")

    values: dict[tuple[str, str], Decimal] = {}
    quotes: list[datetime] = []
    for holding in holdings:
        if not isinstance(holding, dict) or holding.get("portfolio_id") != portfolio_id:
            return _unknown(cutoff, "holding_portfolio_mismatch")
        quantity = _decimal(holding.get("quantity"))
        if quantity is None or quantity < 0:
            return _unknown(cutoff, "holding_quantity_invalid")
        if quantity == 0:
            continue
        if holding.get("currency") != currency:
            return _unknown(cutoff, "mixed_holding_currencies")
        quote_at = _timestamp(holding.get("last_price_at"))
        if quote_at is None or quote_at > calculated_at or cutoff - quote_at > _MAX_QUOTE_AGE:
            return _unknown(cutoff, "holding_quote_missing_stale_or_after_snapshot")
        holding_symbol = holding.get("symbol")
        holding_class = holding.get("asset_class")
        if not isinstance(holding_symbol, str) or not holding_symbol or not isinstance(holding_class, str):
            return _unknown(cutoff, "holding_identity_missing")
        if holding_class in {"stock", "etf"}:
            try:
                holding_symbol = normalize_research_symbol(holding_symbol)
            except ValueError:
                # OTW is multi-market. Non-mainland instruments keep their OTW identity.
                pass

        price = _decimal(holding.get("price"))
        value = _decimal(holding.get("market_value"))
        if price is None or price <= 0 or value is None or value <= 0:
            return _unknown(cutoff, "holding_market_value_or_price_invalid")
        if abs(value - quantity * price) > _MIN_TOLERANCE:
            return _unknown(cutoff, "position_value_does_not_reconcile_with_quote")
        key = (holding_class, holding_symbol)
        values[key] = values.get(key, Decimal(0)) + value
        quotes.append(quote_at)

    if abs(sum(values.values(), Decimal(0)) - market_value) > _MIN_TOLERANCE:
        return _unknown(cutoff, "holdings_do_not_reconcile_with_account_market_value")
    total = cash + sum(values.values(), Decimal(0))
    if total <= 0 or abs(total - equity) > _MIN_TOLERANCE:
        return _unknown(cutoff, "cash_and_holdings_do_not_reconcile_with_equity")
    weights = {key: value / total for key, value in values.items()}
    maximum = max(weights.values(), default=Decimal(0))
    return {
        **_unknown(cutoff, "verified_sector_or_equity_history_unavailable"),
        "status": "partial",
        "snapshot_at": calculated_at.isoformat(),
        "currency": currency,
        "cash_weight": str(cash / total),
        "risk_asset_weight": str(sum(weights.values(), Decimal(0))),
        "largest_position_weight": str(maximum),
        "asset_weight": str(weights.get((asset_class, symbol), Decimal(0))),
        "holding_weights": [
            {"asset_class": kind, "symbol": held, "weight": str(weight)}
            for (kind, held), weight in sorted(weights.items())
        ],
        "oldest_holdings_quote_at": min(quotes).isoformat() if quotes else None,
        "limit_breaches": ["max_single_weight_exceeded"] if maximum > limit else [],
        "concentration": {
            "status": "breach" if maximum > limit else "within_limit",
            "max_position_weight": str(limit),
        },
    }


def portfolio_risk(*, as_of: datetime | None, entity_id: str, asset_class: str) -> dict[str, Any]:
    """Retrieve an explicitly mapped account through OTW's REST GET endpoints.

    Passing no cutoff selects the current snapshot and records the cutoff after
    the remote read. A supplied cutoff is never advanced to fit later data.
    """
    def cutoff() -> datetime:
        return datetime.now(timezone.utc) if as_of is None else as_of

    if as_of is not None and (as_of.utcoffset() is None or as_of > datetime.now(timezone.utc)):
        raise ValueError("as_of must be timezone-aware and cannot be in the future")

    symbol = normalize_research_symbol(entity_id)
    if asset_class not in {"stock", "etf"}:
        raise ValueError("asset_class must be stock or etf")
    try:
        bindings = json.loads(os.getenv("ENGINE_OTW_RESEARCH_STRATEGIES", "{}"))
    except ValueError:
        return _unknown(cutoff(), "otw_strategy_mapping_invalid")
    if not isinstance(bindings, dict):
        return _unknown(cutoff(), "otw_strategy_mapping_invalid")
    strategy_id = bindings.get(f"{asset_class}:{symbol}")
    if not isinstance(strategy_id, str) or not re.fullmatch(
        r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}", strategy_id,
    ):
        return _unknown(cutoff(), "otw_strategy_not_configured_for_asset")

    # OTW only exposes a newly calculated account snapshot on this route.
    # Avoid reading sensitive current state for clearly historical requests.
    if as_of is not None and datetime.now(timezone.utc) - as_of > _MAX_SNAPSHOT_AGE:
        return _unknown(cutoff(), "historical_account_snapshot_unavailable")

    base_url = os.getenv("ENGINE_OTW_RESEARCH_BASE_URL", "")
    token = os.getenv("ENGINE_OTW_RESEARCH_TOKEN", "")
    if not base_url or not token:
        return _unknown(cutoff(), "otw_rest_credentials_unavailable")
    try:
        parts = urlsplit(base_url)
        if parts.username is not None or parts.password is not None or parts.query or parts.fragment:
            return _unknown(cutoff(), "otw_rest_base_url_invalid")
        client = OTWReadClient(
            base_url, token, timeout=5, transport="rest", follow_redirects=False,
        )
        strategy = next((item for item in client.active_strategies() if item["id"] == strategy_id), None)
        if strategy is None:
            return _unknown(cutoff(), "configured_otw_strategy_not_active_or_visible")
        payload = client.strategy_context(strategy_id)
        with localcontext() as context:
            context.prec = 28
            return _assessment(payload, strategy, symbol, asset_class, cutoff())
    except (OTWError, DecimalException, OSError, ValueError):
        return _unknown(cutoff(), "otw_get_unavailable_or_invalid")
