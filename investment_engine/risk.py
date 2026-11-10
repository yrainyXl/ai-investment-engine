"""Independent, deterministic *hypothetical* portfolio risk evaluation.

No broker, OTW, network, market-data or order dependencies. All positions and
shocks are caller-supplied scenario assumptions, never verified live holdings.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date
from decimal import Decimal, InvalidOperation, localcontext
from typing import Literal


class RiskError(ValueError):
    pass


def _dec(value: str, field: str) -> Decimal:
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise RiskError(f"{field} must be a decimal string") from exc
    if not result.is_finite():
        raise RiskError(f"{field} must be finite")
    return result


def _date(value: str, field: str) -> date:
    try:
        return date.fromisoformat(value)
    except (ValueError, TypeError) as exc:
        raise RiskError(f"{field} must be YYYY-MM-DD") from exc


@dataclass(frozen=True)
class HypotheticalPosition:
    symbol: str
    asset_class: str
    sector_id: str
    market_value: str
    currency: str = "CNY"

    def validate(self, currency: str) -> Decimal:
        if not self.symbol or not self.asset_class or not self.sector_id:
            raise RiskError("position symbol, asset_class and sector_id are required")
        if self.currency != currency:
            raise RiskError("mixed currencies require a separately verified FX conversion")
        value = _dec(self.market_value, f"{self.symbol}.market_value")
        if value < 0:
            raise RiskError("short positions are not supported by long-only risk v1")
        return value


@dataclass(frozen=True)
class EquityObservation:
    observed_at: str
    equity: str


@dataclass(frozen=True)
class RiskLimits:
    max_single_weight: str = "0.20"
    max_sector_weight: str = "0.40"
    max_total_risky_weight: str = "1.00"
    max_drawdown: str = "0.20"

    def validate(self) -> None:
        for field in ("max_single_weight", "max_sector_weight",
                      "max_total_risky_weight", "max_drawdown"):
            value = _dec(getattr(self, field), field)
            if not (Decimal(0) < value <= Decimal(1)):
                raise RiskError(f"{field} must be in (0,1]")


@dataclass(frozen=True)
class StressShock:
    scenario_id: str
    name: str
    shocks_by_symbol: dict[str, str]

    def validate(self, symbols: set[str]) -> None:
        if not self.scenario_id or not self.name or not self.shocks_by_symbol:
            raise RiskError("scenario_id, name and shocks are required")
        unknown = set(self.shocks_by_symbol) - symbols
        if unknown:
            raise RiskError("unknown scenario symbols: " + ", ".join(sorted(unknown)))
        for symbol, shock in self.shocks_by_symbol.items():
            value = _dec(shock, f"{symbol}.shock")
            if value < Decimal(-1) or value > Decimal(10):
                raise RiskError("scenario price shocks must be between -100% and +1000%")


@dataclass(frozen=True)
class RiskReport:
    status: Literal["assessed", "not_evaluable"]
    portfolio_id: str
    as_of: str
    currency: str
    portfolio_value: str
    cash_weight: str
    largest_position_weight: str | None
    largest_sector_weight: str | None
    risky_weight: str
    max_drawdown: str | None
    limit_breaches: tuple[str, ...]
    stress_results: tuple[dict, ...]
    assumptions: tuple[str, ...]
    eligible_for_trading: bool = False

    def to_dict(self) -> dict:
        return asdict(self)


def assess_hypothetical_risk(
    portfolio_id: str,
    positions: tuple[HypotheticalPosition, ...],
    *,
    cash: str,
    currency: str,
    as_of: str,
    limits: RiskLimits,
    history: tuple[EquityObservation, ...] = (),
    shocks: tuple[StressShock, ...] = (),
) -> RiskReport:
    """Long-only research risk. Absence of history is reported, not invented."""
    if not portfolio_id.strip() or not currency.strip():
        raise RiskError("portfolio ID and currency are required")
    cutoff = _date(as_of, "as_of")
    limits.validate()
    if not positions:
        raise RiskError("at least one hypothetical position is required")
    if len({p.symbol for p in positions}) != len(positions):
        raise RiskError("duplicate symbols in portfolio")
    values = {p.symbol: p.validate(currency) for p in positions}
    balance = _dec(cash, "cash")
    if balance < 0:
        raise RiskError("negative cash and leverage are not supported")
    total = balance + sum(values.values(), Decimal(0))
    if total <= 0:
        raise RiskError("portfolio total must be positive")

    with localcontext() as ctx:
        ctx.prec = 28
        weights = {symbol: value / total for symbol, value in values.items()}
        sector_values: dict[str, Decimal] = {}
        for p in positions:
            sector_values[p.sector_id] = sector_values.get(p.sector_id, Decimal(0)) + values[p.symbol]
        sector_weights = {sector: value / total for sector, value in sector_values.items()}
        single_max = max(weights.values())
        sector_max = max(sector_weights.values())
        risky_weight = sum(weights.values(), Decimal(0))
        cash_weight = balance / total

        drawdown: Decimal | None = None
        if history:
            if len(history) < 2:
                raise RiskError("equity history must contain at least two observations")
            days = [_date(item.observed_at, "history.observed_at") for item in history]
            if len(days) != len(set(days)) or days != sorted(days):
                raise RiskError("equity history dates must be unique and ordered")
            if days[-1] > cutoff:
                raise RiskError("equity history includes observations after as_of")
            equity_values = [_dec(item.equity, "history.equity") for item in history]
            if any(v <= 0 for v in equity_values):
                raise RiskError("equity history values must be positive")
            peak = equity_values[0]
            drawdown = Decimal(0)
            for value in equity_values:
                peak = max(peak, value)
                drawdown = max(drawdown, Decimal(1) - value / peak)

        breaches: list[str] = []
        if single_max > _dec(limits.max_single_weight, "max_single_weight"):
            breaches.append("max_single_weight_exceeded")
        if sector_max > _dec(limits.max_sector_weight, "max_sector_weight"):
            breaches.append("max_sector_weight_exceeded")
        if risky_weight > _dec(limits.max_total_risky_weight, "max_total_risky_weight"):
            breaches.append("max_total_risky_weight_exceeded")
        if drawdown is not None and drawdown > _dec(limits.max_drawdown, "max_drawdown"):
            breaches.append("max_drawdown_exceeded")

        stress_rows: list[dict] = []
        seen_scenarios: set[str] = set()
        for scenario in shocks:
            scenario.validate(set(values))
            if scenario.scenario_id in seen_scenarios:
                raise RiskError("duplicate scenario_id")
            seen_scenarios.add(scenario.scenario_id)
            shock_value = sum(
                (values[symbol] * _dec(shock, f"{symbol}.shock")
                 for symbol, shock in scenario.shocks_by_symbol.items()),
                Decimal(0),
            )
            scenario_total = total + shock_value
            stress_rows.append({
                "scenario_id": scenario.scenario_id,
                "name": scenario.name,
                "portfolio_loss": format(-shock_value, "f"),
                "portfolio_return": format(shock_value / total, "f"),
                "stressed_value": format(scenario_total, "f"),
                "input_kind": "scenario_assumption",
            })

    return RiskReport(
        status="assessed", portfolio_id=portfolio_id, as_of=as_of, currency=currency,
        portfolio_value=format(total, "f"), cash_weight=format(cash_weight, "f"),
        largest_position_weight=format(single_max, "f"),
        largest_sector_weight=format(sector_max, "f"),
        risky_weight=format(risky_weight, "f"),
        max_drawdown=format(drawdown, "f") if drawdown is not None else None,
        limit_breaches=tuple(breaches), stress_results=tuple(stress_rows),
        assumptions=(
            "All positions are hypothetical; they have not been retrieved or verified from OTW.",
            "All stress shocks are user-defined assumptions, not forecasts.",
            "Historical max drawdown is not available without a verified equity history.",
            "This is a long-only concentration/stress screening, not a complete VaR/liquidity model.",
        ), eligible_for_trading=False,
    )
