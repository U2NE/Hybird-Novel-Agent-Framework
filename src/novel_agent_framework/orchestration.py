from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .models import RunStage


@dataclass(frozen=True)
class TaskBlueprint:
    task_stage: str
    role: str
    route_key: str
    output_schema: str
    input_hint: dict[str, Any]


def blueprints_for_stage(stage: str) -> list[TaskBlueprint]:
    if stage == RunStage.CREATED.value:
        return [
            TaskBlueprint(
                f"write:{lens}",
                "novel-writer",
                "writer",
                "ChapterDraftCandidate+DeclaredDelta",
                {"lens": lens},
            )
            for lens in ("A", "B", "C")
        ]
    if stage == RunStage.CANDIDATES_READY.value:
        return [
            TaskBlueprint(
                f"preflight:{lens}",
                "novel-state-scanner",
                "state-scanner",
                "StructuredContinuityObservation",
                {"lens": lens, "scope": "candidate"},
            )
            for lens in ("A", "B", "C")
        ]
    if stage == RunStage.PREFLIGHT_PASSED.value:
        return [
            TaskBlueprint(
                f"tournament:{pair}",
                "novel-story-director",
                "tournament-judge",
                "PairwisePreference",
                {"comparison_id": pair, "blind": True},
            )
            for pair in ("P-Q", "Q-P", "P-R", "R-P", "Q-R", "R-Q")
        ]
    if stage == RunStage.FROZEN.value:
        return [
            TaskBlueprint(
                "review:continuity",
                "novel-continuity-reviewer",
                "continuity-reviewer",
                "ReviewArtifact",
                {"reviewer": "continuity", "gate": "narrative"},
            ),
            TaskBlueprint(
                "review:character-consistency",
                "novel-character-consistency-reviewer",
                "character-consistency-reviewer",
                "ReviewArtifact",
                {"reviewer": "character-consistency", "gate": "narrative"},
            ),
            TaskBlueprint(
                "review:foreshadowing",
                "novel-foreshadowing-tracker",
                "foreshadowing-tracker",
                "ReviewArtifact",
                {"reviewer": "foreshadowing", "gate": "narrative"},
            ),
        ]
    if stage == RunStage.NARRATIVE_REVIEWED.value:
        return [
            TaskBlueprint(
                "review:dialogue",
                "novel-dialogue-specialist",
                "dialogue-specialist",
                "ReviewArtifact",
                {"reviewer": "dialogue", "gate": "craft"},
            ),
            TaskBlueprint(
                "review:style",
                "novel-style-critic",
                "style-critic",
                "ReviewArtifact",
                {"reviewer": "style", "gate": "craft"},
            ),
            TaskBlueprint(
                "review:reader",
                "novel-reader-simulator",
                "reader-simulator",
                "ReviewArtifact",
                {"reviewer": "reader", "gate": "reader", "reader_visible_only": True},
            ),
        ]
    if stage == RunStage.REVIEWED.value:
        return [
            TaskBlueprint(
                "review-adjudication",
                "novel-story-director",
                "story-director",
                "ReviewAdjudication",
                {"max_cross_exam_rounds": 1},
            )
        ]
    if stage == RunStage.ADJUDICATED.value:
        return [
            TaskBlueprint(
                "repair",
                "novel-writer",
                "writer",
                "RepairedDraft+RevisionManifest+DeclaredDelta",
                {"repair_hierarchy": ["sentence", "paragraph", "chapter"]},
            )
        ]
    if stage == RunStage.REPAIRED.value:
        return [
            TaskBlueprint(
                "regression",
                "novel-continuity-reviewer",
                "continuity-reviewer",
                "RegressionCheck",
                {"bounded_attempts": 3},
            ),
            TaskBlueprint(
                "state-scan",
                "novel-state-scanner",
                "state-scanner",
                "ObservedDelta+StructuredObservation",
                {"scope": "final-draft"},
            ),
        ]
    if stage == RunStage.REGRESSION_PASSED.value:
        return [
            TaskBlueprint(
                "state-scan",
                "novel-state-scanner",
                "state-scanner",
                "ObservedDelta+StructuredObservation",
                {"scope": "final-draft"},
            )
        ]
    return []
