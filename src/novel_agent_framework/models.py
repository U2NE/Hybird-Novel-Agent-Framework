from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class CandidateLens(StrEnum):
    A = "A"
    B = "B"
    C = "C"


class KnowledgeKind(StrEnum):
    KNOWS = "knows"
    SUSPECTS = "suspects"
    FALSELY_BELIEVES = "falsely_believes"
    UNKNOWN = "unknown"


class ForeshadowState(StrEnum):
    PLANNED = "PLANNED"
    PLANTED = "PLANTED"
    HINTED = "HINTED"
    REINFORCED = "REINFORCED"
    DUE = "DUE"
    PAID_OFF = "PAID_OFF"
    DELAYED = "DELAYED"
    ABANDONED = "ABANDONED"


class RunStage(StrEnum):
    CREATED = "created"
    CANDIDATES_READY = "candidates_ready"
    PREFLIGHT_FAILED = "preflight_failed"
    PREFLIGHT_PASSED = "preflight_passed"
    FROZEN = "frozen"
    NARRATIVE_REVIEWED = "narrative_reviewed"
    REVIEWED = "reviewed"
    ADJUDICATED = "adjudicated"
    REPAIRED = "repaired"
    REGRESSION_PASSED = "regression_passed"
    DELTA_VALIDATED = "delta_validated"
    PUBLISHED = "published"
    ABORTED = "aborted"


class MemoryStatus(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


@dataclass(frozen=True)
class DeltaChange:
    kind: str
    entity_key: str
    field: str
    new_value: Any
    old_value: Any | None = None
    evidence: str | None = None
    effective_chapter: int | None = None

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "DeltaChange":
        required = ("kind", "entity_key", "field", "new_value")
        missing = [key for key in required if key not in value]
        if missing:
            raise ValueError(f"delta change missing fields: {', '.join(missing)}")
        return cls(
            kind=str(value["kind"]),
            entity_key=str(value["entity_key"]),
            field=str(value["field"]),
            new_value=value["new_value"],
            old_value=value.get("old_value"),
            evidence=value.get("evidence"),
            effective_chapter=value.get("effective_chapter"),
        )

    def identity(self) -> tuple[str, str, str]:
        return self.kind, self.entity_key, self.field


@dataclass(frozen=True)
class StoryDelta:
    changes: tuple[DeltaChange, ...] = field(default_factory=tuple)

    @classmethod
    def from_dict(cls, value: dict[str, Any] | None) -> "StoryDelta":
        if not value:
            return cls()
        raw = value.get("changes", [])
        if not isinstance(raw, list):
            raise ValueError("delta.changes must be a list")
        changes = tuple(DeltaChange.from_dict(item) for item in raw)
        identities = [item.identity() for item in changes]
        if len(identities) != len(set(identities)):
            raise ValueError("delta contains duplicate kind/entity_key/field identities")
        return cls(changes=changes)

    def as_map(self) -> dict[tuple[str, str, str], Any]:
        return {item.identity(): item.new_value for item in self.changes}


@dataclass(frozen=True)
class ReviewFinding:
    claim: str
    evidence: str
    severity: str
    affected_state: str
    suggested_repair: str
    confidence: float

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "ReviewFinding":
        confidence = float(value["confidence"])
        if not 0.0 <= confidence <= 1.0:
            raise ValueError("review confidence must be within [0, 1]")
        return cls(
            claim=str(value["claim"]),
            evidence=str(value["evidence"]),
            severity=str(value["severity"]),
            affected_state=str(value["affected_state"]),
            suggested_repair=str(value["suggested_repair"]),
            confidence=confidence,
        )
