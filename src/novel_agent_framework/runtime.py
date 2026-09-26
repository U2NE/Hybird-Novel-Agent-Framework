from __future__ import annotations

import hashlib
import json
import sqlite3
import subprocess
import shutil
import uuid
from pathlib import Path
from typing import Any, Iterable

from .db import initialize_database, json_dumps, open_database, db_path, migrate_database, SCHEMA_VERSION
from .errors import NovelFrameworkError, StateConflict, ValidationBlocked
from .graph import NarrativeGraph
from .models import CandidateLens, RunStage, StoryDelta, ReviewFinding
from .orchestration import blueprints_for_stage
from .contracts import ChapterContract
from .continuity import validate_structured_continuity
from .retrieval import ContextCompiler, rebuild_search_projection
from .vectors import build_provider, rebuild_vector_embeddings
from .style import extract_style_profile, compare_to_profile
from .quality import audit_prose
from .benchmark import compare_writer_efforts
from .state_tx import compare_deltas
from .tournament import aggregate as aggregate_tournament, prepare_packets, alias_mapping

NARRATIVE_REVIEWERS = {
    "continuity",
    "character-consistency",
    "foreshadowing",
}
CRAFT_REVIEWERS = {
    "dialogue",
    "style",
    "reader",
}
REQUIRED_REVIEWERS = NARRATIVE_REVIEWERS | CRAFT_REVIEWERS

LENS_STRATEGIES = {
    "A": "external-action-conflict",
    "B": "interiority-subtext-relationship-pressure",
    "C": "unusual-contract-compliant-atmosphere",
}


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:20]}"


def deterministic_id(prefix: str, *parts: str) -> str:
    raw = "\x1f".join(parts)
    return f"{prefix}-{hashlib.sha256(raw.encode('utf-8')).hexdigest()[:24]}"


def load_json_file(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"JSON root must be an object: {path}")
    return value


class NovelRuntime:
    def __init__(self, project_root: Path) -> None:
        self.root = project_root.resolve()

    @property
    def database_path(self) -> Path:
        return db_path(self.root)

    def init(
        self,
        title: str,
        *,
        approval_mode: str = "every_chapter",
    ) -> dict[str, Any]:
        if approval_mode not in {"every_chapter", "autonomous"}:
            raise ValueError("approval_mode must be every_chapter or autonomous")
        path = initialize_database(self.root, title, approval_mode)
        return {"initialized": True, "database": str(path), "approval_mode": approval_mode}

    def status(self) -> dict[str, Any]:
        with open_database(self.root) as con:
            project = dict(
                con.execute("SELECT * FROM project WHERE id='default'").fetchone()
            )
            active_runs = [
                dict(row)
                for row in con.execute(
                    "SELECT * FROM chapter_runs WHERE completed_at IS NULL ORDER BY started_at"
                )
            ]
            publication_rows = [
                dict(row)
                for row in con.execute(
                    """
                    SELECT p.chapter_no,cr.id AS revision_id,cr.revision,cr.stale,cr.stale_reason
                    FROM publications p JOIN chapter_revisions cr ON cr.id=p.chapter_revision_id
                    ORDER BY p.chapter_no
                    """
                )
            ]
            pending_count = con.execute(
                "SELECT COUNT(*) c FROM pending_memory WHERE status='pending'"
            ).fetchone()["c"]
            stale_count = con.execute(
                "SELECT COUNT(*) c FROM chapter_revisions WHERE stale=1"
            ).fetchone()["c"]
            pending_tasks = [
                dict(row)
                for row in con.execute(
                    """
                    SELECT id,run_id,chapter_no,stage,role,model,reasoning_effort,attempt
                    FROM agent_tasks WHERE status='pending'
                    ORDER BY created_at,id
                    """
                )
            ]
            replan_count = con.execute(
                "SELECT COUNT(*) c FROM replan_requests WHERE status='pending'"
            ).fetchone()["c"]
            return {
                "project": project,
                "active_runs": active_runs,
                "publications": publication_rows,
                "pending_memory": int(pending_count),
                "pending_agent_tasks": pending_tasks,
                "pending_replans": int(replan_count),
                "stale_revisions": int(stale_count),
            }

    def put_plan(
        self,
        chapter_no: int,
        title: str,
        plan: dict[str, Any],
        contract: dict[str, Any],
        expected_delta: dict[str, Any],
    ) -> dict[str, Any]:
        StoryDelta.from_dict(expected_delta)
        ChapterContract.from_dict(contract)
        with open_database(self.root) as con:
            row = con.execute(
                "SELECT revision,status FROM chapter_plans WHERE chapter_no=?",
                (chapter_no,),
            ).fetchone()
            if row and row["status"] == "approved":
                raise StateConflict(
                    "approved chapter plan cannot be silently overwritten; create a new revision explicitly"
                )
            revision = 1 if not row else int(row["revision"]) + 1
            con.execute(
                """
                INSERT INTO chapter_plans(
                  chapter_no,title,plan_json,contract_json,expected_delta_json,status,revision
                ) VALUES(?,?,?,?,?,'generated',?)
                ON CONFLICT(chapter_no) DO UPDATE SET
                  title=excluded.title,
                  plan_json=excluded.plan_json,
                  contract_json=excluded.contract_json,
                  expected_delta_json=excluded.expected_delta_json,
                  status='generated',
                  approval_kind=NULL,
                  approved_at=NULL,
                  revision=excluded.revision,
                  updated_at=CURRENT_TIMESTAMP
                """,
                (
                    chapter_no,
                    title,
                    json_dumps(plan),
                    json_dumps(contract),
                    json_dumps(expected_delta),
                    revision,
                ),
            )
            con.commit()
            return {"chapter_no": chapter_no, "revision": revision, "status": "generated"}

    def approve_plan(self, chapter_no: int, *, kind: str = "human") -> dict[str, Any]:
        if kind not in {"human", "auto"}:
            raise ValueError("approval kind must be human or auto")
        with open_database(self.root) as con:
            cur = con.execute(
                """
                UPDATE chapter_plans
                SET status='approved',approval_kind=?,approved_at=CURRENT_TIMESTAMP,
                    updated_at=CURRENT_TIMESTAMP
                WHERE chapter_no=? AND status='generated'
                """,
                (kind, chapter_no),
            )
            if cur.rowcount == 0:
                row = con.execute(
                    "SELECT status,approval_kind FROM chapter_plans WHERE chapter_no=?",
                    (chapter_no,),
                ).fetchone()
                if row and row["status"] == "approved":
                    return {
                        "chapter_no": chapter_no,
                        "status": "approved",
                        "approval_kind": row["approval_kind"],
                        "idempotent": True,
                    }
                raise StateConflict("chapter plan does not exist or cannot be approved")
            con.commit()
            return {"chapter_no": chapter_no, "status": "approved", "approval_kind": kind}

    def start_run(
        self,
        chapter_no: int,
        *,
        mode: str | None = None,
        run_id: str | None = None,
    ) -> dict[str, Any]:
        with open_database(self.root) as con:
            project = con.execute(
                "SELECT approval_mode FROM project WHERE id='default'"
            ).fetchone()
            if not project:
                raise StateConflict("project row is missing")
            if mode is None:
                mode = "auto" if project["approval_mode"] == "autonomous" else "manual"
            if mode not in {"manual", "auto"}:
                raise ValueError("run mode must be manual or auto")
            plan = con.execute(
                "SELECT * FROM chapter_plans WHERE chapter_no=?",
                (chapter_no,),
            ).fetchone()
            if not plan:
                raise StateConflict("chapter plan is required before starting a run")
            if mode == "manual" and plan["status"] != "approved":
                raise ValidationBlocked("manual mode requires explicit human chapter-plan approval")
            if mode == "auto" and plan["status"] != "approved":
                con.execute(
                    """
                    UPDATE chapter_plans
                    SET status='approved',approval_kind='auto',approved_at=CURRENT_TIMESTAMP,
                        updated_at=CURRENT_TIMESTAMP
                    WHERE chapter_no=?
                    """,
                    (chapter_no,),
                )
            aborted_count = con.execute(
                "SELECT COUNT(*) c FROM chapter_runs WHERE chapter_no=? AND stage=?",
                (chapter_no, RunStage.ABORTED.value),
            ).fetchone()["c"]
            if int(aborted_count) >= 3:
                raise ValidationBlocked(
                    "chapter run retry budget exhausted after three aborted attempts"
                )
            existing = con.execute(
                """
                SELECT * FROM chapter_runs
                WHERE chapter_no=? AND completed_at IS NULL
                ORDER BY started_at DESC LIMIT 1
                """,
                (chapter_no,),
            ).fetchone()
            if existing:
                return {**dict(existing), "idempotent": True}
            run_id = run_id or new_id(f"ch{chapter_no:04d}")
            con.execute(
                """
                INSERT INTO chapter_runs(id,chapter_no,plan_revision,mode,stage)
                VALUES(?,?,?,?,?)
                """,
                (run_id, chapter_no, int(plan["revision"]), mode, RunStage.CREATED.value),
            )
            self._transition(
                con,
                run_id,
                None,
                RunStage.CREATED.value,
                f"{run_id}:created",
                {"chapter_no": chapter_no, "plan_revision": int(plan["revision"])},
            )
            con.commit()
            return {
                "id": run_id,
                "chapter_no": chapter_no,
                "plan_revision": int(plan["revision"]),
                "mode": mode,
                "stage": RunStage.CREATED.value,
            }

    def abort_run(self, run_id: str, reason: str) -> dict[str, Any]:
        if not reason.strip():
            raise ValidationBlocked("abort requires a non-empty reason")
        with open_database(self.root) as con:
            run = self._get_run(con, run_id)
            if run["stage"] == RunStage.ABORTED.value:
                return {**dict(run), "idempotent": True}
            if run["stage"] not in {
                RunStage.PREFLIGHT_FAILED.value,
                RunStage.CREATED.value,
                RunStage.CANDIDATES_READY.value,
            }:
                raise StateConflict(f"run cannot be aborted from stage {run['stage']}")
            self._transition(
                con,
                run_id,
                run["stage"],
                RunStage.ABORTED.value,
                f"{run_id}:abort",
                {"reason": reason},
            )
            con.execute(
                "UPDATE chapter_runs SET completed_at=CURRENT_TIMESTAMP WHERE id=?",
                (run_id,),
            )
            con.execute(
                "UPDATE agent_tasks SET status='cancelled' "
                "WHERE run_id=? AND status='pending'",
                (run_id,),
            )
            con.commit()
            return {
                "run_id": run_id,
                "stage": RunStage.ABORTED.value,
                "reason": reason,
            }

    def resume(self, run_id: str) -> dict[str, Any]:
        with open_database(self.root) as con:
            run = self._get_run(con, run_id)
            checkpoint = con.execute(
                "SELECT * FROM checkpoints WHERE run_id=?", (run_id,)
            ).fetchone()
            return {
                "run": dict(run),
                "checkpoint": dict(checkpoint) if checkpoint else None,
                "next_action": self._next_action(con, run_id, run["stage"]),
            }

    def add_candidate(
        self,
        run_id: str,
        lens: str,
        prose: str,
        declared_delta: dict[str, Any],
        *,
        model: str,
        reasoning_effort: str,
    ) -> dict[str, Any]:
        lens = CandidateLens(lens).value
        StoryDelta.from_dict(declared_delta)
        digest = sha256_text(prose)
        with open_database(self.root) as con:
            run = self._get_run(con, run_id)
            task = self._require_task(con, run_id, f"write:{lens}")
            if task["model"] != model or task["reasoning_effort"] != reasoning_effort:
                raise ValidationBlocked("candidate model/effort must match runtime-issued task route")
            if run["stage"] not in {RunStage.CREATED.value, RunStage.CANDIDATES_READY.value}:
                raise StateConflict(f"cannot add candidate at stage {run['stage']}")
            existing = con.execute(
                "SELECT * FROM chapter_candidates WHERE run_id=? AND lens=?",
                (run_id, lens),
            ).fetchone()
            if existing:
                if existing["prose_hash"] == digest and existing["declared_delta_json"] == json_dumps(declared_delta):
                    return {**dict(existing), "idempotent": True}
                raise StateConflict(f"candidate lens {lens} already exists with different content")
            candidate_id = deterministic_id("cand", run_id, lens, digest)
            con.execute(
                """
                INSERT INTO chapter_candidates(
                  id,run_id,lens,strategy,prose,prose_hash,declared_delta_json,model,reasoning_effort
                ) VALUES(?,?,?,?,?,?,?,?,?)
                """,
                (
                    candidate_id,
                    run_id,
                    lens,
                    LENS_STRATEGIES[lens],
                    prose,
                    digest,
                    json_dumps(declared_delta),
                    model,
                    reasoning_effort,
                ),
            )
            count = con.execute(
                "SELECT COUNT(*) c FROM chapter_candidates WHERE run_id=?", (run_id,)
            ).fetchone()["c"]
            if count == 3:
                lenses = {
                    row["lens"]
                    for row in con.execute(
                        "SELECT lens FROM chapter_candidates WHERE run_id=?", (run_id,)
                    )
                }
                if lenses != {"A", "B", "C"}:
                    raise StateConflict("candidate set must be exactly A/B/C")
                self._transition(
                    con,
                    run_id,
                    run["stage"],
                    RunStage.CANDIDATES_READY.value,
                    f"{run_id}:candidates-ready",
                    {"lenses": ["A", "B", "C"]},
                )
            elif count > 3:
                raise StateConflict("exactly three candidates are permitted")
            self._complete_task(
                con,
                task,
                {
                    "candidate_id": candidate_id,
                    "lens": lens,
                    "prose_hash": digest,
                    "declared_delta": declared_delta,
                },
                output_hash=digest,
            )
            con.commit()
            return {
                "id": candidate_id,
                "run_id": run_id,
                "lens": lens,
                "strategy": LENS_STRATEGIES[lens],
                "prose_hash": digest,
                "candidate_count": int(count),
            }

    def candidate_preflight(
        self,
        run_id: str,
        lens: str,
        observation: dict[str, Any],
    ) -> dict[str, Any]:
        lens = CandidateLens(lens).value
        with open_database(self.root) as con:
            run = self._get_run(con, run_id)
            task = self._require_task(con, run_id, f"preflight:{lens}")
            if run["stage"] not in {
                RunStage.CANDIDATES_READY.value,
                RunStage.PREFLIGHT_PASSED.value,
            }:
                raise StateConflict(
                    f"candidate preflight requires candidates_ready, got {run['stage']}"
                )
            candidate = con.execute(
                "SELECT * FROM chapter_candidates WHERE run_id=? AND lens=?",
                (run_id, lens),
            ).fetchone()
            if not candidate:
                raise StateConflict(f"candidate {lens} is missing")
            plan = con.execute(
                "SELECT contract_json FROM chapter_plans WHERE chapter_no=?",
                (run["chapter_no"],),
            ).fetchone()
            if not plan:
                raise StateConflict("ChapterContract is missing")
            contract = ChapterContract.from_dict(json.loads(plan["contract_json"]))
            result = validate_structured_continuity(
                con,
                chapter_no=int(run["chapter_no"]),
                observation=observation,
                contract=contract,
            )
            payload = json_dumps(observation)
            digest = sha256_text(candidate["prose_hash"] + ":" + payload)
            existing = con.execute(
                "SELECT * FROM candidate_preflights WHERE run_id=? AND lens=?",
                (run_id, lens),
            ).fetchone()
            if existing:
                if existing["artifact_hash"] == digest:
                    return {**dict(existing), "result": json.loads(existing["result_json"]), "idempotent": True}
                raise StateConflict("candidate preflight is immutable for a run/lens")
            con.execute(
                """
                INSERT INTO candidate_preflights(
                  run_id,lens,observation_json,result_json,passed,artifact_hash
                ) VALUES(?,?,?,?,?,?)
                """,
                (
                    run_id,
                    lens,
                    payload,
                    json_dumps(result),
                    int(result["passed"]),
                    digest,
                ),
            )
            rows = list(
                con.execute(
                    "SELECT lens,passed FROM candidate_preflights WHERE run_id=?",
                    (run_id,),
                )
            )
            if len(rows) == 3:
                statuses = {row["lens"]: bool(row["passed"]) for row in rows}
                if all(statuses.get(item, False) for item in ("A", "B", "C")):
                    if run["stage"] == RunStage.CANDIDATES_READY.value:
                        self._transition(
                            con,
                            run_id,
                            RunStage.CANDIDATES_READY.value,
                            RunStage.PREFLIGHT_PASSED.value,
                            f"{run_id}:candidate-preflights-pass",
                            {"lenses": ["A", "B", "C"]},
                        )
                elif run["stage"] == RunStage.CANDIDATES_READY.value:
                    self._transition(
                        con,
                        run_id,
                        RunStage.CANDIDATES_READY.value,
                        RunStage.PREFLIGHT_FAILED.value,
                        f"{run_id}:candidate-preflights-fail",
                        {"statuses": statuses},
                    )
            self._complete_task(
                con,
                task,
                {"lens": lens, "passed": result["passed"], "result": result},
                output_hash=digest,
            )
            con.commit()
            return {
                "run_id": run_id,
                "lens": lens,
                "candidate_hash": candidate["prose_hash"],
                "passed": result["passed"],
                "result": result,
                "artifact_hash": digest,
            }

    def tournament_packets(self, run_id: str) -> dict[str, Any]:
        with open_database(self.root) as con:
            run = self._get_run(con, run_id)
            if run["stage"] != RunStage.PREFLIGHT_PASSED.value:
                raise ValidationBlocked("tournament packets require all three candidate preflights to pass")
            candidates = {
                row["lens"]: row["prose"]
                for row in con.execute(
                    "SELECT lens,prose FROM chapter_candidates WHERE run_id=?",
                    (run_id,),
                )
            }
            if set(candidates) != {"A", "B", "C"}:
                raise StateConflict("candidate set is incomplete")
            return prepare_packets(run_id, candidates)

    def list_candidates(self, run_id: str) -> list[dict[str, Any]]:
        with open_database(self.root) as con:
            self._get_run(con, run_id)
            return [
                dict(row)
                for row in con.execute(
                    "SELECT id,lens,strategy,prose_hash,model,reasoning_effort,created_at "
                    "FROM chapter_candidates WHERE run_id=? ORDER BY lens",
                    (run_id,),
                )
            ]

    def select_tournament(
        self,
        run_id: str,
        winner_lens: str | None,
        *,
        pairwise: dict[str, Any] | None = None,
        reason: str,
        adjudicator_model: str | None = None,
        adjudicator_effort: str | None = None,
        hard_adjudication: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        with open_database(self.root) as con:
            run = self._get_run(con, run_id)
            if run["stage"] != RunStage.PREFLIGHT_PASSED.value:
                raise StateConflict(
                    f"tournament requires preflight_passed, got {run['stage']}"
                )
            existing = con.execute(
                "SELECT * FROM tournaments WHERE run_id=?", (run_id,)
            ).fetchone()
            if existing:
                return {**dict(existing), "idempotent": True}

            task_rows = list(
                con.execute(
                    """
                    SELECT stage,output_json,status FROM agent_tasks
                    WHERE run_id=? AND stage LIKE 'tournament:%'
                    ORDER BY stage
                    """,
                    (run_id,),
                )
            )
            if len(task_rows) != 6 or any(row["status"] != "completed" for row in task_rows):
                raise ValidationBlocked("all six anonymous pairwise runtime tasks must complete")
            task_pairwise = {
                row["stage"].split(":", 1)[1]: json.loads(row["output_json"])
                for row in task_rows
            }
            if pairwise is not None and json_dumps(pairwise) != json_dumps(task_pairwise):
                raise ValidationBlocked("supplied pairwise data does not match completed runtime tasks")
            pairwise = task_pairwise
            aggregation = aggregate_tournament(run_id, pairwise)
            if aggregation["hard_adjudication_required"]:
                hard_task = con.execute(
                    """
                    SELECT * FROM agent_tasks
                    WHERE run_id=? AND stage='tournament-hard' AND status='completed'
                    """,
                    (run_id,),
                ).fetchone()
                if not hard_task:
                    raise ValidationBlocked(
                        "tournament cycle/low confidence requires completed runtime-issued hard adjudication"
                    )
                hard_output = json.loads(hard_task["output_json"])
                winner_alias = str(hard_output.get("winner_alias", ""))
                mapping = alias_mapping(run_id)
                if winner_alias not in mapping:
                    raise ValidationBlocked("invalid completed hard tournament winner alias")
                selected = mapping[winner_alias]
                model = str(hard_task["model"])
                effort = str(hard_task["reasoning_effort"])
                hard_payload = {
                    "winner_alias": winner_alias,
                    "winner_lens": selected,
                    "model": model,
                    "effort": effort,
                    "reason": str(hard_output.get("reason", "")),
                    "task_id": hard_task["id"],
                }
            else:
                selected = str(aggregation["condorcet_lens"])
                if winner_lens is not None and CandidateLens(winner_lens).value != selected:
                    raise ValidationBlocked(
                        f"winner {winner_lens} contradicts deterministic Condorcet result {selected}"
                    )
                model = "deterministic-runtime"
                effort = "none"
                hard_payload = None

            candidates = list(
                con.execute(
                    "SELECT * FROM chapter_candidates WHERE run_id=? ORDER BY lens",
                    (run_id,),
                )
            )
            winner = next(row for row in candidates if row["lens"] == selected)
            con.execute(
                """
                INSERT INTO tournaments(
                  run_id,winner_lens,pairwise_json,aggregation_json,hard_adjudication_json,
                  reason,adjudicator_model,adjudicator_effort
                ) VALUES(?,?,?,?,?,?,?,?)
                """,
                (
                    run_id,
                    selected,
                    json_dumps(pairwise),
                    json_dumps(aggregation),
                    json_dumps(hard_payload) if hard_payload is not None else None,
                    reason,
                    model,
                    effort,
                ),
            )
            con.execute(
                "INSERT INTO frozen_drafts(run_id,candidate_id,prose,prose_hash) VALUES(?,?,?,?)",
                (run_id, winner["id"], winner["prose"], winner["prose_hash"]),
            )
            self._transition(
                con,
                run_id,
                RunStage.PREFLIGHT_PASSED.value,
                RunStage.FROZEN.value,
                f"{run_id}:freeze:{selected}",
                {
                    "winner_lens": selected,
                    "candidate_id": winner["id"],
                    "aggregation": aggregation,
                },
                winner["prose_hash"],
            )
            con.commit()
            return {
                "run_id": run_id,
                "winner_lens": selected,
                "candidate_id": winner["id"],
                "frozen_hash": winner["prose_hash"],
                "aggregation": aggregation,
                "hard_adjudication": hard_payload,
            }

    def add_review(
        self,
        run_id: str,
        reviewer: str,
        review: dict[str, Any],
        *,
        model: str,
        reasoning_effort: str,
    ) -> dict[str, Any]:
        if reviewer not in REQUIRED_REVIEWERS:
            raise ValueError(
                f"unknown reviewer {reviewer}; expected one of {sorted(REQUIRED_REVIEWERS)}"
            )
        findings = review.get("findings")
        if not isinstance(findings, list):
            raise ValidationBlocked("review artifact requires a findings list")
        for finding in findings:
            if not isinstance(finding, dict):
                raise ValidationBlocked("each review finding must be an object")
            ReviewFinding.from_dict(finding)
        if reviewer == "reader" and review.get("context_policy") != "reader-visible-only":
            raise ValidationBlocked(
                "reader review must attest context_policy=reader-visible-only"
            )

        with open_database(self.root) as con:
            run = self._get_run(con, run_id)
            task = self._require_task(con, run_id, f"review:{reviewer}")
            if task["model"] != model or task["reasoning_effort"] != reasoning_effort:
                raise ValidationBlocked("review model/effort must match runtime-issued task route")
            frozen = con.execute(
                "SELECT prose_hash FROM frozen_drafts WHERE run_id=?", (run_id,)
            ).fetchone()
            if not frozen:
                raise StateConflict("frozen draft is missing")
            if review.get("draft_hash") != frozen["prose_hash"]:
                raise ValidationBlocked(
                    "review must attest the exact frozen draft_hash; reviewers must share one frozen draft"
                )

            if reviewer in NARRATIVE_REVIEWERS:
                if run["stage"] not in {
                    RunStage.FROZEN.value,
                    RunStage.NARRATIVE_REVIEWED.value,
                }:
                    raise StateConflict(
                        f"narrative review not permitted at stage {run['stage']}"
                    )
            else:
                if run["stage"] not in {
                    RunStage.NARRATIVE_REVIEWED.value,
                    RunStage.REVIEWED.value,
                }:
                    raise StateConflict(
                        "dialogue/style/reader reviews are gated behind narrative reviewers"
                    )

            artifact = json_dumps(review)
            digest = sha256_text(artifact)
            existing = con.execute(
                "SELECT * FROM reviews WHERE run_id=? AND reviewer=?",
                (run_id, reviewer),
            ).fetchone()
            if existing:
                if existing["artifact_hash"] == digest:
                    return {**dict(existing), "idempotent": True}
                raise StateConflict(
                    "review artifacts are immutable; a reviewer cannot overwrite its prior artifact"
                )
            review_id = deterministic_id("review", run_id, reviewer, digest)
            con.execute(
                """
                INSERT INTO reviews(
                  id,run_id,reviewer,review_json,artifact_hash,model,reasoning_effort
                ) VALUES(?,?,?,?,?,?,?)
                """,
                (review_id, run_id, reviewer, artifact, digest, model, reasoning_effort),
            )
            reviewers = {
                row["reviewer"]
                for row in con.execute(
                    "SELECT reviewer FROM reviews WHERE run_id=?", (run_id,)
                )
            }
            if (
                NARRATIVE_REVIEWERS.issubset(reviewers)
                and run["stage"] == RunStage.FROZEN.value
            ):
                self._transition(
                    con,
                    run_id,
                    RunStage.FROZEN.value,
                    RunStage.NARRATIVE_REVIEWED.value,
                    f"{run_id}:narrative-reviews-complete",
                    {"reviewers": sorted(NARRATIVE_REVIEWERS)},
                )
                run = self._get_run(con, run_id)
            if (
                REQUIRED_REVIEWERS.issubset(reviewers)
                and run["stage"] == RunStage.NARRATIVE_REVIEWED.value
            ):
                self._transition(
                    con,
                    run_id,
                    RunStage.NARRATIVE_REVIEWED.value,
                    RunStage.REVIEWED.value,
                    f"{run_id}:reviews-complete",
                    {"reviewers": sorted(REQUIRED_REVIEWERS)},
                )
            self._complete_task(
                con,
                task,
                {"reviewer": reviewer, "artifact_hash": digest, "review": review},
                output_hash=digest,
            )
            con.commit()
            return {
                "id": review_id,
                "reviewer": reviewer,
                "artifact_hash": digest,
                "narrative_gate_complete": NARRATIVE_REVIEWERS.issubset(reviewers),
                "all_required_complete": REQUIRED_REVIEWERS.issubset(reviewers),
            }

    def adjudicate_reviews(
        self,
        run_id: str,
        decision: dict[str, Any],
        *,
        conflicts: list[dict[str, Any]] | None = None,
        cross_exam: dict[str, Any] | None = None,
        model: str,
        reasoning_effort: str,
    ) -> dict[str, Any]:
        conflicts = conflicts or []
        if cross_exam is not None and not conflicts:
            raise ValidationBlocked("cross-examination is allowed only for genuine conflicts")
        if cross_exam is not None and int(cross_exam.get("round", 1)) != 1:
            raise ValidationBlocked("review cross-examination is limited to one round")
        if cross_exam is not None:
            participants = set(map(str, cross_exam.get("participants", [])))
            conflict_reviewers: set[str] = set()
            for conflict in conflicts:
                for reviewer in conflict.get("reviewers", []):
                    conflict_reviewers.add(str(reviewer))
            if not participants or not participants.issubset(conflict_reviewers):
                raise ValidationBlocked(
                    "cross-exam participants must be reviewers named in the declared conflicts"
                )
            if not participants.issubset(REQUIRED_REVIEWERS):
                raise ValidationBlocked("cross-exam contains unknown reviewer")
        with open_database(self.root) as con:
            run = self._get_run(con, run_id)
            task = self._require_task(con, run_id, "review-adjudication")
            if conflicts:
                if task["model"] != model or task["reasoning_effort"] != reasoning_effort:
                    raise ValidationBlocked(
                        "preliminary review synthesis must match its runtime-issued route"
                    )
                rows = list(
                    con.execute(
                        "SELECT reviewer,review_json,artifact_hash FROM reviews "
                        "WHERE run_id=? ORDER BY reviewer",
                        (run_id,),
                    )
                )
                bundle = [
                    {
                        "reviewer": row["reviewer"],
                        "review": json.loads(row["review_json"]),
                        "artifact_hash": row["artifact_hash"],
                    }
                    for row in rows
                ]
                preliminary = {
                    "decision": decision,
                    "conflicts": conflicts,
                    "requires_hard": True,
                }
                self._complete_task(con, task, preliminary)
                hard_route = self._resolve_route_config("story-director-hard")
                hard_id = deterministic_id(
                    "task", run_id, "review-adjudication-hard",
                    "novel-story-director", "1"
                )
                hard_task = con.execute(
                    "SELECT * FROM agent_tasks WHERE id=?", (hard_id,)
                ).fetchone()
                if not hard_task:
                    con.execute(
                        """
                        INSERT INTO agent_tasks(
                          id,run_id,chapter_no,stage,role,route_key,model,reasoning_effort,
                          input_json,output_schema,status,attempt
                        ) VALUES(?,?,?,?,?,?,?,?,?,?,'pending',1)
                        """,
                        (
                            hard_id, run_id, int(run["chapter_no"]),
                            "review-adjudication-hard", "novel-story-director",
                            "story-director-hard", hard_route["model"],
                            hard_route["reasoning_effort"],
                            json_dumps({
                                "reviews": bundle,
                                "preliminary_decision": decision,
                                "conflicts": conflicts,
                                "max_cross_exam_rounds": 1,
                            }),
                            "HardReviewAdjudication",
                        ),
                    )
                    con.execute(
                        """
                        INSERT INTO model_route_decisions(
                          run_id,stage,role,model,reasoning_effort,policy_key
                        ) VALUES(?,?,?,?,?,?)
                        """,
                        (
                            run_id,"review-adjudication-hard","novel-story-director",
                            hard_route["model"],hard_route["reasoning_effort"],
                            "story-director-hard",
                        ),
                    )
                con.commit()
                return {
                    "run_id": run_id,
                    "requires_hard_adjudication": True,
                    "task": {
                        "task_id": hard_id,
                        "stage": "review-adjudication-hard",
                        "role": "novel-story-director",
                        "model": hard_route["model"],
                        "reasoning_effort": hard_route["reasoning_effort"],
                        "output_schema": "HardReviewAdjudication",
                    },
                }
            elif task["model"] != model or task["reasoning_effort"] != reasoning_effort:
                raise ValidationBlocked("review adjudication route must match runtime task")
            if run["stage"] != RunStage.REVIEWED.value:
                raise StateConflict("review adjudication requires all two-stage reviews")
            existing = con.execute(
                "SELECT * FROM review_adjudications WHERE run_id=?", (run_id,)
            ).fetchone()
            if existing:
                return {**dict(existing), "idempotent": True}
            rows = list(
                con.execute(
                    "SELECT reviewer,review_json,artifact_hash FROM reviews WHERE run_id=? ORDER BY reviewer",
                    (run_id,),
                )
            )
            if {row["reviewer"] for row in rows} != REQUIRED_REVIEWERS:
                raise ValidationBlocked("review bundle is incomplete")
            bundle = [
                {
                    "reviewer": row["reviewer"],
                    "review": json.loads(row["review_json"]),
                    "artifact_hash": row["artifact_hash"],
                }
                for row in rows
            ]
            payload = {
                "bundle": bundle,
                "decision": decision,
                "conflicts": conflicts,
                "cross_exam": cross_exam,
            }
            digest = sha256_text(json_dumps(payload))
            con.execute(
                """
                INSERT INTO review_adjudications(
                  run_id,bundle_json,decision_json,conflicts_json,cross_exam_json,
                  cross_exam_used,artifact_hash,model,reasoning_effort
                ) VALUES(?,?,?,?,?,?,?,?,?)
                """,
                (
                    run_id,
                    json_dumps(bundle),
                    json_dumps(decision),
                    json_dumps(conflicts),
                    json_dumps(cross_exam) if cross_exam is not None else None,
                    int(cross_exam is not None),
                    digest,
                    model,
                    reasoning_effort,
                ),
            )
            self._transition(
                con,
                run_id,
                RunStage.REVIEWED.value,
                RunStage.ADJUDICATED.value,
                f"{run_id}:review-adjudication",
                {
                    "artifact_hash": digest,
                    "conflict_count": len(conflicts),
                    "cross_exam_used": bool(cross_exam),
                },
                digest,
            )
            self._complete_task(
                con,
                task,
                {
                    "decision": decision,
                    "conflicts": conflicts,
                    "cross_exam": cross_exam,
                    "artifact_hash": digest,
                },
                output_hash=digest,
            )
            con.commit()
            return {
                "run_id": run_id,
                "artifact_hash": digest,
                "conflict_count": len(conflicts),
                "cross_exam_used": bool(cross_exam),
            }

    def complete_review_hard_task(
        self,
        task_id: str,
        result: dict[str, Any],
    ) -> dict[str, Any]:
        with open_database(self.root) as con:
            task = con.execute("SELECT * FROM agent_tasks WHERE id=?", (task_id,)).fetchone()
            if not task or task["stage"] != "review-adjudication-hard":
                raise ValidationBlocked("task is not a review-adjudication-hard task")
            if task["status"] == "completed":
                existing = con.execute(
                    "SELECT * FROM review_adjudications WHERE run_id=?",
                    (task["run_id"],),
                ).fetchone()
                return {**dict(existing), "idempotent": True} if existing else dict(task)
            source = json.loads(task["input_json"])
            conflicts = list(source.get("conflicts", []))
            decision = result.get("decision")
            if not isinstance(decision, dict):
                raise ValidationBlocked("hard review adjudication requires decision object")
            cross_exam = result.get("cross_exam")
            if cross_exam is not None:
                if int(cross_exam.get("round", 1)) != 1:
                    raise ValidationBlocked("review cross-examination is limited to one round")
                participants = set(map(str, cross_exam.get("participants", [])))
                conflict_reviewers = {
                    str(reviewer)
                    for conflict in conflicts
                    for reviewer in conflict.get("reviewers", [])
                }
                if not participants or not participants.issubset(conflict_reviewers):
                    raise ValidationBlocked(
                        "cross-exam participants must be named in declared conflicts"
                    )
            bundle = source["reviews"]
            payload = {
                "bundle": bundle,
                "decision": decision,
                "conflicts": conflicts,
                "cross_exam": cross_exam,
            }
            digest = sha256_text(json_dumps(payload))
            con.execute(
                """
                INSERT INTO review_adjudications(
                  run_id,bundle_json,decision_json,conflicts_json,cross_exam_json,
                  cross_exam_used,artifact_hash,model,reasoning_effort
                ) VALUES(?,?,?,?,?,?,?,?,?)
                """,
                (
                    task["run_id"],json_dumps(bundle),json_dumps(decision),
                    json_dumps(conflicts),
                    json_dumps(cross_exam) if cross_exam is not None else None,
                    int(cross_exam is not None),digest,task["model"],task["reasoning_effort"],
                ),
            )
            self._complete_task(con, task, result, output_hash=digest)
            run = self._get_run(con, task["run_id"])
            self._transition(
                con,task["run_id"],run["stage"],RunStage.ADJUDICATED.value,
                f"{task['run_id']}:review-hard-adjudication",
                {"artifact_hash":digest,"conflict_count":len(conflicts)},
                digest,
            )
            con.commit()
            return {
                "run_id": task["run_id"],
                "artifact_hash": digest,
                "conflict_count": len(conflicts),
                "cross_exam_used": bool(cross_exam),
            }

    def put_revision_manifest(
        self,
        run_id: str,
        manifest: dict[str, Any],
        *,
        repair_level: str,
        repair_attempts: int = 0,
        repaired_prose: str | None = None,
        repaired_declared_delta: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if repair_level not in {"none", "sentence", "paragraph", "chapter"}:
            raise ValueError("invalid repair level")
        if repair_attempts < 0 or repair_attempts > 3:
            raise ValidationBlocked("repair attempts must be within bounded budget 0..3")
        payload = json_dumps(manifest)
        digest = sha256_text(payload)
        with open_database(self.root) as con:
            run = self._get_run(con, run_id)
            task = self._require_task(con, run_id, "repair")
            if run["stage"] not in {RunStage.ADJUDICATED.value, RunStage.REPAIRED.value}:
                raise StateConflict("revision manifest requires review adjudication")
            existing = con.execute(
                "SELECT * FROM revision_manifests WHERE run_id=?", (run_id,)
            ).fetchone()
            if existing:
                existing_draft = con.execute(
                    "SELECT * FROM repaired_drafts WHERE run_id=?", (run_id,)
                ).fetchone()
                if existing["artifact_hash"] == digest and existing_draft:
                    return {
                        **dict(existing),
                        "repaired_hash": existing_draft["prose_hash"],
                        "idempotent": True,
                    }
                raise StateConflict(
                    "revision manifest already exists; create a new run for a different repair"
                )

            frozen = con.execute(
                """
                SELECT f.prose,f.prose_hash,c.declared_delta_json
                FROM frozen_drafts f
                JOIN chapter_candidates c ON c.id=f.candidate_id
                WHERE f.run_id=?
                """,
                (run_id,),
            ).fetchone()
            if not frozen:
                raise StateConflict("frozen draft is missing")

            if repair_level == "none":
                final_prose = repaired_prose if repaired_prose is not None else frozen["prose"]
                declared_json = (
                    json_dumps(repaired_declared_delta)
                    if repaired_declared_delta is not None
                    else frozen["declared_delta_json"]
                )
            else:
                if repaired_prose is None or repaired_declared_delta is None:
                    raise ValidationBlocked(
                        "non-trivial repair requires repaired prose and revised DeclaredDelta"
                    )
                StoryDelta.from_dict(repaired_declared_delta)
                final_prose = repaired_prose
                declared_json = json_dumps(repaired_declared_delta)

            final_hash = sha256_text(final_prose)
            con.execute(
                """
                INSERT INTO revision_manifests(
                  run_id,manifest_json,artifact_hash,repair_level,repair_attempts
                ) VALUES(?,?,?,?,?)
                """,
                (run_id, payload, digest, repair_level, repair_attempts),
            )
            con.execute(
                """
                INSERT INTO repaired_drafts(
                  run_id,prose,prose_hash,declared_delta_json,repair_level,repair_attempts
                ) VALUES(?,?,?,?,?,?)
                """,
                (
                    run_id,
                    final_prose,
                    final_hash,
                    declared_json,
                    repair_level,
                    repair_attempts,
                ),
            )
            self._transition(
                con,
                run_id,
                run["stage"],
                RunStage.REPAIRED.value,
                f"{run_id}:repair-manifest",
                {
                    "repair_level": repair_level,
                    "repair_attempts": repair_attempts,
                    "final_prose_hash": final_hash,
                },
                final_hash,
            )
            self._complete_task(
                con,
                task,
                {
                    "manifest": manifest,
                    "repair_level": repair_level,
                    "repaired_hash": final_hash,
                    "declared_delta": json.loads(declared_json),
                },
                output_hash=final_hash,
            )
            con.commit()
            return {
                "run_id": run_id,
                "artifact_hash": digest,
                "repaired_hash": final_hash,
                "repair_level": repair_level,
                "repair_attempts": repair_attempts,
            }

    def add_regression_check(
        self,
        run_id: str,
        check: dict[str, Any],
        *,
        attempt: int,
        passed: bool,
    ) -> dict[str, Any]:
        if attempt < 1 or attempt > 3:
            raise ValidationBlocked("regression attempt must be in 1..3")
        payload = json_dumps(check)
        digest = sha256_text(payload)
        with open_database(self.root) as con:
            run = self._get_run(con, run_id)
            task = self._require_task(con, run_id, "regression", attempt=attempt)
            if run["stage"] != RunStage.REPAIRED.value:
                raise StateConflict("regression checks require repaired stage")
            existing = con.execute(
                "SELECT * FROM regression_checks WHERE run_id=? AND attempt=?",
                (run_id, attempt),
            ).fetchone()
            if existing:
                if existing["artifact_hash"] == digest and bool(existing["passed"]) == passed:
                    return {**dict(existing), "idempotent": True}
                raise StateConflict("regression attempt is immutable")
            con.execute(
                """
                INSERT INTO regression_checks(run_id,attempt,check_json,passed,artifact_hash)
                VALUES(?,?,?,?,?)
                """,
                (run_id, attempt, payload, int(passed), digest),
            )
            self._complete_task(
                con,
                task,
                {"attempt": attempt, "passed": passed, "check": check},
                output_hash=digest,
            )
            if passed:
                self._transition(
                    con,
                    run_id,
                    RunStage.REPAIRED.value,
                    RunStage.REGRESSION_PASSED.value,
                    f"{run_id}:regression-pass:{attempt}",
                    {"attempt": attempt, "artifact_hash": digest},
                    digest,
                )
            con.commit()
            return {
                "run_id": run_id,
                "attempt": attempt,
                "passed": passed,
                "artifact_hash": digest,
            }

    def record_state_scan(
        self,
        run_id: str,
        observed_delta: dict[str, Any],
        observation: dict[str, Any],
        *,
        model: str,
        reasoning_effort: str,
    ) -> dict[str, Any]:
        StoryDelta.from_dict(observed_delta)
        with open_database(self.root) as con:
            run = self._get_run(con, run_id)
            task = self._require_task(con, run_id, "state-scan")
            if task["model"] != model or task["reasoning_effort"] != reasoning_effort:
                raise ValidationBlocked("state scanner model/effort must match runtime-issued task route")
            repaired = con.execute(
                "SELECT repair_level,prose_hash FROM repaired_drafts WHERE run_id=?",
                (run_id,),
            ).fetchone()
            if not repaired:
                raise StateConflict("final repaired draft is missing")
            allowed = {RunStage.REPAIRED.value, RunStage.REGRESSION_PASSED.value}
            if run["stage"] not in allowed:
                raise StateConflict(f"state scan not permitted at stage {run['stage']}")
            if repaired["repair_level"] != "none" and run["stage"] != RunStage.REGRESSION_PASSED.value:
                raise ValidationBlocked("non-trivial repair must pass regression before state scan")
            payload = {
                "observed_delta": observed_delta,
                "observation": observation,
                "prose_hash": repaired["prose_hash"],
            }
            digest = sha256_text(json_dumps(payload))
            existing = con.execute(
                "SELECT * FROM state_scans WHERE run_id=?", (run_id,)
            ).fetchone()
            if existing:
                if existing["artifact_hash"] == digest:
                    return {**dict(existing), "idempotent": True}
                raise StateConflict("state scan is immutable for a run")
            con.execute(
                """
                INSERT INTO state_scans(
                  run_id,observed_delta_json,observation_json,artifact_hash,model,reasoning_effort
                ) VALUES(?,?,?,?,?,?)
                """,
                (
                    run_id,
                    json_dumps(observed_delta),
                    json_dumps(observation),
                    digest,
                    model,
                    reasoning_effort,
                ),
            )
            self._complete_task(
                con,
                task,
                {
                    "observed_delta": observed_delta,
                    "observation": observation,
                    "prose_hash": repaired["prose_hash"],
                },
                output_hash=digest,
            )
            con.commit()
            return {"run_id": run_id, "artifact_hash": digest, "prose_hash": repaired["prose_hash"]}

    def validate_delta(
        self,
        run_id: str,
        observed_delta: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        with open_database(self.root) as con:
            run = self._get_run(con, run_id)
            if run["stage"] not in {
                RunStage.REPAIRED.value,
                RunStage.REGRESSION_PASSED.value,
                RunStage.DELTA_VALIDATED.value,
            }:
                raise StateConflict(f"delta validation is not permitted at stage {run['stage']}")
            plan = con.execute(
                "SELECT expected_delta_json FROM chapter_plans WHERE chapter_no=?",
                (run["chapter_no"],),
            ).fetchone()
            repaired = con.execute(
                "SELECT declared_delta_json,repair_level FROM repaired_drafts WHERE run_id=?",
                (run_id,),
            ).fetchone()
            scan = con.execute(
                "SELECT observed_delta_json FROM state_scans WHERE run_id=?",
                (run_id,),
            ).fetchone()
            if not plan or not repaired or not scan:
                raise StateConflict(
                    "delta validation requires ExpectedDelta, repaired DeclaredDelta, and independent StateScan"
                )
            scan_delta_raw = json.loads(scan["observed_delta_json"])
            if observed_delta is not None and json_dumps(observed_delta) != json_dumps(scan_delta_raw):
                raise ValidationBlocked(
                    "supplied ObservedDelta does not match the immutable independent StateScan"
                )
            if repaired["repair_level"] != "none":
                regression = con.execute(
                    """
                    SELECT passed FROM regression_checks
                    WHERE run_id=? ORDER BY attempt DESC LIMIT 1
                    """,
                    (run_id,),
                ).fetchone()
                if not regression or not bool(regression["passed"]):
                    raise ValidationBlocked(
                        "non-trivial repair requires a passing regression check before delta validation"
                    )
            expected = StoryDelta.from_dict(json.loads(plan["expected_delta_json"]))
            declared = StoryDelta.from_dict(json.loads(repaired["declared_delta_json"]))
            observed = StoryDelta.from_dict(scan_delta_raw)
            result = compare_deltas(expected, declared, observed)
            con.execute(
                """
                INSERT INTO delta_validations(run_id,observed_delta_json,result_json,passed)
                VALUES(?,?,?,?)
                ON CONFLICT(run_id) DO UPDATE SET
                  observed_delta_json=excluded.observed_delta_json,
                  result_json=excluded.result_json,
                  passed=excluded.passed,
                  validated_at=CURRENT_TIMESTAMP
                """,
                (run_id, json_dumps(scan_delta_raw), json_dumps(result), int(result["passed"])),
            )
            if result["passed"] and run["stage"] != RunStage.DELTA_VALIDATED.value:
                self._transition(
                    con,
                    run_id,
                    run["stage"],
                    RunStage.DELTA_VALIDATED.value,
                    f"{run_id}:delta-pass:{sha256_text(json_dumps(scan_delta_raw))}",
                    {"change_count": len(observed.changes)},
                )
            con.commit()
            return result

    def publish(self, run_id: str) -> dict[str, Any]:
        with open_database(self.root) as con:
            run = self._get_run(con, run_id)
            existing = con.execute(
                "SELECT * FROM chapter_revisions WHERE run_id=?", (run_id,)
            ).fetchone()
            if existing:
                proposals = [
                    dict(row)
                    for row in con.execute(
                        "SELECT * FROM pending_memory WHERE source_run_id=? ORDER BY id",
                        (run_id,),
                    )
                ]
                return {
                    "chapter_revision": dict(existing),
                    "pending_memory": proposals,
                    "idempotent": True,
                }
            if run["stage"] != RunStage.DELTA_VALIDATED.value:
                raise ValidationBlocked("publish requires a passed three-way delta validation")
            validation = con.execute(
                "SELECT * FROM delta_validations WHERE run_id=? AND passed=1", (run_id,)
            ).fetchone()
            if not validation:
                raise ValidationBlocked("passed delta validation record is missing")
            reviewers = {
                row["reviewer"]
                for row in con.execute("SELECT reviewer FROM reviews WHERE run_id=?", (run_id,))
            }
            if reviewers != REQUIRED_REVIEWERS:
                raise ValidationBlocked(
                    f"publish requires all independent reviewers; missing {sorted(REQUIRED_REVIEWERS - reviewers)}"
                )
            final_draft = con.execute(
                "SELECT * FROM repaired_drafts WHERE run_id=?", (run_id,)
            ).fetchone()
            if not final_draft:
                raise StateConflict("repaired/final draft is missing")
            chapter_no = int(run["chapter_no"])
            revrow = con.execute(
                "SELECT COALESCE(MAX(revision),0)+1 AS next FROM chapter_revisions WHERE chapter_no=?",
                (chapter_no,),
            ).fetchone()
            revision = int(revrow["next"])
            revision_id = deterministic_id(
                "chapter-rev", str(chapter_no), str(revision), final_draft["prose_hash"]
            )
            con.execute(
                """
                INSERT INTO chapter_revisions(id,chapter_no,revision,run_id,prose,prose_hash)
                VALUES(?,?,?,?,?,?)
                """,
                (
                    revision_id,
                    chapter_no,
                    revision,
                    run_id,
                    final_draft["prose"],
                    final_draft["prose_hash"],
                ),
            )
            con.execute(
                """
                INSERT INTO publications(chapter_no,chapter_revision_id)
                VALUES(?,?)
                ON CONFLICT(chapter_no) DO UPDATE SET
                  chapter_revision_id=excluded.chapter_revision_id,
                  published_at=CURRENT_TIMESTAMP
                """,
                (chapter_no, revision_id),
            )

            plan = con.execute(
                "SELECT contract_json FROM chapter_plans WHERE chapter_no=?",
                (chapter_no,),
            ).fetchone()
            contract = json.loads(plan["contract_json"]) if plan else {}
            for point in contract.get("read_points", []):
                kind = str(point["kind"])
                key = str(point["key"])
                revision_value = int(
                    point.get("revision")
                    or self._current_entity_revision(con, kind, key)
                )
                con.execute(
                    """
                    INSERT OR IGNORE INTO read_points(
                      chapter_revision_id,entity_kind,entity_key,entity_revision
                    ) VALUES(?,?,?,?)
                    """,
                    (revision_id, kind, key, revision_value),
                )

            observed = StoryDelta.from_dict(json.loads(validation["observed_delta_json"]))
            proposal_ids: list[str] = []
            for change in observed.changes:
                proposal_id = deterministic_id(
                    "mem",
                    run_id,
                    change.kind,
                    change.entity_key,
                    change.field,
                    json_dumps(change.new_value),
                )
                con.execute(
                    """
                    INSERT OR IGNORE INTO pending_memory(
                      id,source_chapter_no,source_run_id,kind,entity_key,field,
                      old_value_json,new_value_json,evidence,effective_chapter
                    ) VALUES(?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        proposal_id,
                        chapter_no,
                        run_id,
                        change.kind,
                        change.entity_key,
                        change.field,
                        json_dumps(change.old_value) if change.old_value is not None else None,
                        json_dumps(change.new_value),
                        change.evidence,
                        change.effective_chapter or chapter_no,
                    ),
                )
                proposal_ids.append(proposal_id)

            self._transition(
                con,
                run_id,
                run["stage"],
                RunStage.PUBLISHED.value,
                f"{run_id}:publish:{revision_id}",
                {
                    "chapter_revision_id": revision_id,
                    "pending_memory_ids": proposal_ids,
                    "memory_authority": "pending-human-approval",
                },
                final_draft["prose_hash"],
            )
            con.execute(
                "UPDATE chapter_runs SET completed_at=CURRENT_TIMESTAMP WHERE id=?", (run_id,)
            )
            projection = rebuild_search_projection(con)
            con.commit()
            return {
                "chapter_revision": {
                    "id": revision_id,
                    "chapter_no": chapter_no,
                    "revision": revision,
                    "prose_hash": final_draft["prose_hash"],
                },
                "pending_memory_ids": proposal_ids,
                "canon_mutated": False,
                "projection": projection,
            }

    def stage_memory(
        self,
        *,
        source_run_id: str,
        source_chapter_no: int,
        kind: str,
        entity_key: str,
        field: str,
        new_value: Any,
        old_value: Any | None = None,
        evidence: str | None = None,
        effective_chapter: int | None = None,
    ) -> dict[str, Any]:
        proposal_id = deterministic_id(
            "mem",
            source_run_id,
            kind,
            entity_key,
            field,
            json_dumps(new_value),
        )
        with open_database(self.root) as con:
            self._get_run(con, source_run_id)
            con.execute(
                """
                INSERT OR IGNORE INTO pending_memory(
                  id,source_chapter_no,source_run_id,kind,entity_key,field,
                  old_value_json,new_value_json,evidence,effective_chapter
                ) VALUES(?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    proposal_id,
                    source_chapter_no,
                    source_run_id,
                    kind,
                    entity_key,
                    field,
                    json_dumps(old_value) if old_value is not None else None,
                    json_dumps(new_value),
                    evidence,
                    effective_chapter or source_chapter_no,
                ),
            )
            con.commit()
            return {"id": proposal_id, "status": "pending"}

    def list_memory(self, status: str = "pending") -> list[dict[str, Any]]:
        if status not in {"pending", "approved", "rejected", "all"}:
            raise ValueError("memory status must be pending|approved|rejected|all")
        with open_database(self.root) as con:
            if status == "all":
                rows = con.execute("SELECT * FROM pending_memory ORDER BY created_at,id")
            else:
                rows = con.execute(
                    "SELECT * FROM pending_memory WHERE status=? ORDER BY created_at,id",
                    (status,),
                )
            return [dict(row) for row in rows]

    def approve_memory(self, proposal_id: str) -> dict[str, Any]:
        with open_database(self.root) as con:
            proposal = con.execute(
                "SELECT * FROM pending_memory WHERE id=?", (proposal_id,)
            ).fetchone()
            if not proposal:
                raise StateConflict("pending memory proposal not found")
            if proposal["status"] == "approved":
                return {"id": proposal_id, "status": "approved", "idempotent": True}
            if proposal["status"] != "pending":
                raise StateConflict(f"cannot approve memory in status {proposal['status']}")
            project = con.execute(
                "SELECT canonical_revision FROM project WHERE id='default'"
            ).fetchone()
            revision = int(project["canonical_revision"]) + 1
            self._apply_memory(con, proposal, revision)
            con.execute(
                "UPDATE pending_memory SET status='approved',decided_at=CURRENT_TIMESTAMP WHERE id=?",
                (proposal_id,),
            )
            con.execute(
                "UPDATE project SET canonical_revision=?,updated_at=CURRENT_TIMESTAMP WHERE id='default'",
                (revision,),
            )
            stale = self._mark_stale_dependents(
                con,
                proposal["kind"],
                proposal["entity_key"],
                int(proposal["effective_chapter"] or proposal["source_chapter_no"]),
                revision,
            )
            rebuild = rebuild_search_projection(con)
            con.commit()
            return {
                "id": proposal_id,
                "status": "approved",
                "canonical_revision": revision,
                "stale_marked": stale,
                "projection": rebuild,
            }

    def reject_memory(self, proposal_id: str) -> dict[str, Any]:
        with open_database(self.root) as con:
            row = con.execute(
                "SELECT status FROM pending_memory WHERE id=?", (proposal_id,)
            ).fetchone()
            if not row:
                raise StateConflict("pending memory proposal not found")
            if row["status"] == "rejected":
                return {"id": proposal_id, "status": "rejected", "idempotent": True}
            if row["status"] != "pending":
                raise StateConflict("approved memory cannot be rejected retroactively")
            con.execute(
                "UPDATE pending_memory SET status='rejected',decided_at=CURRENT_TIMESTAMP WHERE id=?",
                (proposal_id,),
            )
            con.commit()
            return {"id": proposal_id, "status": "rejected"}

    def idea_propose(self, text: str) -> dict[str, Any]:
        if not text.strip():
            raise ValidationBlocked("idea text must not be empty")
        idea_id = new_id("idea")
        with open_database(self.root) as con:
            con.execute(
                "INSERT INTO author_intents(id,idea_text,status) VALUES(?,?,'proposed')",
                (idea_id, text),
            )
            latest = con.execute(
                "SELECT COALESCE(MAX(chapter_no),0) chapter_no FROM publications"
            ).fetchone()
            cutoff = int(latest["chapter_no"])
            task_id = deterministic_id("task", f"idea-review:{idea_id}")
            task = self._issue_project_task(
                con,
                task_id=task_id,
                chapter_no=cutoff,
                stage=f"idea-review:{idea_id}",
                role="novel-story-director",
                route_key="story-director",
                output_schema="IdeaCritique",
                payload={
                    "idea_id": idea_id,
                    "idea_text": text,
                    "context": self._context_compiler(con).compile(
                        query=text,
                        chapter_no=cutoff,
                    ),
                    "required_axes": [
                        "story_fit",
                        "novelty",
                        "motivation_fit",
                        "causality",
                        "setup_debt",
                        "payoff_opportunities",
                        "continuity_conflicts",
                        "reader_information_impact",
                        "opportunity_cost",
                        "stale_blast_radius",
                        "improvements",
                    ],
                    "instruction": (
                        "Critically evaluate the human idea. Do not praise by default. "
                        "Return every required axis with concrete evidence/implications."
                    ),
                },
            )
            con.commit()
        return {
            "id": idea_id,
            "status": "proposed",
            "idea_text": text,
            "review_task": task,
        }

    def idea_review(
        self,
        idea_id: str,
        critique: dict[str, Any],
        *,
        model: str,
        reasoning_effort: str,
    ) -> dict[str, Any]:
        required = {
            "story_fit",
            "novelty",
            "motivation_fit",
            "causality",
            "setup_debt",
            "payoff_opportunities",
            "continuity_conflicts",
            "reader_information_impact",
            "opportunity_cost",
            "stale_blast_radius",
            "improvements",
        }
        missing = sorted(required - set(critique))
        if missing:
            raise ValidationBlocked(f"IdeaCritique missing required axes: {missing}")
        with open_database(self.root) as con:
            row = con.execute("SELECT * FROM author_intents WHERE id=?", (idea_id,)).fetchone()
            if not row:
                raise StateConflict("idea not found")
            if row["status"] in {"accepted", "rejected"}:
                raise StateConflict("accepted/rejected ideas cannot be re-reviewed")
            task_id = deterministic_id("task", f"idea-review:{idea_id}")
            task = con.execute("SELECT * FROM agent_tasks WHERE id=?", (task_id,)).fetchone()
            if not task:
                raise ValidationBlocked("idea critique requires a runtime-issued Story Director task")
            if task["model"] != model or task["reasoning_effort"] != reasoning_effort:
                raise ValidationBlocked("idea review model/effort must match runtime-issued route")
            payload = json_dumps(critique)
            if task["status"] == "completed":
                if row["critique_json"] == payload:
                    return {
                        "id": idea_id,
                        "status": "reviewed",
                        "critique": critique,
                        "idempotent": True,
                    }
                raise StateConflict("idea review task is immutable once completed")
            if task["status"] != "pending":
                raise StateConflict(f"idea review task is {task['status']}")
            con.execute(
                """
                UPDATE author_intents
                SET critique_json=?,status='reviewed',updated_at=CURRENT_TIMESTAMP
                WHERE id=?
                """,
                (payload, idea_id),
            )
            self._complete_task(
                con,
                task,
                critique,
                output_hash=sha256_text(payload),
            )
            con.commit()
            return {"id": idea_id, "status": "reviewed", "critique": critique}

    def idea_decide(self, idea_id: str, accept: bool) -> dict[str, Any]:
        target = "accepted" if accept else "rejected"
        with open_database(self.root) as con:
            row = con.execute("SELECT * FROM author_intents WHERE id=?", (idea_id,)).fetchone()
            if not row:
                raise StateConflict("idea not found")
            if accept and row["status"] != "reviewed":
                raise ValidationBlocked("idea must receive critical review before acceptance")
            if row["status"] == target:
                return {"id": idea_id, "status": target, "idempotent": True}
            if row["status"] in {"accepted", "rejected"}:
                raise StateConflict("idea decision is already final")
            con.execute(
                "UPDATE author_intents SET status=?,updated_at=CURRENT_TIMESTAMP WHERE id=?",
                (target, idea_id),
            )
            projection = rebuild_search_projection(con) if accept else None
            con.commit()
            return {
                "id": idea_id,
                "status": target,
                "canon_mutated": False,
                "namespace": "AuthorIntent",
                "projection": projection,
            }

    def set_preference(
        self, key: str, value: Any, confidence: float, evidence: list[str]
    ) -> dict[str, Any]:
        if not 0 <= confidence <= 1:
            raise ValueError("confidence must be in [0,1]")
        with open_database(self.root) as con:
            con.execute(
                """
                INSERT INTO author_preferences(preference_key,value_json,confidence,evidence_json)
                VALUES(?,?,?,?)
                ON CONFLICT(preference_key) DO UPDATE SET
                  value_json=excluded.value_json,
                  confidence=excluded.confidence,
                  evidence_json=excluded.evidence_json,
                  updated_at=CURRENT_TIMESTAMP
                """,
                (key, json_dumps(value), confidence, json_dumps(evidence)),
            )
            projection = rebuild_search_projection(con)
            con.commit()
            return {
                "key": key,
                "confidence": confidence,
                "namespace": "AuthorPreference",
                "projection": projection,
            }

    def stale_list(self) -> list[dict[str, Any]]:
        with open_database(self.root) as con:
            return [
                dict(row)
                for row in con.execute(
                    """
                    SELECT id,chapter_no,revision,stale_reason
                    FROM chapter_revisions WHERE stale=1 ORDER BY chapter_no,revision
                    """
                )
            ]

    def set_story_compass(self, value: dict[str, Any]) -> dict[str, Any]:
        premise = str(value.get("premise", ""))
        ending = str(value.get("ending_promise", ""))
        conflict = str(value.get("core_conflict", ""))
        themes = value.get("themes", [])
        if not isinstance(themes, list):
            raise ValueError("StoryCompass.themes must be a list")
        with open_database(self.root) as con:
            row = con.execute("SELECT revision FROM story_compass WHERE id=1").fetchone()
            revision = int(row["revision"]) + 1 if row else 1
            con.execute(
                """
                INSERT INTO story_compass(
                  id,premise,ending_promise,core_conflict,themes_json,revision
                ) VALUES(1,?,?,?,?,?)
                ON CONFLICT(id) DO UPDATE SET
                  premise=excluded.premise,
                  ending_promise=excluded.ending_promise,
                  core_conflict=excluded.core_conflict,
                  themes_json=excluded.themes_json,
                  revision=excluded.revision,
                  updated_at=CURRENT_TIMESTAMP
                """,
                (premise, ending, conflict, json_dumps(themes), revision),
            )
            con.commit()
            return {
                "premise": premise,
                "ending_promise": ending,
                "core_conflict": conflict,
                "themes": themes,
                "revision": revision,
            }

    def put_outline(self, kind: str, value: dict[str, Any]) -> dict[str, Any]:
        with open_database(self.root) as con:
            if kind == "volume":
                volume_id = str(value["id"])
                con.execute(
                    """
                    INSERT INTO volumes(id,ordinal,title,goal,status,revision)
                    VALUES(?,?,?,?,?,1)
                    ON CONFLICT(id) DO UPDATE SET
                      ordinal=excluded.ordinal,title=excluded.title,goal=excluded.goal,
                      status=excluded.status,revision=volumes.revision+1
                    """,
                    (
                        volume_id,
                        int(value["ordinal"]),
                        str(value["title"]),
                        str(value.get("goal", "")),
                        str(value.get("status", "planned")),
                    ),
                )
                con.commit()
                return dict(con.execute("SELECT * FROM volumes WHERE id=?", (volume_id,)).fetchone())
            if kind == "arc":
                arc_id = str(value["id"])
                volume_id = value.get("volume_id")
                if volume_id and not con.execute(
                    "SELECT 1 FROM volumes WHERE id=?", (str(volume_id),)
                ).fetchone():
                    raise ValidationBlocked("arc volume_id does not exist")
                con.execute(
                    """
                    INSERT INTO arcs(
                      id,volume_id,ordinal,title,goal,turning_points_json,status,revision
                    ) VALUES(?,?,?,?,?,?,?,1)
                    ON CONFLICT(id) DO UPDATE SET
                      volume_id=excluded.volume_id,ordinal=excluded.ordinal,
                      title=excluded.title,goal=excluded.goal,
                      turning_points_json=excluded.turning_points_json,
                      status=excluded.status,revision=arcs.revision+1
                    """,
                    (
                        arc_id,
                        str(volume_id) if volume_id else None,
                        int(value["ordinal"]),
                        str(value["title"]),
                        str(value.get("goal", "")),
                        json_dumps(value.get("turning_points", [])),
                        str(value.get("status", "planned")),
                    ),
                )
                con.commit()
                return dict(con.execute("SELECT * FROM arcs WHERE id=?", (arc_id,)).fetchone())
            if kind == "rolling":
                anchor = int(value["anchor_chapter"])
                horizon = int(value.get("horizon_chapters", 5))
                if horizon < 1 or horizon > 12:
                    raise ValidationBlocked("rolling outline horizon must be within 1..12 chapters")
                current = con.execute(
                    "SELECT COALESCE(MAX(revision),0) r FROM rolling_outlines"
                ).fetchone()
                revision = int(current["r"]) + 1
                con.execute("UPDATE rolling_outlines SET status='superseded' WHERE status='active'")
                con.execute(
                    """
                    INSERT INTO rolling_outlines(
                      anchor_chapter,horizon_chapters,outline_json,revision,status
                    ) VALUES(?,?,?,?,'active')
                    """,
                    (anchor, horizon, json_dumps(value.get("outline", {})), revision),
                )
                con.commit()
                return {
                    "anchor_chapter": anchor,
                    "horizon_chapters": horizon,
                    "outline": value.get("outline", {}),
                    "revision": revision,
                    "status": "active",
                }
            raise ValueError("outline kind must be volume|arc|rolling")

    def _routing_path(self) -> Path:
        installed = self.root / ".novel" / "config" / "model-routing.json"
        if installed.exists():
            return installed
        source = self.root / "config" / "model-routing.json"
        if source.exists():
            return source
        raise ValidationBlocked("model routing config is missing")

    def _retrieval_path(self) -> Path | None:
        installed = self.root / ".novel" / "config" / "retrieval.json"
        if installed.exists():
            return installed
        source = self.root / "config" / "retrieval.json"
        return source if source.exists() else None

    def _context_compiler(self, con: sqlite3.Connection) -> ContextCompiler:
        config_path = self._retrieval_path()
        provider = None
        if config_path and config_path.exists():
            config = json.loads(config_path.read_text(encoding="utf-8"))
            vector = config.get("vector", {})
            provider = build_provider(
                str(vector.get("provider", "disabled")),
                vector.get("model"),
            )
        return ContextCompiler(con, vector_provider=provider)

    def _resolve_route_config(self, stage: str) -> dict[str, str]:
        config = json.loads(self._routing_path().read_text(encoding="utf-8"))
        if config.get("fallback") != "fail-closed":
            raise ValidationBlocked("model routing fallback must remain fail-closed")
        route = config.get("routes", {}).get(stage)
        if not isinstance(route, dict):
            raise ValidationBlocked(f"no explicit model route for stage: {stage}")
        model = str(route.get("model", ""))
        effort = str(route.get("effort", ""))
        if model not in {"gpt-6-luna", "gpt-6-sol"}:
            raise ValidationBlocked(f"unapproved model route: {model}")
        if effort not in {"none", "low", "medium", "high"}:
            raise ValidationBlocked(f"unapproved reasoning effort: {effort}")
        return {"model": model, "reasoning_effort": effort}

    def resolve_route(self, stage: str, run_id: str | None = None) -> dict[str, Any]:
        route = self._resolve_route_config(stage)
        result = {
            "schema": "novel-model-routing/v1",
            "stage": stage,
            "model": route["model"],
            "reasoning_effort": route["reasoning_effort"],
            "fallback": "fail-closed",
        }
        if run_id:
            with open_database(self.root) as con:
                self._get_run(con, run_id)
                con.execute(
                    """
                    INSERT INTO model_route_decisions(
                      run_id,stage,role,model,reasoning_effort,policy_key
                    ) VALUES(?,?,?,?,?,?)
                    """,
                    (
                        run_id,
                        stage,
                        stage,
                        route["model"],
                        route["reasoning_effort"],
                        stage,
                    ),
                )
                con.commit()
        return result

    def _require_task(
        self,
        con: sqlite3.Connection,
        run_id: str,
        task_stage: str,
        *,
        attempt: int | None = None,
    ) -> sqlite3.Row:
        if attempt is None:
            row = con.execute(
                """
                SELECT * FROM agent_tasks
                WHERE run_id=? AND stage=? AND status='pending'
                ORDER BY attempt DESC LIMIT 1
                """,
                (run_id, task_stage),
            ).fetchone()
        else:
            row = con.execute(
                """
                SELECT * FROM agent_tasks
                WHERE run_id=? AND stage=? AND attempt=? AND status='pending'
                """,
                (run_id, task_stage, attempt),
            ).fetchone()
        if not row:
            raise ValidationBlocked(
                f"no pending runtime-issued task for {task_stage}; call orchestrate-next"
            )
        return row

    def _complete_task(
        self,
        con: sqlite3.Connection,
        task: sqlite3.Row,
        output: dict[str, Any],
        *,
        output_hash: str | None = None,
    ) -> None:
        digest = output_hash or sha256_text(json_dumps(output))
        cur = con.execute(
            """
            UPDATE agent_tasks
            SET status='completed',output_json=?,output_hash=?,completed_at=CURRENT_TIMESTAMP
            WHERE id=? AND status='pending'
            """,
            (json_dumps(output), digest, task["id"]),
        )
        if cur.rowcount != 1:
            raise StateConflict("agent task completion lost its pending fence")

    def _issue_project_task(
        self,
        con: sqlite3.Connection,
        *,
        task_id: str,
        chapter_no: int,
        stage: str,
        role: str,
        route_key: str,
        output_schema: str,
        payload: dict[str, Any],
        attempt: int = 1,
    ) -> dict[str, Any]:
        route = self._resolve_route_config(route_key)
        existing = con.execute("SELECT * FROM agent_tasks WHERE id=?", (task_id,)).fetchone()
        if not existing:
            con.execute(
                """
                INSERT INTO agent_tasks(
                  id,run_id,chapter_no,stage,role,route_key,model,reasoning_effort,
                  input_json,output_schema,status,attempt
                ) VALUES(?,NULL,?,?,?,?,?,?,?,?,'pending',?)
                """,
                (
                    task_id,
                    chapter_no,
                    stage,
                    role,
                    route_key,
                    route["model"],
                    route["reasoning_effort"],
                    json_dumps(payload),
                    output_schema,
                    attempt,
                ),
            )
            con.execute(
                """
                INSERT INTO model_route_decisions(
                  run_id,stage,role,model,reasoning_effort,policy_key
                ) VALUES(NULL,?,?,?,?,?)
                """,
                (
                    stage,
                    role,
                    route["model"],
                    route["reasoning_effort"],
                    route_key,
                ),
            )
            existing = con.execute("SELECT * FROM agent_tasks WHERE id=?", (task_id,)).fetchone()
        return {
            "task_id": task_id,
            "chapter_no": chapter_no,
            "stage": stage,
            "role": role,
            "model": route["model"],
            "reasoning_effort": route["reasoning_effort"],
            "output_schema": output_schema,
            "input": payload,
            "status": existing["status"],
            "attempt": int(existing["attempt"]),
        }

    def workflow_next(self, chapter_no: int) -> dict[str, Any]:
        with open_database(self.root) as con:
            active = con.execute(
                """
                SELECT id FROM chapter_runs
                WHERE chapter_no=? AND completed_at IS NULL
                ORDER BY started_at DESC LIMIT 1
                """,
                (chapter_no,),
            ).fetchone()
            if active:
                run_id = str(active["id"])
            else:
                run_id = None
                plan = con.execute(
                    "SELECT * FROM chapter_plans WHERE chapter_no=?", (chapter_no,)
                ).fetchone()
                project = con.execute(
                    "SELECT approval_mode FROM project WHERE id='default'"
                ).fetchone()
                if plan:
                    if plan["status"] == "generated":
                        if project["approval_mode"] == "every_chapter":
                            return {
                                "chapter_no": chapter_no,
                                "human_gate": "chapter-plan-approval",
                                "plan": {
                                    "title": plan["title"],
                                    "plan": json.loads(plan["plan_json"]),
                                    "contract": json.loads(plan["contract_json"]),
                                    "expected_delta": json.loads(plan["expected_delta_json"]),
                                    "revision": plan["revision"],
                                },
                            }
                        return {"chapter_no": chapter_no, "runtime_action": "start-run-auto"}
                    if plan["status"] == "approved":
                        return {
                            "chapter_no": chapter_no,
                            "runtime_action": (
                                "start-run-auto"
                                if project["approval_mode"] == "autonomous"
                                else "start-run-manual"
                            ),
                        }

                compass = con.execute("SELECT * FROM story_compass WHERE id=1").fetchone()
                rolling = con.execute(
                    "SELECT * FROM rolling_outlines WHERE status='active' ORDER BY revision DESC LIMIT 1"
                ).fetchone()
                replan = [
                    dict(row)
                    for row in con.execute(
                        """
                        SELECT * FROM replan_requests
                        WHERE status='pending' AND anchor_chapter <= ?
                        ORDER BY anchor_chapter,created_at
                        """,
                        (chapter_no,),
                    )
                ]
                base = {
                    "chapter_no": chapter_no,
                    "story_compass": dict(compass) if compass else None,
                    "rolling_outline": (
                        {**dict(rolling), "outline": json.loads(rolling["outline_json"])}
                        if rolling else None
                    ),
                    "pending_replan_requests": replan,
                    "context": self._context_compiler(con).compile(
                        query=f"{chapter_no}장 계획 갈등 인물 세계관",
                        chapter_no=max(chapter_no - 1, 0),
                    ),
                }
                director_id = deterministic_id("task", f"planning-director:{chapter_no}")
                director_task = self._issue_project_task(
                    con,
                    task_id=director_id,
                    chapter_no=chapter_no,
                    stage=f"planning:director:ch{chapter_no}",
                    role="novel-story-director",
                    route_key="story-director",
                    output_schema="PlanningDispatch",
                    payload={
                        **base,
                        "instruction": (
                            "Select only necessary specialists among "
                            "plot-architect, world-builder, character-agent."
                        ),
                    },
                )
                director_row = con.execute(
                    "SELECT status,output_json FROM agent_tasks WHERE id=?", (director_id,)
                ).fetchone()
                if director_row["status"] != "completed":
                    con.commit()
                    return {"chapter_no": chapter_no, "tasks": [director_task]}

                dispatch = json.loads(director_row["output_json"])
                roles = [str(x) for x in dispatch.get("roles", [])]
                allowed = {"plot-architect", "world-builder", "character-agent"}
                if any(role not in allowed for role in roles):
                    raise ValidationBlocked("PlanningDispatch contains unsupported specialist")
                route_map = {
                    "plot-architect": "plot-architect-rolling",
                    "world-builder": "world-builder",
                    "character-agent": "character-agent",
                }
                role_map = {
                    "plot-architect": "novel-plot-architect",
                    "world-builder": "novel-world-builder",
                    "character-agent": "novel-character-agent",
                }
                pending: list[dict[str, Any]] = []
                for role in roles:
                    task_id = deterministic_id("task", f"planning:{role}:{chapter_no}")
                    task = self._issue_project_task(
                        con,
                        task_id=task_id,
                        chapter_no=chapter_no,
                        stage=f"planning:{role}:ch{chapter_no}",
                        role=role_map[role],
                        route_key=route_map[role],
                        output_schema="PlanningSpecialistProposal",
                        payload={**base, "director_dispatch": dispatch, "specialty": role},
                    )
                    if task["status"] != "completed":
                        pending.append(task)
                if pending:
                    con.commit()
                    return {"chapter_no": chapter_no, "tasks": pending}

                specialist_outputs = []
                for role in roles:
                    task_id = deterministic_id("task", f"planning:{role}:{chapter_no}")
                    row = con.execute(
                        "SELECT role,output_json FROM agent_tasks WHERE id=?", (task_id,)
                    ).fetchone()
                    specialist_outputs.append(
                        {"role": row["role"], "output": json.loads(row["output_json"])}
                    )

                planner_id = deterministic_id("task", f"planning:chapter:{chapter_no}")
                planner_task = self._issue_project_task(
                    con,
                    task_id=planner_id,
                    chapter_no=chapter_no,
                    stage=f"planning:chapter:ch{chapter_no}",
                    role="novel-chapter-planner",
                    route_key="chapter-planner",
                    output_schema="ChapterPlan+ChapterContract+ExpectedDelta",
                    payload={
                        **base,
                        "director_dispatch": dispatch,
                        "specialist_outputs": specialist_outputs,
                    },
                )
                con.commit()
                if planner_task["status"] != "completed":
                    return {"chapter_no": chapter_no, "tasks": [planner_task]}
        if run_id:
            return self.orchestrate_next(run_id)
        return self.workflow_next(chapter_no)

    def complete_planning_task(
        self,
        task_id: str,
        output: dict[str, Any],
    ) -> dict[str, Any]:
        with open_database(self.root) as con:
            task = con.execute("SELECT * FROM agent_tasks WHERE id=?", (task_id,)).fetchone()
            if not task:
                raise StateConflict("planning task not found")
            stage = str(task["stage"])
            if not stage.startswith("planning:"):
                raise ValidationBlocked("task is not a planning task")
            if task["status"] == "completed":
                return {**dict(task), "idempotent": True}
            if ":director:" in stage:
                roles = output.get("roles", [])
                if not isinstance(roles, list):
                    raise ValidationBlocked("PlanningDispatch.roles must be a list")
                allowed = {"plot-architect", "world-builder", "character-agent"}
                normalized = {
                    "roles": [str(role) for role in roles],
                    "reason": str(output.get("reason", "")),
                }
                if any(role not in allowed for role in normalized["roles"]):
                    raise ValidationBlocked("PlanningDispatch requested unsupported role")
            elif ":chapter:" in stage:
                required = {"title", "plan", "contract", "expected_delta"}
                missing = sorted(required - set(output))
                if missing:
                    raise ValidationBlocked(f"chapter planner output missing {missing}")
                chapter_no = int(task["chapter_no"])
                contract = dict(output["contract"])
                expected = dict(output["expected_delta"])
                ChapterContract.from_dict(contract)
                StoryDelta.from_dict(expected)
                existing = con.execute(
                    "SELECT revision,status FROM chapter_plans WHERE chapter_no=?",
                    (chapter_no,),
                ).fetchone()
                if existing and existing["status"] == "approved":
                    raise StateConflict("approved chapter plan cannot be overwritten")
                revision = 1 if not existing else int(existing["revision"]) + 1
                con.execute(
                    """
                    INSERT INTO chapter_plans(
                      chapter_no,title,plan_json,contract_json,expected_delta_json,status,revision
                    ) VALUES(?,?,?,?,?,'generated',?)
                    ON CONFLICT(chapter_no) DO UPDATE SET
                      title=excluded.title,plan_json=excluded.plan_json,
                      contract_json=excluded.contract_json,
                      expected_delta_json=excluded.expected_delta_json,
                      status='generated',approval_kind=NULL,approved_at=NULL,
                      revision=excluded.revision,updated_at=CURRENT_TIMESTAMP
                    """,
                    (
                        chapter_no,
                        str(output["title"]),
                        json_dumps(output["plan"]),
                        json_dumps(contract),
                        json_dumps(expected),
                        revision,
                    ),
                )
                normalized = {
                    "chapter_no": chapter_no,
                    "title": str(output["title"]),
                    "plan_revision": revision,
                }
            else:
                normalized = output
            self._complete_task(con, task, normalized)
            con.commit()
            return {"task_id": task_id, "stage": stage, "output": normalized}

    def orchestrate_next(self, run_id: str) -> dict[str, Any]:
        with open_database(self.root) as con:
            run = self._get_run(con, run_id)
            stage = str(run["stage"])
            if stage == RunStage.PREFLIGHT_FAILED.value:
                return {
                    "run_id": run_id,
                    "stage": stage,
                    "runtime_action": "abort-run",
                    "reason": "one or more candidate hard preflights failed",
                }
            if stage == RunStage.ABORTED.value:
                return {"run_id": run_id, "stage": stage, "terminal": True}
            if stage == RunStage.PREFLIGHT_PASSED.value:
                pair_rows = list(
                    con.execute(
                        """
                        SELECT stage,output_json,status FROM agent_tasks
                        WHERE run_id=? AND stage LIKE 'tournament:%'
                          AND stage != 'tournament-hard'
                        ORDER BY stage
                        """,
                        (run_id,),
                    )
                )
                if len(pair_rows) == 6 and all(row["status"] == "completed" for row in pair_rows):
                    pairwise = {
                        row["stage"].split(":", 1)[1]: json.loads(row["output_json"])
                        for row in pair_rows
                    }
                    aggregation = aggregate_tournament(run_id, pairwise)
                    if not aggregation["hard_adjudication_required"]:
                        return {
                            "run_id": run_id,
                            "stage": stage,
                            "runtime_action": "tournament-select",
                            "winner_lens": aggregation["condorcet_lens"],
                            "aggregation": aggregation,
                        }
                    hard = con.execute(
                        """
                        SELECT * FROM agent_tasks
                        WHERE run_id=? AND stage='tournament-hard' AND attempt=1
                        """,
                        (run_id,),
                    ).fetchone()
                    route = self._resolve_route_config("tournament-hard")
                    candidates = {
                        row["lens"]: row["prose"]
                        for row in con.execute(
                            "SELECT lens,prose FROM chapter_candidates WHERE run_id=?",
                            (run_id,),
                        )
                    }
                    packets = prepare_packets(run_id, candidates)
                    payload = {
                        "run_id": run_id,
                        "aggregation": {
                            k: v for k, v in aggregation.items()
                            if k != "alias_to_lens"
                        },
                        "candidates": {
                            alias: candidates[lens]
                            for alias, lens in alias_mapping(run_id).items()
                        },
                        "instruction": (
                            "Resolve the ambiguous tournament. Choose winner_alias only from P/Q/R. "
                            "Do not infer or request the hidden A/B/C lens."
                        ),
                    }
                    if not hard:
                        task_id = deterministic_id(
                            "task", run_id, "tournament-hard", "novel-story-director", "1"
                        )
                        con.execute(
                            """
                            INSERT INTO agent_tasks(
                              id,run_id,chapter_no,stage,role,route_key,model,reasoning_effort,
                              input_json,output_schema,status,attempt
                            ) VALUES(?,?,?,?,?,?,?,?,?,?,'pending',1)
                            """,
                            (
                                task_id, run_id, int(run["chapter_no"]), "tournament-hard",
                                "novel-story-director", "tournament-hard",
                                route["model"], route["reasoning_effort"],
                                json_dumps(payload), "HardTournamentAdjudication"
                            ),
                        )
                        con.execute(
                            """
                            INSERT INTO model_route_decisions(
                              run_id,stage,role,model,reasoning_effort,policy_key
                            ) VALUES(?,?,?,?,?,?)
                            """,
                            (
                                run_id,"tournament-hard","novel-story-director",
                                route["model"],route["reasoning_effort"],"tournament-hard"
                            ),
                        )
                        hard = con.execute(
                            "SELECT * FROM agent_tasks WHERE id=?", (task_id,)
                        ).fetchone()
                        con.commit()
                    if hard["status"] == "completed":
                        output = json.loads(hard["output_json"])
                        mapping = alias_mapping(run_id)
                        winner_alias = str(output["winner_alias"])
                        return {
                            "run_id": run_id,
                            "stage": stage,
                            "runtime_action": "tournament-select-hard",
                            "winner_lens": mapping[winner_alias],
                            "winner_alias": winner_alias,
                            "aggregation": aggregation,
                            "hard_adjudication": output,
                        }
                    return {
                        "run_id": run_id,
                        "stage": stage,
                        "tasks": [{
                            "task_id": hard["id"],
                            "stage": "tournament-hard",
                            "role": hard["role"],
                            "model": hard["model"],
                            "reasoning_effort": hard["reasoning_effort"],
                            "output_schema": hard["output_schema"],
                            "input": json.loads(hard["input_json"]),
                            "attempt": 1,
                        }],
                    }
            if stage == RunStage.REVIEWED.value:
                hard_review = con.execute(
                    """
                    SELECT * FROM agent_tasks
                    WHERE run_id=? AND stage='review-adjudication-hard' AND attempt=1
                    """,
                    (run_id,),
                ).fetchone()
                if hard_review:
                    if hard_review["status"] == "completed":
                        return {
                            "run_id": run_id,
                            "stage": stage,
                            "runtime_action": "finalize-review-hard",
                            "task_id": hard_review["id"],
                        }
                    return {
                        "run_id": run_id,
                        "stage": stage,
                        "tasks": [{
                            "task_id": hard_review["id"],
                            "stage": hard_review["stage"],
                            "role": hard_review["role"],
                            "model": hard_review["model"],
                            "reasoning_effort": hard_review["reasoning_effort"],
                            "output_schema": hard_review["output_schema"],
                            "input": json.loads(hard_review["input_json"]),
                            "attempt": hard_review["attempt"],
                        }],
                    }
            blueprints = blueprints_for_stage(stage)
            if stage == RunStage.REPAIRED.value:
                repaired = con.execute(
                    "SELECT repair_level FROM repaired_drafts WHERE run_id=?", (run_id,)
                ).fetchone()
                if repaired and repaired["repair_level"] == "none":
                    blueprints = [bp for bp in blueprints if bp.task_stage == "state-scan"]
                else:
                    latest = con.execute(
                        "SELECT attempt,passed FROM regression_checks WHERE run_id=? ORDER BY attempt DESC LIMIT 1",
                        (run_id,),
                    ).fetchone()
                    if latest and not bool(latest["passed"]) and int(latest["attempt"]) >= 3:
                        raise ValidationBlocked("regression retry budget exhausted")
                    blueprints = [bp for bp in blueprints if bp.task_stage == "regression"]

            plan = con.execute(
                "SELECT * FROM chapter_plans WHERE chapter_no=?", (run["chapter_no"],)
            ).fetchone()
            contract = json.loads(plan["contract_json"]) if plan else {}
            plan_json = json.loads(plan["plan_json"]) if plan else {}
            query = " ".join(
                [str(plan["title"]) if plan else "", *map(str, contract.get("required_beats", []))]
            ).strip() or f"{run['chapter_no']}장"
            context = self._context_compiler(con).compile(
                query=query,
                chapter_no=int(run["chapter_no"]),
            )
            packets = None
            if stage == RunStage.PREFLIGHT_PASSED.value:
                candidates = {
                    row["lens"]: row["prose"]
                    for row in con.execute(
                        "SELECT lens,prose FROM chapter_candidates WHERE run_id=?",
                        (run_id,),
                    )
                }
                packets = prepare_packets(run_id, candidates)

            envelopes: list[dict[str, Any]] = []
            for bp in blueprints:
                attempt = 1
                if bp.task_stage == "regression":
                    row = con.execute(
                        "SELECT COALESCE(MAX(attempt),0)+1 AS next FROM agent_tasks WHERE run_id=? AND stage='regression'",
                        (run_id,),
                    ).fetchone()
                    attempt = int(row["next"])
                existing = con.execute(
                    """
                    SELECT * FROM agent_tasks
                    WHERE run_id=? AND stage=? AND role=? AND attempt=?
                    """,
                    (run_id, bp.task_stage, bp.role, attempt),
                ).fetchone()
                if existing and existing["status"] == "completed":
                    continue
                route = self._resolve_route_config(bp.route_key)
                payload: dict[str, Any] = {
                    "chapter_no": int(run["chapter_no"]),
                    "run_id": run_id,
                    "run_stage": stage,
                    "plan": plan_json,
                    "contract": contract,
                    "context": context,
                    **bp.input_hint,
                }
                if bp.task_stage.startswith("write:"):
                    lens = bp.task_stage.split(":", 1)[1]
                    payload["strategy"] = LENS_STRATEGIES[lens]
                elif bp.task_stage.startswith("preflight:"):
                    lens = bp.task_stage.split(":", 1)[1]
                    candidate = con.execute(
                        "SELECT prose,prose_hash FROM chapter_candidates WHERE run_id=? AND lens=?",
                        (run_id, lens),
                    ).fetchone()
                    payload["candidate"] = dict(candidate)
                elif bp.task_stage.startswith("tournament:"):
                    comparison_id = bp.task_stage.split(":", 1)[1]
                    payload = {
                        "run_id": run_id,
                        "pair": next(
                            x for x in packets["pairs"] if x["comparison_id"] == comparison_id
                        ),
                    }
                elif bp.task_stage.startswith("review:"):
                    frozen = con.execute(
                        "SELECT prose,prose_hash FROM frozen_drafts WHERE run_id=?", (run_id,)
                    ).fetchone()
                    payload["frozen_draft"] = dict(frozen)
                    if bp.task_stage == "review:reader":
                        payload["context"] = self._context_compiler(con).compile_reader(
                            query=query,
                            chapter_no=max(0, int(run["chapter_no"]) - 1),
                        )
                elif bp.task_stage == "review-adjudication":
                    payload["reviews"] = [
                        {
                            "reviewer": row["reviewer"],
                            "review": json.loads(row["review_json"]),
                            "artifact_hash": row["artifact_hash"],
                        }
                        for row in con.execute(
                            "SELECT reviewer,review_json,artifact_hash FROM reviews WHERE run_id=? ORDER BY reviewer",
                            (run_id,),
                        )
                    ]
                elif bp.task_stage == "repair":
                    frozen = con.execute(
                        "SELECT prose,prose_hash FROM frozen_drafts WHERE run_id=?", (run_id,)
                    ).fetchone()
                    adj = con.execute(
                        "SELECT decision_json,conflicts_json,cross_exam_json FROM review_adjudications WHERE run_id=?",
                        (run_id,),
                    ).fetchone()
                    payload["frozen_draft"] = dict(frozen)
                    payload["adjudication"] = {
                        "decision": json.loads(adj["decision_json"]),
                        "conflicts": json.loads(adj["conflicts_json"]),
                        "cross_exam": json.loads(adj["cross_exam_json"]) if adj["cross_exam_json"] else None,
                    }
                elif bp.task_stage in {"regression", "state-scan"}:
                    repaired = con.execute(
                        "SELECT prose,prose_hash,repair_level FROM repaired_drafts WHERE run_id=?",
                        (run_id,),
                    ).fetchone()
                    payload["final_draft"] = dict(repaired)
                    if bp.task_stage == "state-scan":
                        payload.pop("plan", None)

                task_id = deterministic_id("task", run_id, bp.task_stage, bp.role, str(attempt))
                if not existing:
                    con.execute(
                        """
                        INSERT INTO agent_tasks(
                          id,run_id,chapter_no,stage,role,route_key,model,reasoning_effort,
                          input_json,output_schema,status,attempt
                        ) VALUES(?,?,?,?,?,?,?,?,?,?,'pending',?)
                        """,
                        (
                            task_id,
                            run_id,
                            int(run["chapter_no"]),
                            bp.task_stage,
                            bp.role,
                            bp.route_key,
                            route["model"],
                            route["reasoning_effort"],
                            json_dumps(payload),
                            bp.output_schema,
                            attempt,
                        ),
                    )
                    con.execute(
                        """
                        INSERT INTO model_route_decisions(
                          run_id,stage,role,model,reasoning_effort,policy_key
                        ) VALUES(?,?,?,?,?,?)
                        """,
                        (
                            run_id,
                            bp.task_stage,
                            bp.role,
                            route["model"],
                            route["reasoning_effort"],
                            bp.route_key,
                        ),
                    )
                envelopes.append(
                    {
                        "task_id": task_id if not existing else existing["id"],
                        "stage": bp.task_stage,
                        "role": bp.role,
                        "model": route["model"],
                        "reasoning_effort": route["reasoning_effort"],
                        "output_schema": bp.output_schema,
                        "input": payload,
                        "attempt": attempt,
                    }
                )
            con.commit()
            if not envelopes:
                if stage == RunStage.DELTA_VALIDATED.value:
                    return {"run_id": run_id, "stage": stage, "runtime_action": "publish"}
                if stage == RunStage.PUBLISHED.value:
                    return {"run_id": run_id, "stage": stage, "human_gate": "pending-memory-review"}
            return {"run_id": run_id, "stage": stage, "tasks": envelopes}

    def complete_pairwise_task(
        self,
        task_id: str,
        result: dict[str, Any],
    ) -> dict[str, Any]:
        with open_database(self.root) as con:
            task = con.execute("SELECT * FROM agent_tasks WHERE id=?", (task_id,)).fetchone()
            if not task:
                raise StateConflict("agent task not found")
            if not str(task["stage"]).startswith("tournament:"):
                raise ValidationBlocked("generic completion only supports tournament pair tasks")
            if task["status"] == "completed":
                return {**dict(task), "idempotent": True}
            comparison = str(task["stage"]).split(":", 1)[1]
            first, second = comparison.split("-")
            preferred = str(result.get("preferred", ""))
            confidence = float(result.get("confidence", -1))
            if preferred not in {first, second} or not 0 <= confidence <= 1:
                raise ValidationBlocked("invalid PairwisePreference output")
            normalized = {
                "preferred": preferred,
                "confidence": confidence,
                "reason": str(result.get("reason", "")),
            }
            self._complete_task(con, task, normalized)
            con.commit()
            return {"task_id": task_id, "stage": task["stage"], "result": normalized}

    def complete_tournament_hard_task(
        self,
        task_id: str,
        result: dict[str, Any],
    ) -> dict[str, Any]:
        with open_database(self.root) as con:
            task = con.execute("SELECT * FROM agent_tasks WHERE id=?", (task_id,)).fetchone()
            if not task or task["stage"] != "tournament-hard":
                raise ValidationBlocked("task is not a tournament-hard task")
            if task["status"] == "completed":
                return {**dict(task), "idempotent": True}
            winner_alias = str(result.get("winner_alias", ""))
            if winner_alias not in {"P", "Q", "R"}:
                raise ValidationBlocked("hard tournament winner_alias must be P, Q, or R")
            reason = str(result.get("reason", "")).strip()
            if not reason:
                raise ValidationBlocked("hard tournament adjudication requires a reason")
            normalized = {"winner_alias": winner_alias, "reason": reason}
            self._complete_task(con, task, normalized)
            con.commit()
            return {"task_id": task_id, "result": normalized}

    def collected_pairwise(self, run_id: str) -> dict[str, Any]:
        with open_database(self.root) as con:
            rows = list(
                con.execute(
                    """
                    SELECT stage,output_json FROM agent_tasks
                    WHERE run_id=? AND stage LIKE 'tournament:%' AND status='completed'
                    ORDER BY stage
                    """,
                    (run_id,),
                )
            )
            if len(rows) != 6:
                raise ValidationBlocked("all six tournament tasks must complete")
            return {
                row["stage"].split(":", 1)[1]: json.loads(row["output_json"])
                for row in rows
            }

    def preflight_check(
        self,
        *,
        chapter_no: int,
        observation: dict[str, Any],
        contract: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        with open_database(self.root) as con:
            if contract is None:
                row = con.execute(
                    "SELECT contract_json FROM chapter_plans WHERE chapter_no=?",
                    (chapter_no,),
                ).fetchone()
                if not row:
                    raise StateConflict("chapter plan/contract not found")
                contract = json.loads(row["contract_json"])
            return validate_structured_continuity(
                con,
                chapter_no=chapter_no,
                observation=observation,
                contract=ChapterContract.from_dict(contract),
            )

    def migrate(self) -> dict[str, Any]:
        applied = migrate_database(self.root)
        return {
            "schema_version": SCHEMA_VERSION,
            "applied": applied,
            "migrated": bool(applied),
        }

    def quality_eval(
        self,
        text: str,
        narrative_observation: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        with open_database(self.root) as con:
            row = con.execute(
                "SELECT profile_json FROM style_profiles WHERE active=1 "
                "ORDER BY created_at DESC LIMIT 1"
            ).fetchone()
            profile = json.loads(row["profile_json"]) if row else None
        return audit_prose(
            text,
            style_profile=profile,
            narrative_observation=narrative_observation,
        )

    def benchmark_writer_effort(
        self,
        none_rows: list[dict[str, Any]],
        low_rows: list[dict[str, Any]],
    ) -> dict[str, Any]:
        return compare_writer_efforts(none_rows, low_rows)

    def create_style_profile(self, name: str, sample_text: str) -> dict[str, Any]:
        profile = extract_style_profile(sample_text)
        profile_id = deterministic_id("style", name, profile["sample_hash"])
        with open_database(self.root) as con:
            con.execute("UPDATE style_profiles SET active=0 WHERE active=1")
            con.execute(
                """
                INSERT OR IGNORE INTO style_profiles(id,name,profile_json,sample_hash,active)
                VALUES(?,?,?,?,1)
                """,
                (profile_id, name, json_dumps(profile), profile["sample_hash"]),
            )
            con.commit()
        return {"id": profile_id, "name": name, "profile": profile, "active": True}

    def list_style_profiles(self) -> list[dict[str, Any]]:
        with open_database(self.root) as con:
            return [
                {
                    **dict(row),
                    "profile": json.loads(row["profile_json"]),
                }
                for row in con.execute(
                    "SELECT * FROM style_profiles ORDER BY active DESC,created_at DESC"
                )
            ]

    def compare_style(self, text: str) -> dict[str, Any]:
        with open_database(self.root) as con:
            row = con.execute(
                "SELECT profile_json FROM style_profiles WHERE active=1 ORDER BY created_at DESC LIMIT 1"
            ).fetchone()
            if not row:
                raise ValidationBlocked("no active author style profile")
            return compare_to_profile(text, json.loads(row["profile_json"]))

    def put_voice_bible(
        self,
        character_key: str,
        relationship_key: str,
        profile: dict[str, Any],
    ) -> dict[str, Any]:
        allowed_registers = {"formal_polite", "polite", "banmal", "mixed", "contextual"}
        baseline = str(profile.get("baseline_register", "contextual"))
        if baseline not in allowed_registers:
            raise ValidationBlocked("invalid Korean baseline_register")
        addresses = profile.get("address_forms", [])
        if not isinstance(addresses, list):
            raise ValidationBlocked("voice bible address_forms must be a list")
        with open_database(self.root) as con:
            row = con.execute(
                """
                SELECT COALESCE(MAX(revision),0)+1 AS next
                FROM voice_bibles WHERE character_key=? AND relationship_key=?
                """,
                (character_key, relationship_key),
            ).fetchone()
            revision = int(row["next"])
            con.execute(
                """
                UPDATE voice_bibles SET active=0
                WHERE character_key=? AND relationship_key=? AND active=1
                """,
                (character_key, relationship_key),
            )
            con.execute(
                """
                INSERT INTO voice_bibles(
                  character_key,relationship_key,profile_json,revision,active
                ) VALUES(?,?,?,?,1)
                """,
                (character_key, relationship_key, json_dumps(profile), revision),
            )
            con.commit()
            return {
                "character_key": character_key,
                "relationship_key": relationship_key,
                "revision": revision,
                "profile": profile,
            }

    def sandbox_next(
        self,
        chapter_no: int,
        character_key: str,
    ) -> dict[str, Any]:
        if chapter_no < 1:
            raise ValueError("sandbox chapter must be positive")
        if not character_key.strip():
            raise ValidationBlocked("sandbox character_key must not be empty")
        stage = f"sandbox:{chapter_no}:{character_key}"
        with open_database(self.root) as con:
            existing = con.execute(
                """
                SELECT * FROM agent_tasks
                WHERE run_id IS NULL AND stage=? AND role='novel-character-agent'
                ORDER BY attempt DESC LIMIT 1
                """,
                (stage,),
            ).fetchone()
            if existing and existing["status"] == "pending":
                return {
                    "task_id": existing["id"],
                    "chapter_no": chapter_no,
                    "stage": stage,
                    "role": existing["role"],
                    "model": existing["model"],
                    "reasoning_effort": existing["reasoning_effort"],
                    "output_schema": existing["output_schema"],
                    "input": json.loads(existing["input_json"]),
                    "status": existing["status"],
                    "attempt": int(existing["attempt"]),
                }
            attempt = 1 if not existing else int(existing["attempt"]) + 1
            task_id = deterministic_id(
                "task",
                f"sandbox:{chapter_no}:{character_key}:attempt:{attempt}",
            )
            knowledge = NarrativeGraph(con).character_knowledge(
                character_key, max(chapter_no - 1, 0)
            )
            rolling = con.execute(
                "SELECT outline_json,revision FROM rolling_outlines "
                "WHERE status='active' ORDER BY revision DESC LIMIT 1"
            ).fetchone()
            task = self._issue_project_task(
                con,
                task_id=task_id,
                chapter_no=chapter_no,
                stage=stage,
                role="novel-character-agent",
                route_key="character-sandbox",
                output_schema="CharacterSimulation",
                payload={
                    "chapter_no": chapter_no,
                    "character_key": character_key,
                    "context": self._context_compiler(con).compile(
                        query=f"{character_key} 욕망 관계 갈등 기억",
                        focus_nodes=[f"character:{character_key}"],
                        chapter_no=max(chapter_no - 1, 0),
                    ),
                    "knowledge": knowledge,
                    "rolling_outline": (
                        {
                            "revision": rolling["revision"],
                            "outline": json.loads(rolling["outline_json"]),
                        }
                        if rolling
                        else None
                    ),
                    "required_fields": [
                        "perception",
                        "memory",
                        "intention",
                        "action",
                        "consequence",
                    ],
                },
                attempt=attempt,
            )
            con.commit()
            return task

    def character_sandbox(
        self,
        chapter_no: int,
        character_key: str,
        simulation: dict[str, Any],
        *,
        task_id: str,
        model: str,
        reasoning_effort: str,
    ) -> dict[str, Any]:
        required = {"perception", "memory", "intention", "action", "consequence"}
        missing = sorted(required - set(simulation))
        if missing:
            raise ValidationBlocked(f"character sandbox missing fields: {missing}")
        simulation_id = deterministic_id(
            "sim",
            str(chapter_no),
            character_key,
            json_dumps(simulation),
        )
        with open_database(self.root) as con:
            task = con.execute("SELECT * FROM agent_tasks WHERE id=?", (task_id,)).fetchone()
            expected_stage = f"sandbox:{chapter_no}:{character_key}"
            if not task or task["run_id"] is not None or task["stage"] != expected_stage:
                raise ValidationBlocked("sandbox simulation requires its runtime-issued project task")
            if task["role"] != "novel-character-agent":
                raise ValidationBlocked("sandbox task role mismatch")
            if task["model"] != model or task["reasoning_effort"] != reasoning_effort:
                raise ValidationBlocked("sandbox model/effort must match runtime-issued route")
            existing = con.execute(
                "SELECT * FROM character_simulations WHERE id=?", (simulation_id,)
            ).fetchone()
            if task["status"] == "completed":
                if existing:
                    return {
                        "id": simulation_id,
                        "chapter_no": chapter_no,
                        "character_key": character_key,
                        "simulation": simulation,
                        "accepted": bool(existing["accepted"]),
                        "idempotent": True,
                    }
                raise StateConflict("completed sandbox task is missing its simulation")
            if task["status"] != "pending":
                raise StateConflict(f"sandbox task is {task['status']}")
            con.execute(
                """
                INSERT INTO character_simulations(
                  id,chapter_no,character_key,perception_json,memory_json,intention_json,
                  action_json,consequence_json
                ) VALUES(?,?,?,?,?,?,?,?)
                """,
                (
                    simulation_id,
                    chapter_no,
                    character_key,
                    json_dumps(simulation["perception"]),
                    json_dumps(simulation["memory"]),
                    json_dumps(simulation["intention"]),
                    json_dumps(simulation["action"]),
                    json_dumps(simulation["consequence"]),
                ),
            )
            self._complete_task(
                con,
                task,
                {"simulation_id": simulation_id, **simulation},
                output_hash=sha256_text(json_dumps(simulation)),
            )
            con.commit()
        return {
            "id": simulation_id,
            "chapter_no": chapter_no,
            "character_key": character_key,
            "simulation": simulation,
            "accepted": False,
        }

    def accept_emergence(
        self,
        simulation_id: str,
        *,
        reason: str,
        impact: dict[str, Any],
    ) -> dict[str, Any]:
        if not reason.strip():
            raise ValidationBlocked("emergent action acceptance requires a reason")
        with open_database(self.root) as con:
            sim = con.execute(
                "SELECT * FROM character_simulations WHERE id=?", (simulation_id,)
            ).fetchone()
            if not sim:
                raise StateConflict("character simulation not found")
            con.execute(
                "UPDATE character_simulations SET accepted=1 WHERE id=?",
                (simulation_id,),
            )
            request_id = deterministic_id("replan", "character-simulation", simulation_id)
            con.execute(
                """
                INSERT OR IGNORE INTO replan_requests(
                  id,source_type,source_id,anchor_chapter,reason,impact_json,status
                ) VALUES(?,?,?,?,?,?,'pending')
                """,
                (
                    request_id,
                    "character-simulation",
                    simulation_id,
                    int(sim["chapter_no"]),
                    reason,
                    json_dumps(impact),
                ),
            )
            con.commit()
            return {
                "simulation_id": simulation_id,
                "replan_request_id": request_id,
                "status": "pending",
                "canon_mutated": False,
            }

    def list_replan_requests(self, status: str = "pending") -> list[dict[str, Any]]:
        if status not in {"pending", "applied", "rejected", "all"}:
            raise ValueError("replan status must be pending|applied|rejected|all")
        with open_database(self.root) as con:
            if status == "all":
                rows = con.execute(
                    "SELECT * FROM replan_requests ORDER BY anchor_chapter,created_at"
                )
            else:
                rows = con.execute(
                    "SELECT * FROM replan_requests WHERE status=? ORDER BY anchor_chapter,created_at",
                    (status,),
                )
            return [dict(row) for row in rows]

    def apply_replan(
        self,
        request_id: str,
        rolling_outline: dict[str, Any],
    ) -> dict[str, Any]:
        with open_database(self.root) as con:
            req = con.execute(
                "SELECT * FROM replan_requests WHERE id=?", (request_id,)
            ).fetchone()
            if not req:
                raise StateConflict("replan request not found")
            if req["status"] == "applied":
                return {"id": request_id, "status": "applied", "idempotent": True}
            if req["status"] != "pending":
                raise StateConflict("replan request is not pending")
        payload = {
            "anchor_chapter": int(rolling_outline.get("anchor_chapter", req["anchor_chapter"])),
            "horizon_chapters": int(rolling_outline.get("horizon_chapters", 5)),
            "outline": rolling_outline.get("outline", rolling_outline),
        }
        outline = self.put_outline("rolling", payload)
        with open_database(self.root) as con:
            con.execute(
                """
                UPDATE replan_requests
                SET status='applied',decided_at=CURRENT_TIMESTAMP WHERE id=?
                """,
                (request_id,),
            )
            con.commit()
        return {"id": request_id, "status": "applied", "rolling_outline": outline}

    def reject_replan(self, request_id: str) -> dict[str, Any]:
        with open_database(self.root) as con:
            row = con.execute(
                "SELECT status FROM replan_requests WHERE id=?", (request_id,)
            ).fetchone()
            if not row:
                raise StateConflict("replan request not found")
            if row["status"] == "rejected":
                return {"id": request_id, "status": "rejected", "idempotent": True}
            if row["status"] != "pending":
                raise StateConflict("only pending replan requests may be rejected")
            con.execute(
                "UPDATE replan_requests SET status='rejected',decided_at=CURRENT_TIMESTAMP WHERE id=?",
                (request_id,),
            )
            con.commit()
            return {"id": request_id, "status": "rejected"}

    def reader_context(self, query: str, chapter_no: int) -> dict[str, Any]:
        with open_database(self.root) as con:
            return self._context_compiler(con).compile_reader(
                query=query,
                chapter_no=chapter_no,
            )

    def rebuild_vectors(
        self,
        provider_name: str,
        model: str | None = None,
    ) -> dict[str, Any]:
        provider = build_provider(provider_name, model)
        if provider is None:
            raise ValidationBlocked("cannot rebuild vectors with disabled provider")
        with open_database(self.root) as con:
            result = rebuild_vector_embeddings(con, provider)
            con.commit()
            return result

    def graph_query(
        self,
        query_type: str,
        *,
        key: str = "",
        chapter_no: int | None = None,
        max_hops: int = 3,
    ) -> Any:
        with open_database(self.root) as con:
            graph = NarrativeGraph(con)
            if query_type == "knowledge":
                if chapter_no is None:
                    raise ValueError("knowledge query requires --chapter")
                return graph.character_knowledge(key, chapter_no)
            if query_type == "causal-descendants":
                return graph.causal_descendants(
                    key, max_hops=max_hops, max_chapter=chapter_no
                )
            if query_type == "causal-ancestors":
                return graph.causal_ancestors(
                    key, max_hops=max_hops, max_chapter=chapter_no
                )
            if query_type == "neighbors":
                return graph.neighbors(key, max_chapter=chapter_no)
            if query_type == "impacted-chapters":
                return [
                    dict(row)
                    for row in con.execute(
                        """
                        SELECT cr.id,cr.chapter_no,cr.revision,rp.entity_kind,rp.entity_key,
                               rp.entity_revision
                        FROM read_points rp
                        JOIN chapter_revisions cr ON cr.id=rp.chapter_revision_id
                        WHERE rp.entity_key=?
                        ORDER BY cr.chapter_no,cr.revision
                        """,
                        (key,),
                    )
                ]
            if query_type == "object-history":
                return [
                    {**dict(row), "state": json.loads(row["state_json"])}
                    for row in con.execute(
                        "SELECT * FROM object_states WHERE object_key=? ORDER BY revision",
                        (key,),
                    )
                ]
            if query_type == "relationship-history":
                if "->" not in key:
                    raise ValueError("relationship-history key must be subject->object")
                subject, obj = key.split("->", 1)
                return [
                    {**dict(row), "value": json.loads(row["value_json"])}
                    for row in con.execute(
                        """
                        SELECT * FROM relationship_states
                        WHERE subject_key=? AND object_key=? ORDER BY revision
                        """,
                        (subject, obj),
                    )
                ]
            if query_type == "foreshadow-due":
                cutoff = chapter_no if chapter_no is not None else 2**31 - 1
                return [
                    dict(row)
                    for row in con.execute(
                        """
                        SELECT f.* FROM foreshadows f
                        JOIN (
                          SELECT x.foreshadow_key,MAX(x.revision) revision
                          FROM foreshadows x
                          JOIN pending_memory pm ON pm.id=x.source_memory_id
                          WHERE COALESCE(pm.effective_chapter,pm.source_chapter_no) <= ?
                          GROUP BY x.foreshadow_key
                        ) l ON l.foreshadow_key=f.foreshadow_key AND l.revision=f.revision
                        WHERE f.due_chapter IS NOT NULL AND f.due_chapter <= ?
                          AND f.state NOT IN ('PAID_OFF','ABANDONED')
                        ORDER BY f.due_chapter
                        """,
                        (cutoff, cutoff),
                    )
                ]
            if query_type == "promises-due":
                cutoff = chapter_no if chapter_no is not None else 2**31 - 1
                return [
                    dict(row)
                    for row in con.execute(
                        """
                        SELECT p.* FROM promises p
                        JOIN (
                          SELECT x.promise_key,MAX(x.revision) revision
                          FROM promises x
                          JOIN pending_memory pm ON pm.id=x.source_memory_id
                          WHERE COALESCE(pm.effective_chapter,pm.source_chapter_no) <= ?
                          GROUP BY x.promise_key
                        ) l ON l.promise_key=p.promise_key AND l.revision=p.revision
                        WHERE p.due_chapter IS NOT NULL AND p.due_chapter <= ?
                          AND p.status NOT IN ('paid_off','abandoned','closed')
                        ORDER BY p.due_chapter
                        """,
                        (cutoff, cutoff),
                    )
                ]
            if query_type == "event":
                return [
                    dict(row)
                    for row in con.execute(
                        "SELECT * FROM events WHERE id=? ORDER BY revision", (key,)
                    )
                ]
            raise ValueError(
                "unknown graph query type"
            )

    def context_compile(
        self,
        query: str,
        *,
        focus_nodes: list[str] | None = None,
        chapter_no: int | None = None,
    ) -> dict[str, Any]:
        config_path = self._retrieval_path()
        provider = None
        max_docs = 16
        graph_hops = 2
        if config_path:
            config = json.loads(config_path.read_text(encoding="utf-8"))
            vector = config.get("vector", {})
            provider = build_provider(
                str(vector.get("provider", "disabled")),
                vector.get("model"),
            )
            max_docs = int(config.get("max_documents", 16))
            graph_hops = int(config.get("graph_hops", 2))
        with open_database(self.root) as con:
            return ContextCompiler(con, vector_provider=provider).compile(
                query=query,
                focus_nodes=focus_nodes,
                chapter_no=chapter_no,
                max_docs=max_docs,
                graph_hops=graph_hops,
            )

    def rebuild_index(self) -> dict[str, Any]:
        config_path = self._retrieval_path()
        vector_result: dict[str, Any] | None = None
        with open_database(self.root) as con:
            result = rebuild_search_projection(con)
            if config_path:
                config = json.loads(config_path.read_text(encoding="utf-8"))
                vector = config.get("vector", {})
                provider = build_provider(
                    str(vector.get("provider", "disabled")),
                    vector.get("model"),
                )
                if provider is not None:
                    vector_result = rebuild_vector_embeddings(con, provider)
            con.commit()
            return {"search": result, "vector": vector_result}

    def backup(self, destination: Path) -> dict[str, Any]:
        destination = destination.resolve()
        destination.parent.mkdir(parents=True, exist_ok=True)
        with open_database(self.root) as con:
            dest = sqlite3.connect(destination)
            try:
                con.backup(dest)
            finally:
                dest.close()
        return {
            "database": str(self.database_path),
            "backup": str(destination),
            "sha256": hashlib.sha256(destination.read_bytes()).hexdigest(),
            "schema_version": SCHEMA_VERSION,
        }

    def export_state(self, destination: Path) -> dict[str, Any]:
        destination = destination.resolve()
        destination.parent.mkdir(parents=True, exist_ok=True)
        tables = [
            "project","story_compass","volumes","arcs","rolling_outlines",
            "chapter_plans","chapter_runs","transitions","checkpoints",
            "chapter_candidates","candidate_preflights","tournaments","frozen_drafts",
            "reviews","review_adjudications","revision_manifests","repaired_drafts",
            "regression_checks","state_scans","delta_validations","chapter_revisions",
            "publications","read_points","pending_memory","canonical_facts","characters",
            "character_states","knowledge_states","relationship_states","events","reveals",
            "object_states","world_rules","plot_threads","foreshadows","promises",
            "reader_questions","graph_nodes","graph_edges","author_intents",
            "author_preferences","agent_tasks","character_simulations","replan_requests",
            "style_profiles","voice_bibles","projection_meta","model_route_decisions",
        ]
        with open_database(self.root) as con:
            payload: dict[str, Any] = {
                "schema": "novel-state-export/v2",
                "database_schema_version": SCHEMA_VERSION,
                "tables": {},
            }
            for table in tables:
                payload["tables"][table] = [
                    dict(row) for row in con.execute(f"SELECT * FROM {table}")
                ]
        destination.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        return {
            "output": str(destination),
            "schema_version": SCHEMA_VERSION,
            "tables": {name: len(rows) for name, rows in payload["tables"].items()},
        }

    def doctor(self) -> dict[str, Any]:
        report: dict[str, Any] = {
            "database": {"status": "missing"},
            "codex": {"status": "not-found"},
            "agents": {"status": "unknown"},
            "skill": {"status": "unknown"},
            "retrieval": {"status": "unknown"},
        }
        if self.database_path.exists():
            try:
                with open_database(self.root) as con:
                    fts = con.execute(
                        "SELECT value FROM schema_meta WHERE key='fts5_available'"
                    ).fetchone()
                    report["database"] = {
                        "status": "ok",
                        "path": str(self.database_path),
                        "fts5": bool(fts and fts["value"] == "1"),
                    }
            except Exception as exc:  # doctor must report rather than hide
                report["database"] = {"status": "error", "error": str(exc)}
        config = self.root / ".codex" / "config.toml"
        agents = self.root / ".codex" / "agents"
        expected = [f"novel-{name}.toml" for name in (
            "story-director",
            "plot-architect",
            "world-builder",
            "character-agent",
            "chapter-planner",
            "writer",
            "dialogue-specialist",
            "continuity-reviewer",
            "character-consistency-reviewer",
            "foreshadowing-tracker",
            "style-critic",
            "reader-simulator",
            "state-scanner",
        )]
        missing = [name for name in expected if not (agents / name).exists()]
        report["agents"] = {
            "status": "ok" if config.exists() and not missing else "error",
            "config": str(config),
            "missing": missing,
        }
        skill = self.root / ".agents" / "skills" / "novel" / "SKILL.md"
        report["skill"] = {"status": "ok" if skill.exists() else "missing", "path": str(skill)}
        retrieval = self._retrieval_path()
        if retrieval and retrieval.exists():
            try:
                retrieval_config = json.loads(retrieval.read_text(encoding="utf-8"))
                report["retrieval"] = {
                    "status": "ok",
                    "path": str(retrieval),
                    "vector_provider": retrieval_config.get("vector", {}).get("provider", "disabled"),
                }
            except (OSError, json.JSONDecodeError) as exc:
                report["retrieval"] = {"status": "error", "error": str(exc)}
        else:
            report["retrieval"] = {"status": "missing"}
        codex = shutil.which("codex")
        if codex:
            try:
                version = subprocess.run(
                    [codex, "--version"], capture_output=True, text=True, timeout=3, check=False
                )
                auth = subprocess.run(
                    [codex, "login", "status"],
                    capture_output=True,
                    text=True,
                    timeout=3,
                    check=False,
                )
                report["codex"] = {
                    "status": "ready" if auth.returncode == 0 and "Logged in" in auth.stdout else "auth-pending",
                    "version": version.stdout.strip() or version.stderr.strip(),
                    "auth": (auth.stdout + auth.stderr).strip(),
                }
            except (OSError, subprocess.TimeoutExpired) as exc:
                report["codex"] = {"status": "error", "error": str(exc)}
        return report

    def _get_run(self, con: sqlite3.Connection, run_id: str) -> sqlite3.Row:
        row = con.execute("SELECT * FROM chapter_runs WHERE id=?", (run_id,)).fetchone()
        if not row:
            raise StateConflict(f"chapter run not found: {run_id}")
        return row

    def _transition(
        self,
        con: sqlite3.Connection,
        run_id: str,
        from_stage: str | None,
        to_stage: str,
        idempotency_key: str,
        payload: dict[str, Any],
        evidence_hash: str | None = None,
    ) -> None:
        existing = con.execute(
            "SELECT to_stage FROM transitions WHERE run_id=? AND idempotency_key=?",
            (run_id, idempotency_key),
        ).fetchone()
        if existing:
            if existing["to_stage"] != to_stage:
                raise StateConflict("idempotency key collision with a different transition")
            return
        current = con.execute(
            "SELECT stage FROM chapter_runs WHERE id=?", (run_id,)
        ).fetchone()
        if not current:
            raise StateConflict("run missing during transition")
        if from_stage is not None and current["stage"] != from_stage:
            raise StateConflict(
                f"transition expected stage {from_stage}, current {current['stage']}"
            )
        con.execute(
            """
            INSERT INTO transitions(
              run_id,from_stage,to_stage,idempotency_key,evidence_hash,payload_json
            ) VALUES(?,?,?,?,?,?)
            """,
            (
                run_id,
                from_stage,
                to_stage,
                idempotency_key,
                evidence_hash,
                json_dumps(payload),
            ),
        )
        con.execute(
            "UPDATE chapter_runs SET stage=?,updated_at=CURRENT_TIMESTAMP WHERE id=?",
            (to_stage, run_id),
        )
        con.execute(
            """
            INSERT INTO checkpoints(run_id,stage,payload_json)
            VALUES(?,?,?)
            ON CONFLICT(run_id) DO UPDATE SET
              stage=excluded.stage,payload_json=excluded.payload_json,
              updated_at=CURRENT_TIMESTAMP
            """,
            (run_id, to_stage, json_dumps(payload)),
        )

    def _next_action(self, con: sqlite3.Connection, run_id: str, stage: str) -> str:
        if stage == RunStage.CREATED.value:
            count = con.execute(
                "SELECT COUNT(*) c FROM chapter_candidates WHERE run_id=?", (run_id,)
            ).fetchone()["c"]
            return f"generate remaining A/B/C candidates ({3-int(count)} remaining)"
        if stage == RunStage.CANDIDATES_READY.value:
            return "run deterministic preflight on all A/B/C candidates"
        if stage == RunStage.PREFLIGHT_FAILED.value:
            return "abort this run and start a new bounded retry"
        if stage == RunStage.PREFLIGHT_PASSED.value:
            return "run anonymous two-order pairwise tournament"
        if stage == RunStage.FROZEN.value:
            have = {
                row["reviewer"]
                for row in con.execute("SELECT reviewer FROM reviews WHERE run_id=?", (run_id,))
            }
            return f"collect narrative-gate reviews: {sorted(NARRATIVE_REVIEWERS-have)}"
        if stage == RunStage.NARRATIVE_REVIEWED.value:
            have = {
                row["reviewer"]
                for row in con.execute("SELECT reviewer FROM reviews WHERE run_id=?", (run_id,))
            }
            return f"collect craft/reader reviews: {sorted(CRAFT_REVIEWERS-have)}"
        if stage == RunStage.REVIEWED.value:
            return "story-director adjudicates review bundle; optional one-round cross-exam on conflicts"
        if stage == RunStage.ADJUDICATED.value:
            return "write surgical revision manifest and final repaired draft"
        if stage == RunStage.REPAIRED.value:
            row = con.execute(
                "SELECT repair_level FROM repaired_drafts WHERE run_id=?", (run_id,)
            ).fetchone()
            if row and row["repair_level"] != "none":
                return "run regression check"
            return "run independent state scanner"
        if stage == RunStage.REGRESSION_PASSED.value:
            return "run independent state scanner"
        if stage == RunStage.DELTA_VALIDATED.value:
            return "publish prose; memory proposals will remain pending"
        if stage == RunStage.PUBLISHED.value:
            return "human review of pending memory proposals"
        if stage == RunStage.ABORTED.value:
            return "terminal aborted run; create a new run if retry budget remains"
        return "inspect run state"

    def _current_entity_revision(
        self, con: sqlite3.Connection, kind: str, key: str
    ) -> int:
        project = con.execute(
            "SELECT canonical_revision FROM project WHERE id='default'"
        ).fetchone()
        if not project:
            return 0
        return int(project["canonical_revision"])

    def _mark_stale_dependents(
        self,
        con: sqlite3.Connection,
        kind: str,
        key: str,
        effective_chapter: int,
        new_revision: int,
    ) -> int:
        rows = con.execute(
            """
            SELECT DISTINCT cr.id
            FROM read_points rp
            JOIN chapter_revisions cr ON cr.id=rp.chapter_revision_id
            WHERE rp.entity_kind=? AND rp.entity_key=?
              AND rp.entity_revision < ?
              AND cr.chapter_no > ?
              AND cr.stale=0
            """,
            (kind, key, new_revision, effective_chapter),
        ).fetchall()
        reason = f"{kind}:{key} changed at canonical revision {new_revision}"
        for row in rows:
            con.execute(
                "UPDATE chapter_revisions SET stale=1,stale_reason=? WHERE id=?",
                (reason, row["id"]),
            )
        return len(rows)

    def _apply_memory(
        self, con: sqlite3.Connection, proposal: sqlite3.Row, revision: int
    ) -> None:
        kind = proposal["kind"]
        entity = proposal["entity_key"]
        field = proposal["field"]
        value = json.loads(proposal["new_value_json"])
        chapter = int(proposal["effective_chapter"] or proposal["source_chapter_no"])
        source = proposal["id"]

        if kind == "fact":
            con.execute(
                """
                INSERT INTO canonical_facts(entity_key,field,value_json,effective_chapter,revision,source_memory_id)
                VALUES(?,?,?,?,?,?)
                """,
                (entity, field, json_dumps(value), chapter, revision, source),
            )
            self._upsert_graph_node(con, f"fact:{entity}", "fact", entity, value, revision, source)
            return

        if kind == "character_state":
            con.execute(
                """
                INSERT INTO characters(id,name,definition_json,revision)
                VALUES(?,?,?,?)
                ON CONFLICT(name) DO NOTHING
                """,
                (f"char:{entity}", entity, "{}", revision),
            )
            character = con.execute(
                "SELECT id FROM characters WHERE name=?", (entity,)
            ).fetchone()
            if not character:
                raise StateConflict("failed to resolve character state owner")
            con.execute(
                """
                INSERT INTO character_states(
                  character_id,field,value_json,effective_chapter,revision,source_memory_id
                ) VALUES(?,?,?,?,?,?)
                """,
                (
                    character["id"],
                    field,
                    json_dumps(value),
                    chapter,
                    revision,
                    source,
                ),
            )
            self._upsert_graph_node(
                con, f"character:{entity}", "character", entity, {field: value}, revision, source
            )
            return

        if kind == "knowledge":
            if not isinstance(value, dict) or value.get("state") not in {
                "knows",
                "suspects",
                "falsely_believes",
                "unknown",
            }:
                raise ValidationBlocked("knowledge memory requires {state, belief_value?}")
            con.execute(
                """
                INSERT INTO knowledge_states(
                  character_key,fact_key,state,belief_value_json,since_chapter,evidence,revision,source_memory_id
                ) VALUES(?,?,?,?,?,?,?,?)
                """,
                (
                    entity,
                    field,
                    value["state"],
                    json_dumps(value.get("belief_value")) if "belief_value" in value else None,
                    chapter,
                    proposal["evidence"],
                    revision,
                    source,
                ),
            )
            self._upsert_graph_node(con, f"character:{entity}", "character", entity, {}, revision, source)
            self._upsert_graph_node(con, f"fact:{field}", "fact", field, {}, revision, source)
            self._insert_graph_edge(
                con,
                f"knowledge:{entity}:{field}",
                f"character:{entity}",
                f"fact:{field}",
                value["state"],
                chapter,
                value,
                revision,
                source,
            )
            return

        if kind == "relationship":
            if not isinstance(value, dict):
                raise ValidationBlocked("relationship memory requires an object value")
            subject = str(value.get("subject") or entity.split("->", 1)[0])
            obj = str(value.get("object") or (entity.split("->", 1)[1] if "->" in entity else ""))
            relation = str(value.get("relation") or field)
            if not subject or not obj:
                raise ValidationBlocked("relationship requires subject and object")
            con.execute(
                """
                INSERT INTO relationship_states(
                  subject_key,object_key,relation,value_json,effective_chapter,revision,source_memory_id
                ) VALUES(?,?,?,?,?,?,?)
                """,
                (subject, obj, relation, json_dumps(value.get("value", value)), chapter, revision, source),
            )
            self._upsert_graph_node(con, f"character:{subject}", "character", subject, {}, revision, source)
            self._upsert_graph_node(con, f"character:{obj}", "character", obj, {}, revision, source)
            self._insert_graph_edge(
                con,
                f"relationship:{subject}:{relation}:{obj}",
                f"character:{subject}",
                f"character:{obj}",
                relation,
                chapter,
                value,
                revision,
                source,
            )
            return

        if kind == "event":
            if not isinstance(value, dict):
                raise ValidationBlocked("event memory requires an object value")
            occurrence = int(value.get("occurrence_index", chapter * 1000))
            summary = str(value.get("summary") or field)
            con.execute(
                """
                INSERT INTO events(id,occurrence_index,chapter_no,summary,payload_json,revision,source_memory_id)
                VALUES(?,?,?,?,?,?,?)
                """,
                (entity, occurrence, chapter, summary, json_dumps(value), revision, source),
            )
            self._upsert_graph_node(con, f"event:{entity}", "event", summary, value, revision, source)
            new_causes = {str(cause) for cause in value.get("caused_by", [])}
            prior_causes = {
                str(row["src_key"]).removeprefix("event:"): row
                for row in con.execute(
                    """
                    SELECT e.*
                    FROM graph_edges e
                    JOIN (
                      SELECT edge_key,MAX(revision) revision
                      FROM graph_edges
                      WHERE edge_type='causes' AND dst_key=?
                      GROUP BY edge_key
                    ) l ON l.edge_key=e.edge_key AND l.revision=e.revision
                    WHERE e.edge_type='causes' AND e.dst_key=? AND e.active=1
                    """,
                    (f"event:{entity}", f"event:{entity}"),
                )
            }
            for removed in sorted(set(prior_causes) - new_causes):
                row = prior_causes[removed]
                self._insert_graph_edge(
                    con,
                    row["edge_key"],
                    row["src_key"],
                    row["dst_key"],
                    row["edge_type"],
                    chapter,
                    {"removed": True},
                    revision,
                    source,
                    active=False,
                )
            for cause in sorted(new_causes):
                self._upsert_graph_node(con, f"event:{cause}", "event", str(cause), {}, revision, source)
                self._insert_graph_edge(
                    con,
                    f"cause:{cause}:{entity}",
                    f"event:{cause}",
                    f"event:{entity}",
                    "causes",
                    chapter,
                    {},
                    revision,
                    source,
                )
            return

        if kind == "reveal":
            if not isinstance(value, dict):
                raise ValidationBlocked("reveal memory requires an object value")
            con.execute(
                """
                INSERT INTO reveals(fact_key,reader_reveal_index,chapter_no,payload_json,revision,source_memory_id)
                VALUES(?,?,?,?,?,?)
                """,
                (
                    entity,
                    int(value.get("reader_reveal_index", chapter * 1000)),
                    chapter,
                    json_dumps(value),
                    revision,
                    source,
                ),
            )
            return

        if kind == "object_state":
            if not isinstance(value, dict):
                raise ValidationBlocked("object_state memory requires an object value")
            con.execute(
                """
                INSERT INTO object_states(
                  object_key,owner_key,location_key,state_json,effective_chapter,revision,source_memory_id
                ) VALUES(?,?,?,?,?,?,?)
                """,
                (
                    entity,
                    value.get("owner"),
                    value.get("location"),
                    json_dumps(value.get("state", {})),
                    chapter,
                    revision,
                    source,
                ),
            )
            self._upsert_graph_node(con, f"object:{entity}", "object", entity, value, revision, source)
            self._replace_single_graph_edge(
                con,
                edge_key=f"object-owner:{entity}",
                src=f"object:{entity}",
                dst=f"character:{value['owner']}" if value.get("owner") else None,
                edge_type="owned_by",
                chapter=chapter,
                revision=revision,
                source=source,
            )
            self._replace_single_graph_edge(
                con,
                edge_key=f"object-location:{entity}",
                src=f"object:{entity}",
                dst=f"location:{value['location']}" if value.get("location") else None,
                edge_type="located_at",
                chapter=chapter,
                revision=revision,
                source=source,
            )
            return

        if kind == "world_rule":
            text = str(value.get("text") if isinstance(value, dict) else value)
            active = int(value.get("active", True)) if isinstance(value, dict) else 1
            con.execute(
                """
                INSERT INTO world_rules(rule_key,rule_text,active,effective_chapter,revision,source_memory_id)
                VALUES(?,?,?,?,?,?)
                """,
                (entity, text, active, chapter, revision, source),
            )
            self._upsert_graph_node(con, f"world_rule:{entity}", "world_rule", text, {"active": bool(active)}, revision, source)
            return

        if kind == "foreshadow":
            if not isinstance(value, dict):
                raise ValidationBlocked("foreshadow memory requires an object value")
            state = str(value.get("state"))
            allowed = {
                "PLANNED","PLANTED","HINTED","REINFORCED","DUE","PAID_OFF","DELAYED","ABANDONED"
            }
            if state not in allowed:
                raise ValidationBlocked("invalid foreshadow state")
            con.execute(
                """
                INSERT INTO foreshadows(
                  foreshadow_key,state,description,planted_chapter,due_chapter,payoff_chapter,
                  revision,source_memory_id
                ) VALUES(?,?,?,?,?,?,?,?)
                """,
                (
                    entity,
                    state,
                    str(value.get("description", field)),
                    value.get("planted_chapter"),
                    value.get("due_chapter"),
                    value.get("payoff_chapter"),
                    revision,
                    source,
                ),
            )
            return

        if kind == "promise":
            if not isinstance(value, dict):
                raise ValidationBlocked("promise memory requires an object value")
            con.execute(
                """
                INSERT INTO promises(
                  promise_key,status,description,due_chapter,payoff_event_key,revision,source_memory_id
                ) VALUES(?,?,?,?,?,?,?)
                """,
                (
                    entity,
                    str(value.get("status", "open")),
                    str(value.get("description", field)),
                    value.get("due_chapter"),
                    value.get("payoff_event_key"),
                    revision,
                    source,
                ),
            )
            return

        if kind == "plot_thread":
            if not isinstance(value, dict):
                raise ValidationBlocked("plot_thread memory requires an object value")
            con.execute(
                """
                INSERT INTO plot_threads(
                  thread_key,status,description,effective_chapter,revision,source_memory_id
                ) VALUES(?,?,?,?,?,?)
                """,
                (
                    entity,
                    str(value.get("status", "open")),
                    str(value.get("description", field)),
                    chapter,
                    revision,
                    source,
                ),
            )
            return

        if kind == "reader_question":
            if not isinstance(value, dict):
                raise ValidationBlocked("reader_question memory requires an object value")
            con.execute(
                """
                INSERT INTO reader_questions(
                  question_key,status,text,opened_chapter,closed_chapter,revision,source_memory_id
                ) VALUES(?,?,?,?,?,?,?)
                """,
                (
                    entity,
                    str(value.get("status", "open")),
                    str(value.get("text", field)),
                    int(value.get("opened_chapter", chapter)),
                    value.get("closed_chapter"),
                    revision,
                    source,
                ),
            )
            return

        # Unknown semantic categories remain canonical facts rather than silently discarded.
        con.execute(
            """
            INSERT INTO canonical_facts(entity_key,field,value_json,effective_chapter,revision,source_memory_id)
            VALUES(?,?,?,?,?,?)
            """,
            (entity, f"{kind}.{field}", json_dumps(value), chapter, revision, source),
        )

    def _upsert_graph_node(
        self,
        con: sqlite3.Connection,
        node_key: str,
        node_type: str,
        label: str,
        payload: Any,
        revision: int,
        source: str,
    ) -> None:
        con.execute(
            """
            INSERT INTO graph_nodes(node_key,node_type,label,payload_json,revision,source_memory_id)
            VALUES(?,?,?,?,?,?)
            """,
            (node_key, node_type, label, json_dumps(payload), revision, source),
        )

    def _replace_single_graph_edge(
        self,
        con: sqlite3.Connection,
        *,
        edge_key: str,
        src: str,
        dst: str | None,
        edge_type: str,
        chapter: int,
        revision: int,
        source: str,
    ) -> None:
        previous = con.execute(
            """
            SELECT * FROM graph_edges
            WHERE edge_key=? ORDER BY revision DESC LIMIT 1
            """,
            (edge_key,),
        ).fetchone()
        if dst is None:
            if previous and bool(previous["active"]):
                self._insert_graph_edge(
                    con,
                    edge_key,
                    src,
                    previous["dst_key"],
                    edge_type,
                    chapter,
                    {"removed": True},
                    revision,
                    source,
                    active=False,
                )
            return
        self._insert_graph_edge(
            con,
            edge_key,
            src,
            dst,
            edge_type,
            chapter,
            {},
            revision,
            source,
            active=True,
        )

    def _insert_graph_edge(
        self,
        con: sqlite3.Connection,
        edge_key: str,
        src: str,
        dst: str,
        edge_type: str,
        chapter: int,
        payload: Any,
        revision: int,
        source: str,
        *,
        active: bool = True,
    ) -> None:
        con.execute(
            """
            INSERT INTO graph_edges(
              edge_key,src_key,dst_key,edge_type,effective_chapter,payload_json,
              active,revision,source_memory_id
            ) VALUES(?,?,?,?,?,?,?,?,?)
            """,
            (
                edge_key,
                src,
                dst,
                edge_type,
                chapter,
                json_dumps(payload),
                int(active),
                revision,
                source,
            ),
        )
