"""Persistent local audit reports for research and backtests (never written to OTW)."""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4


class ReportStore:
    def __init__(self, root: str | Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def save(self, kind: str, subject: str, payload: dict[str, Any]) -> dict[str, str]:
        if not re.fullmatch(r"[a-z][a-z0-9_-]{0,30}", kind):
            raise ValueError("Invalid report kind")
        if not re.fullmatch(r"[A-Za-z0-9._-]{1,80}", subject):
            raise ValueError("Invalid report subject")
        report_id = uuid4().hex
        created_at = datetime.now(timezone.utc).isoformat()
        envelope = {
            "report_id": report_id,
            "kind": kind,
            "subject": subject,
            "created_at": created_at,
            "report": payload,
        }
        directory = self.root / "reports" / kind
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"{report_id}.json"
        temporary = path.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(envelope, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        temporary.replace(path)
        return {"report_id": report_id, "created_at": created_at, "path": str(path.relative_to(self.root))}

    def get(self, report_id: str) -> dict[str, Any] | None:
        if not re.fullmatch(r"[0-9a-f]{32}", report_id):
            raise ValueError("Invalid report id")
        base = self.root / "reports"
        matches = list(base.glob(f"*/{report_id}.json"))
        if not matches:
            return None
        try:
            payload = json.loads(matches[0].read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        return payload if isinstance(payload, dict) else None

    def list(self, kind: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
        if kind is not None and not re.fullmatch(r"[a-z][a-z0-9_-]{0,30}", kind):
            raise ValueError("Invalid report kind")
        if limit < 1 or limit > 200:
            raise ValueError("limit must be between 1 and 200")
        base = self.root / "reports"
        files = sorted((base / kind).glob("*.json") if kind else base.glob("*/*.json"),
                       key=lambda item: item.stat().st_mtime, reverse=True)
        result: list[dict[str, Any]] = []
        for path in files[:limit]:
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                result.append({
                    "report_id": data.get("report_id"), "kind": data.get("kind"),
                    "subject": data.get("subject"), "created_at": data.get("created_at"),
                    "path": str(path.relative_to(self.root)),
                })
            except (OSError, ValueError):
                continue
        return result
