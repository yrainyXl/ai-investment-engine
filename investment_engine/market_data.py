"""Market data ingestion, normalization, quality checks and local CSV persistence.

AKShare is optional and imported lazily. No source data is silently fabricated.
"""
from __future__ import annotations

import csv
import math
import os
import re
from dataclasses import asdict, dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Iterable


class MarketDataError(RuntimeError):
    pass


@dataclass(frozen=True)
class Bar:
    symbol: str
    exchange: str
    asset_type: str
    interval: str
    timestamp: str
    open: float
    high: float
    low: float
    close: float
    volume: float
    amount: float | None
    source: str
    fetched_at: str
    adjustment: str
    currency: str = "CNY"
    volume_unit: str = "lots"


FIELDS = list(Bar.__dataclass_fields__.keys())
ASSET_TYPES = {"stock", "etf", "future"}
ADJUSTMENTS = {"none", "qfq", "hfq", "continuous"}


def _finite_number(value: Any, field: str, *, optional: bool = False) -> float | None:
    if value is None or (isinstance(value, str) and not value.strip()):
        if optional:
            return None
        raise MarketDataError(f"Missing {field}")
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        if optional:
            return None
        raise MarketDataError(f"Invalid {field}") from exc
    if not math.isfinite(number):
        if optional:
            return None
        raise MarketDataError(f"Non-finite {field}")
    return number


def _iso_date(value: Any) -> str:
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    return date.fromisoformat(str(value)[:10]).isoformat()


def validate_bars(bars: Iterable[Bar], *, as_of: date | None = None) -> dict[str, Any]:
    rows = list(bars)
    errors: list[str] = []
    warnings: list[str] = []
    seen: set[str] = set()
    previous: date | None = None
    for index, bar in enumerate(rows):
        try:
            day = date.fromisoformat(bar.timestamp[:10])
        except ValueError:
            errors.append(f"row {index}: invalid timestamp")
            continue
        if bar.timestamp in seen:
            errors.append(f"row {index}: duplicate timestamp {bar.timestamp}")
        seen.add(bar.timestamp)
        if previous and day <= previous:
            errors.append(f"row {index}: timestamps are not strictly increasing")
        if previous and (day - previous).days > 4:
            warnings.append(f"calendar gap {previous.isoformat()} to {day.isoformat()}; verify exchange holiday/calendar")
        previous = day
        if as_of and day > as_of:
            errors.append(f"row {index}: future bar {day.isoformat()} after as_of")
        values = (bar.open, bar.high, bar.low, bar.close, bar.volume)
        if not all(math.isfinite(v) for v in values):
            errors.append(f"row {index}: non-finite OHLCV")
            continue
        if min(bar.open, bar.high, bar.low, bar.close) <= 0:
            errors.append(f"row {index}: non-positive price")
        if bar.volume < 0 or (bar.amount is not None and (not math.isfinite(bar.amount) or bar.amount < 0)):
            errors.append(f"row {index}: negative or invalid volume/amount")
        if bar.high < max(bar.open, bar.close, bar.low) or bar.low > min(bar.open, bar.close, bar.high):
            errors.append(f"row {index}: inconsistent OHLC relationship")
    return {
        "valid": not errors, "bar_count": len(rows),
        "first_bar": rows[0].timestamp if rows else None,
        "last_bar": rows[-1].timestamp if rows else None,
        "errors": errors[:50], "warnings": warnings[:50], "warning_count": len(warnings),
    }


class MarketDataStore:
    def __init__(self, root: str | Path | None = None):
        self.root = Path(root or os.getenv("ENGINE_DATA_DIR", "data")).expanduser()
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, asset_type: str, symbol: str, adjustment: str) -> Path:
        if asset_type not in ASSET_TYPES or adjustment not in ADJUSTMENTS:
            raise MarketDataError("Unsupported asset_type or adjustment")
        if not re.fullmatch(r"[A-Za-z0-9._-]{1,24}", symbol):
            raise MarketDataError("Invalid symbol")
        return self.root / f"{asset_type}_{symbol}_{adjustment}.csv"

    def save(self, bars: Iterable[Bar], *, as_of: date | None = None) -> Path:
        rows = list(bars)
        if not rows:
            raise MarketDataError("Provider returned no bars")
        first = rows[0]
        path = self._path(first.asset_type, first.symbol, first.adjustment)
        merged: dict[str, Bar] = {}
        if path.exists():
            for old in self.load(first.symbol, first.asset_type, first.adjustment):
                merged[old.timestamp] = old
        for row in rows:
            if row.symbol != first.symbol or row.asset_type != first.asset_type or row.adjustment != first.adjustment:
                raise MarketDataError("Cannot mix symbols, asset types or adjustments in one series")
            merged[row.timestamp] = row
        ordered = [merged[key] for key in sorted(merged)]
        quality = validate_bars(ordered, as_of=as_of)
        if not quality["valid"]:
            raise MarketDataError("Refusing to persist invalid series: " + "; ".join(quality["errors"][:3]))
        tmp = path.with_suffix(".csv.tmp")
        with tmp.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=FIELDS)
            writer.writeheader()
            writer.writerows(asdict(row) for row in ordered)
        tmp.replace(path)
        return path

    def load(self, symbol: str, asset_type: str = "stock", adjustment: str = "qfq") -> list[Bar]:
        path = self._path(asset_type, symbol, adjustment)
        if not path.exists():
            return []
        rows: list[Bar] = []
        with path.open("r", encoding="utf-8", newline="") as handle:
            for raw in csv.DictReader(handle):
                amount = _finite_number(raw.get("amount"), "amount", optional=True)
                rows.append(Bar(
                    symbol=raw["symbol"], exchange=raw["exchange"], asset_type=raw["asset_type"],
                    interval=raw["interval"], timestamp=raw["timestamp"],
                    open=float(raw["open"]), high=float(raw["high"]), low=float(raw["low"]),
                    close=float(raw["close"]), volume=float(raw["volume"]), amount=amount,
                    source=raw["source"], fetched_at=raw["fetched_at"], adjustment=raw["adjustment"],
                    currency=raw.get("currency") or "CNY", volume_unit=raw.get("volume_unit") or "lots",
                ))
        return rows

    def list_series(self) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        for path in sorted(self.root.glob("*.csv")):
            try:
                with path.open("r", encoding="utf-8", newline="") as handle:
                    rows = list(csv.DictReader(handle))
                if not rows:
                    continue
                first, last = rows[0], rows[-1]
                items.append({
                    "symbol": first["symbol"], "asset_type": first["asset_type"],
                    "adjustment": first["adjustment"], "interval": first["interval"],
                    "bar_count": len(rows), "first_bar": first["timestamp"], "last_bar": last["timestamp"],
                    "source": first["source"], "path": path.name,
                })
            except (OSError, KeyError):
                continue
        return items


class AKShareProvider:
    """AKShare adapter for mainland A-share, ETF and commodity futures daily bars."""

    def __init__(self):
        try:
            import akshare as ak
        except ImportError as exc:
            raise MarketDataError("AKShare is not installed; install the market-data extra") from exc
        self.ak = ak

    def fetch(self, *, symbol: str, asset_type: str, start_date: str, end_date: str,
              adjustment: str = "qfq") -> list[Bar]:
        if asset_type not in ASSET_TYPES:
            raise MarketDataError(f"Unsupported asset type: {asset_type}")
        if adjustment not in ADJUSTMENTS:
            raise MarketDataError(f"Unsupported adjustment: {adjustment}")
        start = date.fromisoformat(start_date.replace("/", "-")) if "-" in start_date else datetime.strptime(start_date, "%Y%m%d").date()
        end = date.fromisoformat(end_date.replace("/", "-")) if "-" in end_date else datetime.strptime(end_date, "%Y%m%d").date()
        if start > end:
            raise MarketDataError("start_date must not be after end_date")
        fetched_at = datetime.now(timezone.utc).isoformat()
        if asset_type == "stock":
            if adjustment == "continuous":
                raise MarketDataError("continuous adjustment is only valid for futures")
            frame = self.ak.stock_zh_a_hist(
                symbol=symbol, period="daily", start_date=start.strftime("%Y%m%d"),
                end_date=end.strftime("%Y%m%d"), adjust="" if adjustment == "none" else adjustment,
            )
            source, exchange, volume_unit = "akshare.eastmoney.stock_zh_a_hist", _exchange(symbol), "lots"
            mapper = lambda r: (r["日期"], r["开盘"], r["最高"], r["最低"], r["收盘"], r["成交量"], r.get("成交额"))
        elif asset_type == "etf":
            if adjustment == "continuous":
                raise MarketDataError("continuous adjustment is only valid for futures")
            frame = self.ak.fund_etf_hist_em(
                symbol=symbol, period="daily", start_date=start.strftime("%Y%m%d"),
                end_date=end.strftime("%Y%m%d"), adjust="" if adjustment == "none" else adjustment,
            )
            source, exchange, volume_unit = "akshare.eastmoney.fund_etf_hist_em", _exchange(symbol), "provider_units"
            mapper = lambda r: (r["日期"], r["开盘"], r["最高"], r["最低"], r["收盘"], r["成交量"], r.get("成交额"))
        else:
            frame = self.ak.futures_zh_daily_sina(symbol=symbol)
            source, exchange, volume_unit = "akshare.sina.futures_zh_daily_sina", _future_exchange(symbol), "contracts"
            mapper = lambda r: (r["date"], r["open"], r["high"], r["low"], r["close"], r["volume"], None)
        if frame is None or frame.empty:
            raise MarketDataError(f"AKShare returned no rows for {asset_type}:{symbol}")
        bars: list[Bar] = []
        for record in frame.to_dict(orient="records"):
            raw_date, op, hi, lo, close, volume, amount = mapper(record)
            day = _iso_date(raw_date)
            if day < start.isoformat() or day > end.isoformat():
                continue
            bars.append(Bar(
                symbol=symbol, exchange=exchange, asset_type=asset_type, interval="1d",
                timestamp=day, open=float(op), high=float(hi), low=float(lo), close=float(close),
                volume=float(volume), amount=_finite_number(amount, "amount", optional=True),
                source=source, fetched_at=fetched_at,
                adjustment="continuous" if asset_type == "future" else adjustment,
                currency="CNY", volume_unit=volume_unit,
            ))
        bars.sort(key=lambda item: item.timestamp)
        if not bars:
            raise MarketDataError(f"No bars in requested date range for {asset_type}:{symbol}")
        quality = validate_bars(bars, as_of=end)
        if not quality["valid"]:
            raise MarketDataError("AKShare data quality failed: " + "; ".join(quality["errors"][:3]))
        return bars


def _exchange(symbol: str) -> str:
    if symbol.startswith(("6", "5", "9")):
        return "SSE"
    if symbol.startswith(("0", "1", "2", "3")):
        return "SZSE"
    return "UNKNOWN"


def _future_exchange(symbol: str) -> str:
    prefix = symbol.upper()[:2]
    return {"RU": "SHFE", "NR": "INE", "RB": "SHFE", "HC": "SHFE", "TA": "CZCE"}.get(prefix, "CN_FUTURES")


class MarketDataService:
    def __init__(self, store: MarketDataStore | None = None, provider: AKShareProvider | None = None):
        self.store = store or MarketDataStore()
        self.provider = provider

    def sync(self, *, symbol: str, asset_type: str, start_date: str, end_date: str,
             adjustment: str = "qfq") -> dict[str, Any]:
        provider_name = os.getenv("MARKET_DATA_PROVIDER", "akshare").lower()
        if self.provider is None and provider_name != "akshare":
            raise MarketDataError(f"Unsupported MARKET_DATA_PROVIDER: {provider_name}")
        provider = self.provider or AKShareProvider()
        stored_adjustment = "continuous" if asset_type == "future" else adjustment
        cached = self.store.load(symbol, asset_type, stored_adjustment)
        requested_start = _iso_date(start_date)
        effective_start = requested_start
        if cached:
            cached_start = cached[0].timestamp
            if cached_start < requested_start:
                effective_start = cached_start
        try:
            bars = provider.fetch(symbol=symbol, asset_type=asset_type, start_date=effective_start,
                                  end_date=end_date, adjustment=adjustment)
        except MarketDataError:
            raise
        except Exception as exc:
            raise MarketDataError(f"AKShare request failed: {type(exc).__name__}") from exc
        end = date.fromisoformat(end_date.replace("/", "-")) if "-" in end_date else datetime.strptime(end_date, "%Y%m%d").date()
        path = self.store.save(bars)
        all_bars = self.store.load(symbol, asset_type, stored_adjustment)
        quality = validate_bars(all_bars)
        return {
            "symbol": symbol, "asset_type": asset_type, "adjustment": stored_adjustment,
            "source": bars[0].source, "requested_start_date": start_date,
            "effective_refresh_start_date": effective_start, "fetched_count": len(bars), "stored_count": len(all_bars),
            "first_bar": all_bars[0].timestamp if all_bars else None,
            "last_bar": all_bars[-1].timestamp if all_bars else None,
            "quality": quality, "storage_file": path.name,
        }
