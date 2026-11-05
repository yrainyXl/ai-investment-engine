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
    parser.add_argument("--strategy-id", default=os.getenv("OTW_STRATEGY_ID", ""))
    parser.add_argument("--as-of", help="RFC3339 timestamp with timezone; default now in UTC")
    parser.add_argument("--evidence", default=os.getenv("ENGINE_EVIDENCE_PATH") or None)
    parser.add_argument("--bars", default=os.getenv("ENGINE_BARS_PATH") or None)
    parser.add_argument("--out", default="output/latest-preview.json")
    args = parser.parse_args()
    when = parse_time(args.as_of) if args.as_of else datetime.now(timezone.utc)
    if when is None or (args.as_of and ("Z" not in args.as_of and "+" not in args.as_of[10:] and "-" not in args.as_of[10:])):
        parser.error("--as-of must be valid RFC3339 with an explicit timezone")
    client = None
    if args.source == "otw":
        client = OTWReadClient(os.getenv("OTW_BASE_URL", ""), os.getenv("OTW_API_TOKEN", ""))
    report = run_pipeline(source=args.source, as_of=when, strategy_id=args.strategy_id, client=client,
                          evidence_path=args.evidence, bars_path=args.bars)
    target = Path(args.out)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Source: {report['source']} | Strategy: {report['strategy_id']}")
    print(f"Hypothesis: {report['research']['hypothesis']} | Quant: {report['quant']['status']}")
    print(f"Proposal: {report['proposal']} | Final decision: {report['decision']}")
    print(f"Execution: {report['execution']['mode']} (NO ORDERS) | Report: {target}")


if __name__ == "__main__":
    main()
