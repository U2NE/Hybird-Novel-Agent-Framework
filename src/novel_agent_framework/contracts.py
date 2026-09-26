from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class ReadPoint:
    kind: str
    key: str
    revision: int | None = None

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "ReadPoint":
        if not value.get("kind") or not value.get("key"):
            raise ValueError("read point requires kind and key")
        revision = value.get("revision")
        if revision is not None and int(revision) < 0:
            raise ValueError("read point revision must be non-negative")
        return cls(str(value["kind"]), str(value["key"]), int(revision) if revision is not None else None)


@dataclass(frozen=True)
class BeatUnit:
    id: str
    purpose: str
    conflict: str = ""
    required_change: str = ""

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "BeatUnit":
        if not value.get("id"):
            raise ValueError("beat unit requires id")
        return cls(
            id=str(value["id"]),
            purpose=str(value.get("purpose") or value.get("goal") or ""),
            conflict=str(value.get("conflict", "")),
            required_change=str(value.get("required_change", "")),
        )


@dataclass(frozen=True)
class ChapterContract:
    required_beats: tuple[str, ...] = ()
    forbidden_reveals: tuple[str, ...] = ()
    exit_conditions: dict[str, Any] = field(default_factory=dict)
    read_points: tuple[ReadPoint, ...] = ()
    pov: str | None = None
    location: str | None = None
    time_anchor: str | None = None

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "ChapterContract":
        if not isinstance(value, dict):
            raise ValueError("ChapterContract must be an object")
        read_points_raw = value.get("read_points", [])
        if not isinstance(read_points_raw, list):
            raise ValueError("ChapterContract.read_points must be a list")
        read_points = tuple(ReadPoint.from_dict(item) for item in read_points_raw)
        identities = [(item.kind, item.key) for item in read_points]
        if len(identities) != len(set(identities)):
            raise ValueError("ChapterContract has duplicate read points")
        required = value.get("required_beats", [])
        forbidden = value.get("forbidden_reveals", [])
        exits = value.get("exit_conditions", value.get("expected_exit_state", {}))
        if not isinstance(required, list) or not isinstance(forbidden, list) or not isinstance(exits, dict):
            raise ValueError("invalid ChapterContract list/object fields")
        return cls(
            required_beats=tuple(map(str, required)),
            forbidden_reveals=tuple(map(str, forbidden)),
            exit_conditions=exits,
            read_points=read_points,
            pov=str(value["pov"]) if value.get("pov") is not None else None,
            location=str(value["location"]) if value.get("location") is not None else None,
            time_anchor=str(value["time_anchor"]) if value.get("time_anchor") is not None else None,
        )


@dataclass(frozen=True)
class ContextPacket:
    chapter_no: int
    canonical_revision: int
    reader_cutoff: int
    evidence: tuple[dict[str, Any], ...]
    graph_edges: tuple[dict[str, Any], ...]

    def __post_init__(self) -> None:
        if self.chapter_no < 1:
            raise ValueError("chapter_no must be positive")
        if self.reader_cutoff > self.chapter_no:
            raise ValueError("reader cutoff cannot exceed target chapter")


@dataclass(frozen=True)
class ChapterDraftCandidate:
    lens: str
    prose_hash: str
    model: str
    reasoning_effort: str

    def __post_init__(self) -> None:
        if self.lens not in {"A", "B", "C"}:
            raise ValueError("candidate lens must be A/B/C")


@dataclass(frozen=True)
class ChapterRevisionRef:
    chapter_no: int
    revision: int
    prose_hash: str
    stale: bool = False
