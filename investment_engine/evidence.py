"""Point-in-time evidence and source-document storage.

Provider observations are data, not automatically approved thesis evidence.
Human review, publication timing and verified units are required before a
numeric observation can support or contradict a research factor.
"""
from __future__ import annotations

import hashlib
import json
import math
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

EVIDENCE_TYPES = {"confirmed_fact", "market_expectation", "inference"}
ASSESSMENTS = {"supporting", "contradicting", "unassessed"}


class EvidenceError(RuntimeError):
    pass


def _parse_aware(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return parsed if parsed.tzinfo is not None else None


def _iso(value: str | datetime | None) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            raise EvidenceError("timestamps must include a timezone")
        return value.isoformat()
    parsed = _parse_aware(value)
    if parsed is None:
        raise EvidenceError("timestamp must be RFC3339 with timezone")
    return parsed.isoformat()


@dataclass(frozen=True)
class Observation:
    observation_id: str
    series_id: str
    subject: str
    factor: str
    metric: str
    value: float
    unit: str
    effective_at: str
    published_at: str | None
    retrieved_at: str
    source_name: str
    source_url: str
    evidence_type: str = "confirmed_fact"
    assessment: str = "unassessed"
    reviewed_by_human: bool = False
    unit_verified: bool = False
    series_definition: str = ""
    revision_id: str | None = None
    detail: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def create(cls, *, series_id: str, subject: str, factor: str, metric: str,
               value: float, unit: str, effective_at: str | datetime,
               published_at: str | datetime | None, retrieved_at: str | datetime,
               source_name: str, source_url: str, evidence_type: str = "confirmed_fact",
               assessment: str = "unassessed", reviewed_by_human: bool = False,
               unit_verified: bool = False, series_definition: str = "",
               revision_id: str | None = None, detail: str = "",
               metadata: dict[str, Any] | None = None) -> "Observation":
        effective = _iso(effective_at)
        published = _iso(published_at)
        retrieved = _iso(retrieved_at)
        key = "|".join((source_name, series_id, metric, effective or "", revision_id or repr(float(value)), unit))
        observation_id = hashlib.sha256(key.encode("utf-8")).hexdigest()[:32]
        item = cls(
            observation_id=observation_id, series_id=series_id, subject=subject,
            factor=factor, metric=metric, value=float(value), unit=unit,
            effective_at=effective or "", published_at=published, retrieved_at=retrieved or "",
            source_name=source_name, source_url=source_url, evidence_type=evidence_type,
            assessment=assessment, reviewed_by_human=reviewed_by_human,
            unit_verified=unit_verified, series_definition=series_definition,
            revision_id=revision_id, detail=detail, metadata=metadata or {},
        )
        item.validate()
        return item

    def validate(self) -> None:
        if not self.observation_id or not self.series_id or not self.subject or not self.metric:
            raise EvidenceError("observation identity, series, subject and metric are required")
        if self.evidence_type not in EVIDENCE_TYPES:
            raise EvidenceError("unsupported evidence_type")
        if self.assessment not in ASSESSMENTS:
            raise EvidenceError("unsupported assessment")
        if not math.isfinite(float(self.value)):
            raise EvidenceError("observation value must be finite")
        if not self.unit or not self.source_name or not self.series_definition:
            raise EvidenceError("unit, source_name and series_definition are required")
        if not isinstance(self.source_url, str) or not self.source_url.startswith("https://"):
            raise EvidenceError("source_url must use HTTPS")
        if _parse_aware(self.effective_at) is None or _parse_aware(self.retrieved_at) is None:
            raise EvidenceError("effective_at and retrieved_at must include a timezone")
        if self.published_at is not None and _parse_aware(self.published_at) is None:
            raise EvidenceError("published_at must include a timezone when supplied")

    def eligibility(self, *, as_of: datetime) -> dict[str, Any]:
        if as_of.tzinfo is None:
            raise EvidenceError("as_of must include a timezone")
        self.validate()
        published = _parse_aware(self.published_at)
        effective = _parse_aware(self.effective_at)
        retrieved = _parse_aware(self.retrieved_at)
        temporal = bool(published and effective and retrieved
                        and published <= as_of and effective <= as_of and retrieved <= as_of)
        eligible = bool(
            temporal and self.reviewed_by_human and self.unit_verified
            and self.assessment in ("supporting", "contradicting")
            and self.evidence_type in ("confirmed_fact", "market_expectation")
        )
        reasons: list[str] = []
        if not published:
            reasons.append("publication timestamp is unknown")
        elif not temporal:
            reasons.append("record was not available by as_of")
        if not self.reviewed_by_human:
            reasons.append("human review is pending")
        if not self.unit_verified:
            reasons.append("measurement unit or series scope is unverified")
        if self.assessment == "unassessed":
            reasons.append("evidence has not been assessed as supporting or contradicting")
        if self.evidence_type == "inference":
            reasons.append("inferences cannot count as facts or market expectations")
        return {"eligible_for_assessment": eligible, "reasons": reasons}


class EvidenceStore:
    def __init__(self, root: str | Path | None = None):
        self.root = Path(root or "data").expanduser()
        self.directory = self.root / "evidence"
        self.directory.mkdir(parents=True, exist_ok=True)
        self.observations_path = self.directory / "industry_observations.json"
        self.documents_path = self.directory / "source_documents.json"

    @staticmethod
    def _read(path: Path) -> list[dict[str, Any]]:
        if not path.exists():
            return []
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise EvidenceError(f"Could not read {path.name}") from exc
        if not isinstance(value, list) or not all(isinstance(x, dict) for x in value):
            raise EvidenceError(f"Invalid evidence file: {path.name}")
        return value

    @staticmethod
    def _write(path: Path, items: list[dict[str, Any]]) -> None:
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(json.dumps(items, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        temporary.replace(path)

    def save_observations(self, observations: list[Observation]) -> dict[str, Any]:
        existing = self._read(self.observations_path)
        merged = {str(item.get("observation_id")): item for item in existing}
        for item in observations:
            item.validate()
            merged[item.observation_id] = asdict(item)
        ordered = sorted(merged.values(), key=lambda x: (x.get("effective_at") or "", x.get("observation_id") or ""))
        self._write(self.observations_path, ordered)
        return {"saved_count": len(observations), "total_count": len(ordered),
                "storage_file": str(self.observations_path.relative_to(self.root))}

    def list_observations(self, *, as_of: datetime | None = None,
                          series_id: str | None = None, limit: int = 500) -> list[dict[str, Any]]:
        if limit < 1 or limit > 5000:
            raise EvidenceError("limit must be between 1 and 5000")
        output: list[dict[str, Any]] = []
        for raw in self._read(self.observations_path):
            if series_id and raw.get("series_id") != series_id:
                continue
            item = Observation(**raw)
            item.validate()
            value = asdict(item)
            if as_of is not None:
                value.update(item.eligibility(as_of=as_of))
                published = _parse_aware(item.published_at)
                effective = _parse_aware(item.effective_at)
                retrieved = _parse_aware(item.retrieved_at)
                value["visible_as_of"] = bool(
                    published and effective and retrieved
                    and published <= as_of and effective <= as_of and retrieved <= as_of
                )
            output.append(value)
        return output[-limit:]

    def save_documents(self, documents: list[dict[str, Any]]) -> dict[str, Any]:
        existing = self._read(self.documents_path)
        merged = {str(item.get("document_id")): item for item in existing}
        for item in documents:
            required = ("document_id", "symbol", "title", "published_at", "retrieved_at", "source_name", "source_url")
            if any(not item.get(key) for key in required):
                raise EvidenceError("source document is missing required provenance")
            if not str(item["source_url"]).startswith("https://"):
                raise EvidenceError("document source_url must use HTTPS")
            if _parse_aware(str(item["published_at"])) is None:
                raise EvidenceError("document published_at must include a timezone")
            if _parse_aware(str(item["retrieved_at"])) is None:
                raise EvidenceError("document retrieved_at must include a timezone")
            merged[str(item["document_id"])] = dict(item)
        ordered = sorted(merged.values(), key=lambda x: (x.get("published_at") or "", x.get("document_id") or ""))
        self._write(self.documents_path, ordered)
        return {"saved_count": len(documents), "total_count": len(ordered),
                "storage_file": str(self.documents_path.relative_to(self.root))}

    def list_documents(self, *, symbol: str | None = None, as_of: datetime | None = None,
                       limit: int = 200) -> list[dict[str, Any]]:
        if limit < 1 or limit > 2000:
            raise EvidenceError("limit must be between 1 and 2000")
        output = []
        for item in self._read(self.documents_path):
            if symbol and item.get("symbol") != symbol:
                continue
            published = _parse_aware(item.get("published_at"))
            retrieved = _parse_aware(item.get("retrieved_at"))
            if as_of is not None and (
                published is None or retrieved is None or published > as_of or retrieved > as_of
            ):
                continue
            output.append(item)
        return output[-limit:]

    def summary(self, *, as_of: datetime) -> dict[str, Any]:
        observations = self.list_observations(as_of=as_of, limit=5000)
        documents = self.list_documents(as_of=as_of, limit=2000)
        # Local import avoids a module cycle: financials reuses EvidenceError.
        from .financials import FinancialFactStore
        financial_summary = FinancialFactStore(self.root).summary(as_of=as_of)
        return {
            "observation_count": len(observations),
            "visible_observation_count": sum(bool(x.get("visible_as_of")) for x in observations),
            "eligible_observation_count": sum(bool(x.get("eligible_for_assessment")) for x in observations),
            "document_count": len(documents),
            **financial_summary,
            "series_ids": sorted({str(x.get("series_id")) for x in observations}),
            "sources": sorted({str(x.get("source_name")) for x in observations + documents}),
            "assessment_note": "Provider records do not become thesis evidence until publication timing, effective date, source scope, units and human review are verified.",
        }
