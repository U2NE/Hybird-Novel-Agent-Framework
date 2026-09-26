#!/usr/bin/env python3
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

SOURCE = Path(__file__).resolve().parents[1]
INSTALLER = SOURCE / "scripts" / "install_project.py"


def run_json(cmd: list[str], expected: int = 0) -> object:
    result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if result.returncode != expected:
        raise RuntimeError(
            f"command failed ({result.returncode}, expected {expected}): {' '.join(cmd)}\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
    return json.loads(result.stdout) if result.stdout.strip() else {}


def write_json(path: Path, value: object) -> Path:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def write_text(path: Path, value: str) -> Path:
    path.write_text(value, encoding="utf-8")
    return path


def cli(target: Path, *args: str, expected: int = 0) -> object:
    return run_json(
        [
            sys.executable,
            str(target / ".novel" / "bin" / "novel.py"),
            "--project-root",
            str(target),
            *args,
        ],
        expected=expected,
    )


def complete_planning(target: Path, work: Path, chapter: int, delta: dict, contract: dict) -> None:
    first = cli(target, "workflow-next", "--chapter", str(chapter))
    director = first["tasks"][0]
    out = write_json(work / f"planning-director-{chapter}.json", {"roles": [], "reason": "fixture"})
    cli(target, "planning-complete", "--task", director["task_id"], "--output-json", str(out))

    second = cli(target, "workflow-next", "--chapter", str(chapter))
    planner = second["tasks"][0]
    plan_out = write_json(
        work / f"planning-chapter-{chapter}.json",
        {
            "title": f"{chapter}장",
            "plan": {"beats": [{"id": "beat", "purpose": "advance"}]},
            "contract": contract,
            "expected_delta": delta,
        },
    )
    cli(target, "planning-complete", "--task", planner["task_id"], "--output-json", str(plan_out))
    ready = cli(target, "workflow-next", "--chapter", str(chapter))
    assert ready["runtime_action"] == "start-run-auto"


def complete_run(target: Path, work: Path, chapter: int, delta: dict) -> tuple[str, dict]:
    started = cli(target, "run-start", "--chapter", str(chapter), "--mode", "auto")
    run_id = started["id"]
    delta_file = write_json(work / f"delta-{chapter}.json", delta)

    writer = cli(target, "orchestrate-next", "--run", run_id)
    assert {t["stage"] for t in writer["tasks"]} == {"write:A", "write:B", "write:C"}
    for task in writer["tasks"]:
        lens = task["stage"].split(":")[1]
        prose = write_text(work / f"ch{chapter}-{lens}.txt", f"{chapter}장 {lens} 후보 본문.")
        cli(
            target,
            "candidate-add",
            "--run", run_id,
            "--lens", lens,
            "--prose-file", str(prose),
            "--declared-delta-json", str(delta_file),
            "--model", task["model"],
            "--effort", task["reasoning_effort"],
        )

    preflights = cli(target, "orchestrate-next", "--run", run_id)
    observation = write_json(
        work / f"candidate-observation-{chapter}.json",
        {"realized_beats": ["beat"], "exit_state": {}},
    )
    for task in preflights["tasks"]:
        lens = task["stage"].split(":")[1]
        result = cli(
            target,
            "candidate-preflight",
            "--run", run_id,
            "--lens", lens,
            "--observation-json", str(observation),
        )
        assert result["passed"] is True

    pair_tasks = cli(target, "orchestrate-next", "--run", run_id)
    assert len(pair_tasks["tasks"]) == 6
    for task in pair_tasks["tasks"]:
        pair = task["stage"].split(":")[1]
        a, b = pair.split("-")
        preferred = "P" if "P" in (a, b) else "Q"
        result_file = write_json(
            work / f"{chapter}-{pair}.json",
            {"preferred": preferred, "confidence": 0.95, "reason": "fixture"},
        )
        cli(
            target,
            "pairwise-complete",
            "--task", task["task_id"],
            "--result-json", str(result_file),
        )

    tournament_action = cli(target, "orchestrate-next", "--run", run_id)
    assert tournament_action["runtime_action"] == "tournament-select"
    selected = cli(
        target,
        "tournament-select",
        "--run", run_id,
        "--winner", tournament_action["winner_lens"],
        "--reason", "clear deterministic fixture",
    )
    frozen_hash = selected["frozen_hash"]

    narrative = cli(target, "orchestrate-next", "--run", run_id)
    for task in narrative["tasks"]:
        reviewer = task["stage"].split(":", 1)[1]
        review = write_json(
            work / f"review-{chapter}-{reviewer}.json",
            {"draft_hash": frozen_hash, "findings": []},
        )
        cli(
            target,
            "review-add",
            "--run", run_id,
            "--reviewer", reviewer,
            "--review-json", str(review),
            "--model", task["model"],
            "--effort", task["reasoning_effort"],
        )

    craft = cli(target, "orchestrate-next", "--run", run_id)
    for task in craft["tasks"]:
        reviewer = task["stage"].split(":", 1)[1]
        payload = {"draft_hash": frozen_hash, "findings": []}
        if reviewer == "reader":
            payload["context_policy"] = "reader-visible-only"
            assert task["input"]["context"]["audience"] == "reader"
        review = write_json(work / f"review-{chapter}-{reviewer}.json", payload)
        cli(
            target,
            "review-add",
            "--run", run_id,
            "--reviewer", reviewer,
            "--review-json", str(review),
            "--model", task["model"],
            "--effort", task["reasoning_effort"],
        )

    adjudication = cli(target, "orchestrate-next", "--run", run_id)["tasks"][0]
    decision = write_json(work / f"adjudication-{chapter}.json", {"repairs": []})
    cli(
        target,
        "review-adjudicate",
        "--run", run_id,
        "--decision-json", str(decision),
        "--model", adjudication["model"],
        "--effort", adjudication["reasoning_effort"],
    )

    repair = cli(target, "orchestrate-next", "--run", run_id)["tasks"][0]
    assert repair["stage"] == "repair"
    manifest = write_json(work / f"manifest-{chapter}.json", {"repairs": []})
    cli(
        target,
        "revision-manifest",
        "--run", run_id,
        "--manifest-json", str(manifest),
        "--repair-level", "none",
    )

    scan = cli(target, "orchestrate-next", "--run", run_id)["tasks"][0]
    assert scan["stage"] == "state-scan"
    scan_observation = write_json(
        work / f"scan-observation-{chapter}.json",
        {"realized_beats": ["beat"], "exit_state": {}},
    )
    cli(
        target,
        "state-scan",
        "--run", run_id,
        "--observed-delta-json", str(delta_file),
        "--observation-json", str(scan_observation),
        "--model", scan["model"],
        "--effort", scan["reasoning_effort"],
    )
    validated = cli(target, "delta-validate", "--run", run_id)
    assert validated["passed"] is True
    published = cli(target, "publish", "--run", run_id)
    return run_id, published


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="novel-runtime-smoke-") as temp:
        base = Path(temp)
        target = base / "target"
        work = base / "work"
        target.mkdir()
        work.mkdir()
        subprocess.run(["git", "init", "-q", str(target)], check=True)

        installed = run_json([sys.executable, str(INSTALLER), str(target)])
        assert len(list((target / ".codex" / "agents").glob("novel-*.toml"))) == 13
        assert (target / ".novel" / "config" / "retrieval.json").is_file()
        assert installed["validation"]["config"] == "parsed"

        cli(target, "init", "--title", "연기 도시", "--approval-mode", "autonomous")
        compass = write_json(
            work / "compass.json",
            {
                "premise": "비밀 때문에 관계가 뒤틀린다.",
                "ending_promise": "비밀의 대가를 선택한다.",
                "core_conflict": "욕망과 책임",
                "themes": ["책임"],
            },
        )
        cli(target, "story-compass-set", "--json", str(compass))

        delta1 = {
            "changes": [
                {
                    "kind": "fact",
                    "entity_key": "secret",
                    "field": "owner",
                    "new_value": "B",
                    "evidence": "1장",
                    "effective_chapter": 1,
                }
            ]
        }
        complete_planning(
            target, work, 1, delta1,
            {"required_beats": ["beat"], "read_points": [], "exit_conditions": {}},
        )
        run1, pub1 = complete_run(target, work, 1, delta1)
        assert pub1["canon_mutated"] is False
        reader_after_publish = cli(
            target, "reader-context", "--query", "후보", "--chapter", "1"
        )
        assert reader_after_publish["documents"], "published prose must be reader-visible immediately"

        pending = cli(target, "memory-list", "--status", "pending")
        assert len(pending) == 1
        approved1 = cli(target, "memory-approve", "--id", pending[0]["id"])
        assert approved1["canonical_revision"] == 1

        delta2 = {"changes": []}
        complete_planning(
            target, work, 2, delta2,
            {
                "required_beats": ["beat"],
                "read_points": [{"kind": "fact", "key": "secret", "revision": 1}],
                "exit_conditions": {},
            },
        )
        run2, pub2 = complete_run(target, work, 2, delta2)
        replay = cli(target, "publish", "--run", run2)
        assert replay["idempotent"] is True
        assert cli(target, "resume", "--run", run2)["run"]["stage"] == "published"

        changed = cli(
            target,
            "memory-stage",
            "--run", run1,
            "--chapter", "1",
            "--kind", "fact",
            "--entity", "secret",
            "--field", "owner",
            "--value-json", '"C"',
            "--old-value-json", '"B"',
            "--effective-chapter", "1",
            "--evidence", "retroactive correction",
        )
        approval2 = cli(target, "memory-approve", "--id", changed["id"])
        assert approval2["stale_marked"] >= 1
        stale = cli(target, "stale-list")
        assert any(row["chapter_no"] == 2 for row in stale)

        idea = cli(target, "idea-propose", "--text", "조력자가 첫 사건을 설계했다.")
        critique = write_json(
            work / "idea-critique.json",
            {
                "story_fit": "conditional",
                "novelty": "medium",
                "motivation_fit": "requires prior grievance",
                "causality": "can explain the first incident",
                "setup_debt": ["초기 단서 재검토"],
                "payoff_opportunities": ["관계 붕괴"],
                "continuity_conflicts": [],
                "reader_information_impact": "delays certainty",
                "opportunity_cost": "reduces alternate culprit space",
                "stale_blast_radius": [1, 2],
                "improvements": ["설계자와 실행자를 분리"],
            },
        )
        idea_task = idea["review_task"]
        cli(
            target,
            "idea-review",
            "--id", idea["id"],
            "--critique-json", str(critique),
            "--model", idea_task["model"],
            "--effort", idea_task["reasoning_effort"],
        )
        accepted = cli(target, "idea-approve", "--id", idea["id"])
        assert accepted["canon_mutated"] is False
        author_context = cli(target, "context", "--query", "조력자", "--chapter", "2")
        assert any(doc["doc_type"] == "author_intent" for doc in author_context["documents"])

        sandbox_task = cli(
            target,
            "sandbox-next",
            "--chapter", "2",
            "--character", "Alice",
        )
        sandbox_payload = write_json(
            work / "sandbox.json",
            {
                "perception": {"threat": "Bob"},
                "memory": {"last": "betrayal"},
                "intention": {"goal": "confront"},
                "action": {"type": "leave-plan"},
                "consequence": {"future_outline_conflict": True},
            },
        )
        sandbox = cli(
            target,
            "sandbox-propose",
            "--chapter", "2",
            "--character", "Alice",
            "--simulation-json", str(sandbox_payload),
            "--task", sandbox_task["task_id"],
            "--model", sandbox_task["model"],
            "--effort", sandbox_task["reasoning_effort"],
        )
        impact = write_json(work / "sandbox-impact.json", {"chapters": [2, 3]})
        replanned = cli(
            target,
            "sandbox-accept",
            "--id", sandbox["id"],
            "--reason", "인물 동기가 기존 국소 플롯보다 강함",
            "--impact-json", str(impact),
        )
        assert replanned["canon_mutated"] is False
        assert cli(target, "replan-list", "--status", "pending")

        status = cli(target, "status")
        assert status["pending_memory"] == 0
        assert "pending_agent_tasks" in status

        evaluation = cli(target, "eval")
        assert evaluation["passed"] is True

        doctor = cli(target, "doctor")
        assert doctor["agents"]["status"] == "ok"
        assert doctor["retrieval"]["status"] == "ok"

        print(
            json.dumps(
                {
                    "ok": True,
                    "installed_target": str(target),
                    "chapter1_run": run1,
                    "chapter2_run": run2,
                    "canonical_revision": status["project"]["canonical_revision"],
                    "stale_revisions": status["stale_revisions"],
                    "agent_roles": 13,
                    "codex_auth": doctor["codex"]["status"],
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
