"""Market-data sync, inventory and historical backtest CLI."""
from __future__ import annotations

import argparse
import json
import os
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from .analysis import parse_time
from .backtest import run_backtest
from .evidence import EvidenceStore
from .providers import AkShareRubberInventoryProvider, CNInfoDisclosureProvider, ProviderError
from .market_data import MarketDataError, MarketDataService, MarketDataStore, completed_daily_bars
from .otw_client import OTWReadClient
from .research import build_research_snapshot

SH = ZoneInfo("Asia/Shanghai")


def main() -> None:
    parser = argparse.ArgumentParser(description="AI Investment Engine market data and research tools")
    sub = parser.add_subparsers(dest="command", required=True)

    sync = sub.add_parser("sync", help="Fetch daily bars from AKShare and persist locally")
    sync.add_argument("--asset-type", choices=["stock", "etf", "future"], default="stock")
    sync.add_argument("--symbol", required=True, help="A-share/ETF code or futures series such as RU0")
    sync.add_argument("--start-date", default=os.getenv("MARKET_DATA_DEFAULT_START_DATE", "20180101"))
    sync.add_argument("--end-date", default=datetime.now(SH).date().isoformat())
    sync.add_argument("--adjustment", choices=["none", "qfq", "hfq", "continuous"],
                      default=os.getenv("MARKET_DATA_DEFAULT_ADJUSTMENT", "qfq"))

    listing = sub.add_parser("list", help="List locally cached market-data series")

    backtest = sub.add_parser("backtest", help="Run a research-only backtest on cached daily bars")
    backtest.add_argument("--asset-type", choices=["stock", "etf", "future"], default="stock")
    backtest.add_argument("--symbol", required=True)
    backtest.add_argument("--adjustment", choices=["none", "qfq", "hfq", "continuous"], default="qfq")
    backtest.add_argument("--strategy", choices=["sma_trend", "buy_and_hold"], default="sma_trend")
    backtest.add_argument("--fast-period", type=int, default=20)
    backtest.add_argument("--slow-period", type=int, default=60)
    backtest.add_argument("--fee-bps", type=float, default=10.0)
    backtest.add_argument("--slippage-bps", type=float, default=5.0)
    backtest.add_argument("--out", default="output/backtest.json")

    research = sub.add_parser("research", help="Build an evidence-separated snapshot from OTW + cached data")
    research.add_argument("--strategy-id", default="")
    research.add_argument("--symbol")
    research.add_argument("--futures-symbol", default="RU0")
    research.add_argument("--evidence")
    research.add_argument("--as-of", default=datetime.now(SH).isoformat())
    research.add_argument("--out", default="output/research.json")

    industry = sub.add_parser("sync-industry", help="Sync SHFE rubber delivery-warehouse inventory observations")
    industry.add_argument("--as-of", default=datetime.now(SH).isoformat())

    disclosures = sub.add_parser("sync-disclosures", help="Index official CNINFO disclosures; PDFs are not parsed")
    disclosures.add_argument("--symbol", default="601118")
    disclosures.add_argument("--start-date", default="2020-01-01")
    disclosures.add_argument("--end-date", default=datetime.now(SH).date().isoformat())
    disclosures.add_argument("--max-pages", type=int, default=5)

    args = parser.parse_args()
    store = MarketDataStore(os.getenv("ENGINE_DATA_DIR", "data"))

    if args.command == "sync":
        result = MarketDataService(store=store).sync(
            symbol=args.symbol, asset_type=args.asset_type, start_date=args.start_date,
            end_date=args.end_date, adjustment=args.adjustment,
        )
    elif args.command == "list":
        result = {"series": store.list_series()}
    elif args.command == "sync-industry":
        as_of = parse_time(args.as_of)
        if as_of is None or as_of.tzinfo is None or as_of > datetime.now().astimezone():
            parser.error("--as-of must be a valid non-future RFC3339 timestamp with timezone")
        observations = AkShareRubberInventoryProvider().fetch(as_of=as_of)
        saved = EvidenceStore(os.getenv("ENGINE_DATA_DIR", "data")).save_observations(observations)
        result = {
            **saved,
            "series_id": "99qh_natural_rubber_inventory_scope_unverified",
            "source": "akshare.99qh.futures_inventory_99",
            "row_count": len(observations),
            "first_effective_at": observations[0].effective_at if observations else None,
            "last_effective_at": observations[-1].effective_at if observations else None,
            "unit_status": "unverified",
            "publication_timestamp_status": "unavailable_from_source_schema",
            "assessment_status": "not_eligible_pending_human_review_and_scope_verification",
        }
    elif args.command == "sync-disclosures":
        documents = CNInfoDisclosureProvider().fetch(
            symbol=args.symbol, start_date=args.start_date, end_date=args.end_date,
            max_pages=args.max_pages,
        )
        saved = EvidenceStore(os.getenv("ENGINE_DATA_DIR", "data")).save_documents(documents)
        result = {
            **saved, "symbol": args.symbol, "source": "cninfo.official_disclosure_index",
            "row_count": len(documents),
            "first_published_at": documents[0]["published_at"] if documents else None,
            "last_published_at": documents[-1]["published_at"] if documents else None,
            "content_status": "metadata_only_pdf_not_parsed",
        }
    elif args.command == "backtest":
        adjustment = "continuous" if args.asset_type == "future" else args.adjustment
        as_of = datetime.now(SH)
        bars = completed_daily_bars(store.load(args.symbol, args.asset_type, adjustment), as_of=as_of)
        result = run_backtest(
            bars, strategy=args.strategy, fast_period=args.fast_period, slow_period=args.slow_period,
            fee_bps=args.fee_bps, slippage_bps=args.slippage_bps, as_of=as_of.date(),
        )
        result["as_of"] = as_of.isoformat()
        result["market_data_quality"] = store.read_quarantine(args.symbol, args.asset_type, adjustment)
        target = Path(args.out)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(result, ensure_ascii=False, indent=2))
        print(f"Report: {target}")
        return
    else:
        as_of = parse_time(args.as_of)
        if as_of is None or as_of > datetime.now().astimezone():
            parser.error("--as-of must be a valid non-future RFC3339 timestamp with timezone")
        client = OTWReadClient(
            os.getenv("OTW_BASE_URL", ""), os.getenv("OTW_API_TOKEN", ""),
            transport=os.getenv("OTW_TRANSPORT", "mcp"),
        )
        strategy_id = client.resolve_strategy_id(args.strategy_id or None)
        context = client.strategy_context(strategy_id)
        holdings = context.get("account", {}).get("holdings", [])
        symbol = args.symbol
        if not symbol:
            symbols = sorted({str(item.get("symbol")) for item in holdings if item.get("symbol")})
            if len(symbols) != 1:
                parser.error("Specify --symbol when the OTW strategy has zero or multiple holdings")
            symbol = symbols[0]
        matched = next((item for item in holdings if str(item.get("symbol")) == symbol), None)
        thesis = client.asset_thesis(strategy_id, matched["id"]) if matched and matched.get("id") else None
        thesis_document = thesis.get("document", {}) if thesis else {}
        thesis_content = thesis_document.get("content", {})
        strategy = context.get("strategy", {})
        account = context.get("account", {})
        account_summary = {
            "currency": account.get("currency"), "cash": account.get("cash"), "equity": account.get("equity"),
            "holdings": [{"symbol": item.get("symbol"), "quantity": item.get("quantity")} for item in holdings],
        }
        stock_bars = completed_daily_bars(store.load(symbol, "stock", "qfq"), as_of=as_of)
        futures_bars = completed_daily_bars(store.load(args.futures_symbol, "future", "continuous"), as_of=as_of)
        evidence = json.loads(Path(args.evidence).read_text(encoding="utf-8")) if args.evidence else None
        result = build_research_snapshot(
            strategy_id=strategy_id, symbol=symbol, thesis_content=thesis_content,
            stock_bars=stock_bars, futures_symbol=args.futures_symbol,
            futures_bars=futures_bars, evidence=evidence,
            strategy_name=strategy.get("name"), thesis_updated_at=thesis_document.get("updated_at"),
            account_summary=account_summary, provider_data_summary=EvidenceStore(os.getenv("ENGINE_DATA_DIR", "data")).summary(as_of=as_of), as_of=as_of,
            market_quality={
                "equity": store.read_quarantine(symbol, "stock", "qfq"),
                "commodity_reference": store.read_quarantine(args.futures_symbol, "future", "continuous"),
            },
        )
    if args.command in ("sync", "research"):
        out_path = getattr(args, "out", None)
        if out_path:
            target = Path(out_path)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            print(f"Report: {target}")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
