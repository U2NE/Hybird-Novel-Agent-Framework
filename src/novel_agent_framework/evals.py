from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any

from .models import StoryDelta
from .state_tx import compare_deltas


def run_eval_suite(runtime: Any) -> dict[str, Any]:
    """Run non-mutating framework logic checks plus isolated continuity-trap fixtures."""
    cases: list[dict[str, Any]] = []

    def record(name: str, passed: bool, detail: str = "") -> None:
        cases.append({"name": name, "passed": passed, "detail": detail})

    same = {
        "changes": [
            {"kind": "fact", "entity_key": "door", "field": "state", "new_value": "open"}
        ]
    }
    diff = {
        "changes": [
            {"kind": "fact", "entity_key": "door", "field": "state", "new_value": "closed"}
        ]
    }
    result = compare_deltas(
        StoryDelta.from_dict(same), StoryDelta.from_dict(same), StoryDelta.from_dict(same)
    )
    record("three-way-identical-delta-passes", result["passed"])

    result = compare_deltas(
        StoryDelta.from_dict(same), StoryDelta.from_dict(same), StoryDelta.from_dict(diff)
    )
    record("observed-delta-divergence-blocks", not result["passed"])

    result = compare_deltas(
        StoryDelta.from_dict(same), StoryDelta.from_dict(diff), StoryDelta.from_dict(diff)
    )
    record("declared-delta-divergence-blocks", not result["passed"])

    try:
        StoryDelta.from_dict(
            {
                "changes": [
                    {"kind": "fact", "entity_key": "x", "field": "a", "new_value": 1},
                    {"kind": "fact", "entity_key": "x", "field": "a", "new_value": 2},
                ]
            }
        )
        record("duplicate-delta-identity-rejected", False)
    except ValueError:
        record("duplicate-delta-identity-rejected", True)

    doctor = runtime.doctor()
    record("database-schema-readable", doctor["database"]["status"] == "ok")

    # Isolated continuity trap suite: never mutates the user's project.
    from .runtime import NovelRuntime

    with tempfile.TemporaryDirectory(prefix="novel-eval-") as temp:
        rt = NovelRuntime(Path(temp))
        rt.init("eval", approval_mode="autonomous")
        rt.put_plan(
            1,
            "1장",
            {"beats": []},
            {
                "required_beats": ["must-happen"],
                "forbidden_reveals": ["hidden-fact"],
                "exit_conditions": {"door": "open"},
                "read_points": [],
            },
            {"changes": []},
        )
        run_id = rt.start_run(1, mode="auto")["id"]

        seeds = [
            ("character_state", "Ghost", "alive", False),
            ("object_state", "sword", "state", {"state": {"destroyed": True}}),
            (
                "relationship",
                "Alice->Bob",
                "trust",
                {"subject": "Alice", "object": "Bob", "relation": "trust", "value": "low"},
            ),
            (
                "foreshadow",
                "seed-1",
                "state",
                {
                    "state": "PLANNED",
                    "description": "아직 심지 않음",
                    "due_chapter": 1,
                },
            ),
        ]
        for kind, entity, field, value in seeds:
            staged = rt.stage_memory(
                source_run_id=run_id,
                source_chapter_no=1,
                kind=kind,
                entity_key=entity,
                field=field,
                new_value=value,
                effective_chapter=1,
            )
            rt.approve_memory(staged["id"])

        observation = {
            "actors": [{"character": "Ghost", "evidence": "유령이 문을 연다"}],
            "travel": [
                {
                    "character": "Alice",
                    "from": "서울",
                    "to": "부산",
                    "elapsed_minutes": 5,
                    "minimum_minutes": 120,
                }
            ],
            "knowledge_uses": [
                {"character": "Alice", "fact_key": "killer", "evidence": "범인을 단정함"}
            ],
            "object_uses": [{"object_key": "sword", "evidence": "파괴된 검을 다시 휘두름"}],
            "relationship_claims": [
                {
                    "subject": "Alice",
                    "object": "Bob",
                    "relation": "trust",
                    "value": "high",
                }
            ],
            "world_rule_checks": [
                {"rule_key": "no-teleport", "compliant": False, "evidence": "순간이동"}
            ],
            "foreshadow_payoffs": [
                {"foreshadow_key": "seed-1", "evidence": "심지 않은 복선 회수"}
            ],
            "revealed_facts": ["hidden-fact"],
            "reader_reveals": [
                {"fact_key": "murder", "source_event": "event-that-has-not-happened"}
            ],
            "realized_beats": [],
            "exit_state": {"door": "closed"},
        }
        report = rt.preflight_check(chapter_no=1, observation=observation)
        codes = {item["code"] for item in report["issues"]}
        expected_codes = {
            "DEAD_CHARACTER_ACTS",
            "IMPOSSIBLE_TRAVEL",
            "KNOWLEDGE_LEAK",
            "DESTROYED_OBJECT_REAPPEARS",
            "RELATIONSHIP_STATE_RESET",
            "WORLD_RULE_VIOLATION",
            "PAYOFF_WITHOUT_PLANT",
            "FORESHADOW_DUE_UNRESOLVED",
            "FORBIDDEN_REVEAL",
            "REVEAL_BEFORE_EVENT_OCCURRENCE",
            "EXIT_STATE_MISMATCH",
            "REQUIRED_BEAT_MISSING",
        }
        record(
            "continuity-trap-suite",
            expected_codes.issubset(codes) and not report["passed"],
            f"codes={sorted(codes)}",
        )

    return {
        "schema": "novel-eval/v1",
        "passed": all(case["passed"] for case in cases),
        "cases": cases,
    }
