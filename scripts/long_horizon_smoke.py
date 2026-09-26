#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from novel_agent_framework.runtime import NovelRuntime


def complete_chapter(rt: NovelRuntime, chapter: int, read_revision: int) -> str:
    delta = {
        "changes": [
            {
                "kind": "fact",
                "entity_key": "serial-anchor",
                "field": "chapter",
                "new_value": chapter,
                "effective_chapter": chapter,
                "evidence": f"chapter-{chapter}",
            }
        ]
    }
    read_points = (
        [{"kind": "fact", "key": "serial-anchor", "revision": read_revision}]
        if chapter > 1 else []
    )
    rt.put_plan(
        chapter,
        f"{chapter}장",
        {"beats": [{"id": "beat", "purpose": "advance"}]},
        {
            "required_beats": ["beat"],
            "read_points": read_points,
            "exit_conditions": {},
        },
        delta,
    )
    run = rt.start_run(chapter, mode="auto")["id"]

    for task in rt.orchestrate_next(run)["tasks"]:
        lens = task["stage"].split(":")[1]
        rt.add_candidate(
            run, lens, f"{chapter}장 {lens} 후보", delta,
            model=task["model"], reasoning_effort=task["reasoning_effort"],
        )

    for task in rt.orchestrate_next(run)["tasks"]:
        lens = task["stage"].split(":")[1]
        result = rt.candidate_preflight(
            run, lens, {"realized_beats": ["beat"], "exit_state": {}}
        )
        assert result["passed"]

    for task in rt.orchestrate_next(run)["tasks"]:
        pair = task["stage"].split(":")[1]
        a, b = pair.split("-")
        preferred = "P" if "P" in (a, b) else "Q"
        rt.complete_pairwise_task(
            task["task_id"],
            {"preferred": preferred, "confidence": 0.95, "reason": "long-horizon"},
        )
    action = rt.orchestrate_next(run)
    selected = rt.select_tournament(
        run, action["winner_lens"], reason="long-horizon clear tournament"
    )
    frozen_hash = selected["frozen_hash"]

    for task in rt.orchestrate_next(run)["tasks"]:
        reviewer = task["stage"].split(":", 1)[1]
        rt.add_review(
            run, reviewer,
            {"draft_hash": frozen_hash, "findings": []},
            model=task["model"], reasoning_effort=task["reasoning_effort"],
        )
    for task in rt.orchestrate_next(run)["tasks"]:
        reviewer = task["stage"].split(":", 1)[1]
        review = {"draft_hash": frozen_hash, "findings": []}
        if reviewer == "reader":
            review["context_policy"] = "reader-visible-only"
        rt.add_review(
            run, reviewer, review,
            model=task["model"], reasoning_effort=task["reasoning_effort"],
        )

    adj = rt.orchestrate_next(run)["tasks"][0]
    rt.adjudicate_reviews(
        run, {"repairs": []},
        model=adj["model"], reasoning_effort=adj["reasoning_effort"],
    )
    assert rt.orchestrate_next(run)["tasks"][0]["stage"] == "repair"
    rt.put_revision_manifest(run, {"repairs": []}, repair_level="none")

    scan = rt.orchestrate_next(run)["tasks"][0]
    rt.record_state_scan(
        run, delta, {"realized_beats": ["beat"], "exit_state": {}},
        model=scan["model"], reasoning_effort=scan["reasoning_effort"],
    )
    assert rt.validate_delta(run)["passed"]
    rt.publish(run)

    pending = [row for row in rt.list_memory("pending") if row["source_run_id"] == run]
    assert len(pending) == 1
    approval = rt.approve_memory(pending[0]["id"])
    assert approval["canonical_revision"] == chapter
    assert rt.publish(run)["idempotent"] is True
    assert rt.resume(run)["run"]["stage"] == "published"
    return run


def main() -> int:
    parser = argparse.ArgumentParser(description="Long-horizon deterministic novel runtime smoke")
    parser.add_argument("--chapters", type=int, default=30)
    args = parser.parse_args()
    total = args.chapters
    if total < 10:
        raise SystemExit("--chapters must be at least 10")
    with tempfile.TemporaryDirectory(prefix="novel-long-horizon-") as temp:
        root = Path(temp)
        cfg = root / ".novel" / "config"
        cfg.mkdir(parents=True)
        shutil.copy2(ROOT / "config" / "model-routing.json", cfg / "model-routing.json")
        shutil.copy2(ROOT / "config" / "retrieval.json", cfg / "retrieval.json")

        rt = NovelRuntime(root)
        rt.init("장기 회귀", approval_mode="autonomous")
        runs: list[str] = []
        for chapter in range(1, total + 1):
            runs.append(complete_chapter(rt, chapter, chapter - 1))

        status = rt.status()
        assert status["project"]["canonical_revision"] == total
        assert len(status["publications"]) == total
        assert status["pending_memory"] == 0
        assert status["stale_revisions"] == 0

        at10 = rt.context_compile("serial-anchor", chapter_no=10)
        at_end = rt.context_compile("serial-anchor", chapter_no=total)
        assert any("10" in row["content"] for row in at10["documents"])
        assert not any(str(total) in row["content"] for row in at10["documents"])
        assert any(str(total) in row["content"] for row in at_end["documents"])

        staged = rt.stage_memory(
            source_run_id=runs[4],
            source_chapter_no=5,
            kind="fact",
            entity_key="serial-anchor",
            field="chapter",
            new_value="retroactive-5",
            old_value=5,
            effective_chapter=5,
            evidence="long-horizon-retcon",
        )
        approval = rt.approve_memory(staged["id"])
        stale = rt.stale_list()
        stale_chapters = {row["chapter_no"] for row in stale}
        assert set(range(6, total + 1)).issubset(stale_chapters)
        assert approval["stale_marked"] >= total - 5

        print(json.dumps({
            "ok": True,
            "chapters": total,
            "canonical_revision": rt.status()["project"]["canonical_revision"],
            "stale_after_retcon": len(stale_chapters),
            "first_stale": min(stale_chapters),
            "last_stale": max(stale_chapters),
        }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
