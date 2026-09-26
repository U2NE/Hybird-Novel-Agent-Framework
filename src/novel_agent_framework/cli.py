from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from .errors import NovelFrameworkError
from .runtime import NovelRuntime, load_json_file


def _json_arg(path: str) -> dict[str, Any]:
    return load_json_file(Path(path))


def _json_list_arg(path: str | None) -> list[dict[str, Any]]:
    if path is None:
        return []
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(value, list):
        raise ValueError(f"JSON root must be a list: {path}")
    return [dict(item) for item in value]


def _read_text(path: str) -> str:
    return Path(path).read_text(encoding="utf-8")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="novel",
        description="Novel Agent Framework deterministic runtime",
    )
    parser.add_argument("--project-root", default=".", help="target novel Git repository")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("doctor")
    sub.add_parser("status")
    sub.add_parser("migrate")

    p = sub.add_parser("init")
    p.add_argument("--title", required=True)
    p.add_argument(
        "--approval-mode",
        choices=["every_chapter", "autonomous"],
        default="every_chapter",
    )

    p = sub.add_parser("workflow-next")
    p.add_argument("--chapter", type=int, required=True)

    p = sub.add_parser("planning-complete")
    p.add_argument("--task", required=True)
    p.add_argument("--output-json", required=True)

    p = sub.add_parser("orchestrate-next")
    p.add_argument("--run", required=True)

    p = sub.add_parser("run-abort")
    p.add_argument("--run", required=True)
    p.add_argument("--reason", required=True)

    p = sub.add_parser("pairwise-complete")
    p.add_argument("--task", required=True)
    p.add_argument("--result-json", required=True)

    p = sub.add_parser("tournament-hard-complete")
    p.add_argument("--task", required=True)
    p.add_argument("--result-json", required=True)

    p = sub.add_parser("story-compass-set")
    p.add_argument("--json", required=True)

    p = sub.add_parser("outline-put")
    p.add_argument("--kind", choices=["volume", "arc", "rolling"], required=True)
    p.add_argument("--json", required=True)

    p = sub.add_parser("route")
    p.add_argument("--stage", required=True)
    p.add_argument("--run")

    p = sub.add_parser("plan-put")
    p.add_argument("--chapter", type=int, required=True)
    p.add_argument("--title", required=True)
    p.add_argument("--plan-json", required=True)
    p.add_argument("--contract-json", required=True)
    p.add_argument("--expected-delta-json", required=True)

    p = sub.add_parser("plan-approve")
    p.add_argument("--chapter", type=int, required=True)
    p.add_argument("--kind", choices=["human", "auto"], default="human")

    p = sub.add_parser("run-start")
    p.add_argument("--chapter", type=int, required=True)
    p.add_argument("--mode", choices=["manual", "auto"])
    p.add_argument("--run-id")

    p = sub.add_parser("resume")
    p.add_argument("--run", required=True)

    p = sub.add_parser("candidate-add")
    p.add_argument("--run", required=True)
    p.add_argument("--lens", choices=["A", "B", "C"], required=True)
    p.add_argument("--prose-file", required=True)
    p.add_argument("--declared-delta-json", required=True)
    p.add_argument("--model", required=True)
    p.add_argument("--effort", required=True)

    p = sub.add_parser("candidate-preflight")
    p.add_argument("--run", required=True)
    p.add_argument("--lens", choices=["A", "B", "C"], required=True)
    p.add_argument("--observation-json", required=True)

    p = sub.add_parser("candidates")
    p.add_argument("--run", required=True)

    p = sub.add_parser("tournament-packets")
    p.add_argument("--run", required=True)

    p = sub.add_parser("tournament-select")
    p.add_argument("--run", required=True)
    p.add_argument("--winner", choices=["A", "B", "C"])
    p.add_argument("--pairwise-json")
    p.add_argument("--reason", required=True)
    p.add_argument("--model")
    p.add_argument("--effort")
    p.add_argument("--hard-adjudication-json")

    p = sub.add_parser("review-add")
    p.add_argument("--run", required=True)
    p.add_argument(
        "--reviewer",
        required=True,
        choices=[
            "continuity",
            "character-consistency",
            "foreshadowing",
            "dialogue",
            "style",
            "reader",
        ],
    )
    p.add_argument("--review-json", required=True)
    p.add_argument("--model", required=True)
    p.add_argument("--effort", required=True)

    p = sub.add_parser("review-adjudicate")
    p.add_argument("--run", required=True)
    p.add_argument("--decision-json", required=True)
    p.add_argument("--conflicts-json")
    p.add_argument("--cross-exam-json")
    p.add_argument("--model", required=True)
    p.add_argument("--effort", required=True)

    p = sub.add_parser("review-hard-complete")
    p.add_argument("--task", required=True)
    p.add_argument("--result-json", required=True)

    p = sub.add_parser("revision-manifest")
    p.add_argument("--run", required=True)
    p.add_argument("--manifest-json", required=True)
    p.add_argument(
        "--repair-level",
        choices=["none", "sentence", "paragraph", "chapter"],
        required=True,
    )
    p.add_argument("--attempts", type=int, default=0)
    p.add_argument("--repaired-prose-file")
    p.add_argument("--declared-delta-json")

    p = sub.add_parser("regression-add")
    p.add_argument("--run", required=True)
    p.add_argument("--check-json", required=True)
    p.add_argument("--attempt", type=int, required=True)
    p.add_argument("--passed", action="store_true")

    p = sub.add_parser("state-scan")
    p.add_argument("--run", required=True)
    p.add_argument("--observed-delta-json", required=True)
    p.add_argument("--observation-json", required=True)
    p.add_argument("--model", required=True)
    p.add_argument("--effort", required=True)

    p = sub.add_parser("delta-validate")
    p.add_argument("--run", required=True)
    p.add_argument("--observed-delta-json")

    p = sub.add_parser("publish")
    p.add_argument("--run", required=True)

    p = sub.add_parser("memory-list")
    p.add_argument(
        "--status",
        choices=["pending", "approved", "rejected", "all"],
        default="pending",
    )

    p = sub.add_parser("memory-approve")
    p.add_argument("--id", required=True)

    p = sub.add_parser("memory-reject")
    p.add_argument("--id", required=True)

    p = sub.add_parser("memory-stage")
    p.add_argument("--run", required=True)
    p.add_argument("--chapter", type=int, required=True)
    p.add_argument("--kind", required=True)
    p.add_argument("--entity", required=True)
    p.add_argument("--field", required=True)
    p.add_argument("--value-json", required=True)
    p.add_argument("--old-value-json")
    p.add_argument("--evidence")
    p.add_argument("--effective-chapter", type=int)

    p = sub.add_parser("idea-propose")
    p.add_argument("--text", required=True)

    p = sub.add_parser("idea-review")
    p.add_argument("--id", required=True)
    p.add_argument("--critique-json", required=True)
    p.add_argument("--model", required=True)
    p.add_argument("--effort", required=True)

    p = sub.add_parser("idea-approve")
    p.add_argument("--id", required=True)

    p = sub.add_parser("idea-reject")
    p.add_argument("--id", required=True)

    p = sub.add_parser("preference-set")
    p.add_argument("--key", required=True)
    p.add_argument("--value-json", required=True)
    p.add_argument("--confidence", type=float, required=True)
    p.add_argument("--evidence-json", required=True)

    p = sub.add_parser("quality-eval")
    p.add_argument("--file", required=True)
    p.add_argument("--observation-json")

    p = sub.add_parser("benchmark-writer")
    p.add_argument("--none-json", required=True)
    p.add_argument("--low-json", required=True)

    p = sub.add_parser("style-profile-create")
    p.add_argument("--name", required=True)
    p.add_argument("--sample-file", required=True)

    sub.add_parser("style-profile-list")

    p = sub.add_parser("style-compare")
    p.add_argument("--file", required=True)

    p = sub.add_parser("voice-bible-put")
    p.add_argument("--character", required=True)
    p.add_argument("--relationship", default="*")
    p.add_argument("--profile-json", required=True)

    p = sub.add_parser("sandbox-next")
    p.add_argument("--chapter", type=int, required=True)
    p.add_argument("--character", required=True)

    p = sub.add_parser("sandbox-propose")
    p.add_argument("--chapter", type=int, required=True)
    p.add_argument("--character", required=True)
    p.add_argument("--simulation-json", required=True)
    p.add_argument("--task", required=True)
    p.add_argument("--model", required=True)
    p.add_argument("--effort", required=True)

    p = sub.add_parser("sandbox-accept")
    p.add_argument("--id", required=True)
    p.add_argument("--reason", required=True)
    p.add_argument("--impact-json", required=True)

    p = sub.add_parser("replan-list")
    p.add_argument(
        "--status",
        choices=["pending", "applied", "rejected", "all"],
        default="pending",
    )

    p = sub.add_parser("replan-apply")
    p.add_argument("--id", required=True)
    p.add_argument("--rolling-outline-json", required=True)

    p = sub.add_parser("replan-reject")
    p.add_argument("--id", required=True)

    p = sub.add_parser("graph-query")
    p.add_argument(
        "--type",
        required=True,
        choices=[
            "knowledge",
            "causal-descendants",
            "causal-ancestors",
            "neighbors",
            "impacted-chapters",
            "object-history",
            "relationship-history",
            "foreshadow-due",
            "promises-due",
            "event",
        ],
    )
    p.add_argument("--key", default="")
    p.add_argument("--chapter", type=int)
    p.add_argument("--max-hops", type=int, default=3)

    p = sub.add_parser("context")
    p.add_argument("--query", required=True)
    p.add_argument("--focus", action="append", default=[])
    p.add_argument("--chapter", type=int)

    p = sub.add_parser("reader-context")
    p.add_argument("--query", required=True)
    p.add_argument("--chapter", type=int, required=True)

    p = sub.add_parser("preflight-check")
    p.add_argument("--chapter", type=int, required=True)
    p.add_argument("--observation-json", required=True)
    p.add_argument("--contract-json")

    sub.add_parser("rebuild-index")

    p = sub.add_parser("rebuild-vectors")
    p.add_argument("--provider", choices=["sentence-transformers", "openai"], required=True)
    p.add_argument("--model")

    p = sub.add_parser("backup")
    p.add_argument("--output", required=True)

    p = sub.add_parser("export")
    p.add_argument("--output", required=True)

    sub.add_parser("stale-list")
    sub.add_parser("eval")
    return parser


def run(args: argparse.Namespace) -> Any:
    runtime = NovelRuntime(Path(args.project_root))
    command = args.command

    if command == "doctor":
        return runtime.doctor()
    if command == "init":
        return runtime.init(args.title, approval_mode=args.approval_mode)
    if command == "migrate":
        return runtime.migrate()
    if command == "status":
        return runtime.status()
    if command == "workflow-next":
        return runtime.workflow_next(args.chapter)
    if command == "planning-complete":
        return runtime.complete_planning_task(args.task, _json_arg(args.output_json))
    if command == "orchestrate-next":
        return runtime.orchestrate_next(args.run)
    if command == "run-abort":
        return runtime.abort_run(args.run, args.reason)
    if command == "pairwise-complete":
        return runtime.complete_pairwise_task(args.task, _json_arg(args.result_json))
    if command == "tournament-hard-complete":
        return runtime.complete_tournament_hard_task(
            args.task, _json_arg(args.result_json)
        )
    if command == "story-compass-set":
        return runtime.set_story_compass(_json_arg(args.json))
    if command == "outline-put":
        return runtime.put_outline(args.kind, _json_arg(args.json))
    if command == "route":
        return runtime.resolve_route(args.stage, args.run)
    if command == "plan-put":
        return runtime.put_plan(
            args.chapter,
            args.title,
            _json_arg(args.plan_json),
            _json_arg(args.contract_json),
            _json_arg(args.expected_delta_json),
        )
    if command == "plan-approve":
        return runtime.approve_plan(args.chapter, kind=args.kind)
    if command == "run-start":
        return runtime.start_run(args.chapter, mode=args.mode, run_id=args.run_id)
    if command == "resume":
        return runtime.resume(args.run)
    if command == "candidate-add":
        return runtime.add_candidate(
            args.run,
            args.lens,
            _read_text(args.prose_file),
            _json_arg(args.declared_delta_json),
            model=args.model,
            reasoning_effort=args.effort,
        )
    if command == "candidate-preflight":
        return runtime.candidate_preflight(
            args.run,
            args.lens,
            _json_arg(args.observation_json),
        )
    if command == "candidates":
        return runtime.list_candidates(args.run)
    if command == "tournament-packets":
        return runtime.tournament_packets(args.run)
    if command == "tournament-select":
        return runtime.select_tournament(
            args.run,
            args.winner,
            pairwise=_json_arg(args.pairwise_json) if args.pairwise_json else None,
            reason=args.reason,
            adjudicator_model=args.model,
            adjudicator_effort=args.effort,
            hard_adjudication=(
                _json_arg(args.hard_adjudication_json)
                if args.hard_adjudication_json
                else None
            ),
        )
    if command == "review-add":
        return runtime.add_review(
            args.run,
            args.reviewer,
            _json_arg(args.review_json),
            model=args.model,
            reasoning_effort=args.effort,
        )
    if command == "review-adjudicate":
        return runtime.adjudicate_reviews(
            args.run,
            _json_arg(args.decision_json),
            conflicts=_json_list_arg(args.conflicts_json),
            cross_exam=_json_arg(args.cross_exam_json) if args.cross_exam_json else None,
            model=args.model,
            reasoning_effort=args.effort,
        )
    if command == "review-hard-complete":
        return runtime.complete_review_hard_task(
            args.task, _json_arg(args.result_json)
        )
    if command == "revision-manifest":
        return runtime.put_revision_manifest(
            args.run,
            _json_arg(args.manifest_json),
            repair_level=args.repair_level,
            repair_attempts=args.attempts,
            repaired_prose=(
                _read_text(args.repaired_prose_file)
                if args.repaired_prose_file
                else None
            ),
            repaired_declared_delta=(
                _json_arg(args.declared_delta_json)
                if args.declared_delta_json
                else None
            ),
        )
    if command == "regression-add":
        return runtime.add_regression_check(
            args.run,
            _json_arg(args.check_json),
            attempt=args.attempt,
            passed=args.passed,
        )
    if command == "state-scan":
        return runtime.record_state_scan(
            args.run,
            _json_arg(args.observed_delta_json),
            _json_arg(args.observation_json),
            model=args.model,
            reasoning_effort=args.effort,
        )
    if command == "delta-validate":
        return runtime.validate_delta(
            args.run,
            _json_arg(args.observed_delta_json) if args.observed_delta_json else None,
        )
    if command == "publish":
        return runtime.publish(args.run)
    if command == "memory-list":
        return runtime.list_memory(args.status)
    if command == "memory-approve":
        return runtime.approve_memory(args.id)
    if command == "memory-reject":
        return runtime.reject_memory(args.id)
    if command == "memory-stage":
        value = json.loads(args.value_json)
        old = json.loads(args.old_value_json) if args.old_value_json else None
        return runtime.stage_memory(
            source_run_id=args.run,
            source_chapter_no=args.chapter,
            kind=args.kind,
            entity_key=args.entity,
            field=args.field,
            new_value=value,
            old_value=old,
            evidence=args.evidence,
            effective_chapter=args.effective_chapter,
        )
    if command == "idea-propose":
        return runtime.idea_propose(args.text)
    if command == "idea-review":
        return runtime.idea_review(
            args.id,
            _json_arg(args.critique_json),
            model=args.model,
            reasoning_effort=args.effort,
        )
    if command == "idea-approve":
        return runtime.idea_decide(args.id, True)
    if command == "idea-reject":
        return runtime.idea_decide(args.id, False)
    if command == "preference-set":
        evidence = json.loads(args.evidence_json)
        if not isinstance(evidence, list):
            raise ValueError("evidence-json must be a JSON list")
        return runtime.set_preference(
            args.key,
            json.loads(args.value_json),
            args.confidence,
            [str(x) for x in evidence],
        )
    if command == "quality-eval":
        return runtime.quality_eval(
            _read_text(args.file),
            _json_arg(args.observation_json) if args.observation_json else None,
        )
    if command == "benchmark-writer":
        none_rows = json.loads(Path(args.none_json).read_text(encoding="utf-8"))
        low_rows = json.loads(Path(args.low_json).read_text(encoding="utf-8"))
        if not isinstance(none_rows, list) or not isinstance(low_rows, list):
            raise ValueError("benchmark inputs must be JSON lists")
        return runtime.benchmark_writer_effort(
            [dict(row) for row in none_rows],
            [dict(row) for row in low_rows],
        )
    if command == "style-profile-create":
        return runtime.create_style_profile(args.name, _read_text(args.sample_file))
    if command == "style-profile-list":
        return runtime.list_style_profiles()
    if command == "style-compare":
        return runtime.compare_style(_read_text(args.file))
    if command == "voice-bible-put":
        return runtime.put_voice_bible(
            args.character,
            args.relationship,
            _json_arg(args.profile_json),
        )
    if command == "sandbox-next":
        return runtime.sandbox_next(args.chapter, args.character)
    if command == "sandbox-propose":
        return runtime.character_sandbox(
            args.chapter,
            args.character,
            _json_arg(args.simulation_json),
            task_id=args.task,
            model=args.model,
            reasoning_effort=args.effort,
        )
    if command == "sandbox-accept":
        return runtime.accept_emergence(
            args.id,
            reason=args.reason,
            impact=_json_arg(args.impact_json),
        )
    if command == "replan-list":
        return runtime.list_replan_requests(args.status)
    if command == "replan-apply":
        return runtime.apply_replan(
            args.id,
            _json_arg(args.rolling_outline_json),
        )
    if command == "replan-reject":
        return runtime.reject_replan(args.id)
    if command == "graph-query":
        return runtime.graph_query(
            args.type,
            key=args.key,
            chapter_no=args.chapter,
            max_hops=args.max_hops,
        )
    if command == "context":
        return runtime.context_compile(
            args.query,
            focus_nodes=args.focus,
            chapter_no=args.chapter,
        )
    if command == "reader-context":
        return runtime.reader_context(args.query, args.chapter)
    if command == "preflight-check":
        return runtime.preflight_check(
            chapter_no=args.chapter,
            observation=_json_arg(args.observation_json),
            contract=_json_arg(args.contract_json) if args.contract_json else None,
        )
    if command == "rebuild-index":
        return runtime.rebuild_index()
    if command == "rebuild-vectors":
        return runtime.rebuild_vectors(args.provider, args.model)
    if command == "backup":
        return runtime.backup(Path(args.output))
    if command == "export":
        return runtime.export_state(Path(args.output))
    if command == "stale-list":
        return runtime.stale_list()
    if command == "eval":
        from .evals import run_eval_suite

        return run_eval_suite(runtime)
    raise ValueError(f"unsupported command: {command}")


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    try:
        result = run(args)
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
        if (
            args.command == "delta-validate"
            and isinstance(result, dict)
            and not result.get("passed")
        ):
            return 2
        if args.command == "doctor" and isinstance(result, dict):
            hard_errors = [
                item
                for key, item in result.items()
                if key in {"database", "agents", "skill"}
                and item.get("status") == "error"
            ]
            if hard_errors:
                return 2
        return 0
    except (NovelFrameworkError, ValueError, json.JSONDecodeError) as exc:
        print(f"novel: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
