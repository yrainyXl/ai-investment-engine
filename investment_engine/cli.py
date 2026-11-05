"""CLI: preview one strategy, discover strategies, or batch-preview all active.

Never posts OTW analysis runs or orders.
"""
from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timezone
from pathlib import Path

from .analysis import parse_time
from .otw_client import OTWReadClient
from .pipeline import run_pipeline


def main() -> None:
    parser = argparse.ArgumentParser(description="OTW investment engine v0.1 — read-only decision preview")
    parser.add_argument("--source", choices=["fixture", "otw"], default="fixture")
    parser.add_argument("--strategy-id", default="", help="Select a strategy for a single run (not an env setting)")
    parser.add_argument("--list-strategies", action="store_true", help="GET OTW paper strategy list; no analysis")
    parser.add_argument("--all-active", action="store_true", help="Read-only preview every active OTW paper strategy")
    parser.add_argument("--as-of", help="RFC3339 timestamp with timezone; default now in UTC")
    parser.add_argument("--evidence", default=os.getenv("ENGINE_EVIDENCE_PATH") or None)
    parser.add_argument("--bars", default=os.getenv("ENGINE_BARS_PATH") or None)
    parser.add_argument("--out", default="output/latest-preview.json")
    args = parser.parse_args()

    if args.list_strategies and args.all_active:
        parser.error("--list-strategies and --all-active cannot be used together")
    if args.source != "otw" and (args.list_strategies or args.all_active):
        parser.error("--list-strategies and --all-active require --source otw")
    if args.all_active and args.strategy_id:
        parser.error("Use either --all-active or --strategy-id, not both")

    when = parse_time(args.as_of) if args.as_of else datetime.now(timezone.utc)
    if when is None or (args.as_of and ("Z" not in args.as_of and "+" not in args.as_of[10:] and "-" not in args.as_of[10:])):
        parser.error("--as-of must be valid RFC3339 with an explicit timezone")

    client = None
    if args.source == "otw":
        client = OTWReadClient(os.getenv("OTW_BASE_URL", ""), os.getenv("OTW_API_TOKEN", ""), transport=os.getenv("OTW_TRANSPORT", "rest"))
    if args.list_strategies:
        entries = client.list_strategies()
        public_entries = [
            {field: s.get(field) for field in ("id", "name", "status", "portfolio_id", "ai_provider", "currency")}
            for s in entries
        ]
        print(json.dumps({"strategies": public_entries, "count": len(public_entries)}, ensure_ascii=False, indent=2))
        return

    if args.all_active:
        if args.evidence or args.bars:
            parser.error("--all-active cannot reuse one evidence/bars file across different strategies")
        entries = client.active_strategies()
        if not entries:
            parser.error("No active OTW strategies returned; refusing an empty batch")
        reports = [
            run_pipeline(source="otw", as_of=when, strategy_id=s["id"], client=client,
                         evidence_path=args.evidence, bars_path=args.bars)
            for s in entries
        ]
        output = {"source": "otw", "mode": "batch_read_only", "count": len(reports), "reports": reports}
        print(f"Read-only batch: {len(reports)} active paper strategies; no orders or runs were written.")
    else:
        report = run_pipeline(source=args.source, as_of=when, strategy_id=args.strategy_id, client=client,
                              evidence_path=args.evidence, bars_path=args.bars)
        output = report
        print(f"Source: {report['source']} | Strategy: {report['strategy_id']}")
        print(f"Hypothesis: {report['research']['hypothesis']} | Quant: {report['quant']['status']}")
        print(f"Proposal: {report['proposal']} | Final decision: {report['decision']}")
        print(f"Execution: {report['execution']['mode']} (NO ORDERS)")

    target = Path(args.out)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Report: {target}")


if __name__ == "__main__":
    main()
