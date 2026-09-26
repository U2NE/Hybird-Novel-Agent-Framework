from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path

from novel_agent_framework.errors import StateConflict, ValidationBlocked
from novel_agent_framework.models import RunStage
from novel_agent_framework.runtime import NovelRuntime


SOURCE_ROOT = Path(__file__).resolve().parents[1]


class RuntimeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        cfg = self.root / ".novel" / "config"
        cfg.mkdir(parents=True)
        shutil.copy2(SOURCE_ROOT / "config" / "model-routing.json", cfg / "model-routing.json")
        shutil.copy2(SOURCE_ROOT / "config" / "retrieval.json", cfg / "retrieval.json")
        self.runtime = NovelRuntime(self.root)
        self.runtime.init("테스트", approval_mode="autonomous")

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _plan_and_run(
        self,
        chapter: int,
        delta: dict | None = None,
        *,
        required_beats: list[str] | None = None,
        exit_conditions: dict | None = None,
        read_points: list[dict] | None = None,
    ) -> str:
        delta = delta or {"changes": []}
        required_beats = required_beats or ["beat"]
        self.runtime.put_plan(
            chapter,
            f"{chapter}장",
            {"beats": [{"id": required_beats[0], "purpose": "advance"}]},
            {
                "required_beats": required_beats,
                "exit_conditions": exit_conditions or {},
                "read_points": read_points or [],
            },
            delta,
        )
        return self.runtime.start_run(chapter, mode="auto")["id"]

    @staticmethod
    def _good_observation(required: list[str] | None = None, exits: dict | None = None) -> dict:
        return {
            "realized_beats": required or ["beat"],
            "exit_state": exits or {},
        }

    def _write_candidates(self, run: str, delta: dict) -> None:
        envelope = self.runtime.orchestrate_next(run)
        self.assertEqual({t["stage"] for t in envelope["tasks"]}, {"write:A", "write:B", "write:C"})
        for task in envelope["tasks"]:
            lens = task["stage"].split(":")[1]
            self.runtime.add_candidate(
                run,
                lens,
                f"{lens} 후보 본문",
                delta,
                model=task["model"],
                reasoning_effort=task["reasoning_effort"],
            )

    def _pass_preflights(self, run: str, observation: dict | None = None) -> None:
        envelope = self.runtime.orchestrate_next(run)
        self.assertEqual(
            {t["stage"] for t in envelope["tasks"]},
            {"preflight:A", "preflight:B", "preflight:C"},
        )
        for task in envelope["tasks"]:
            lens = task["stage"].split(":")[1]
            result = self.runtime.candidate_preflight(
                run, lens, observation or self._good_observation()
            )
            self.assertTrue(result["passed"])

    def _complete_clear_tournament(self, run: str) -> dict:
        envelope = self.runtime.orchestrate_next(run)
        self.assertEqual(len(envelope["tasks"]), 6)
        for task in envelope["tasks"]:
            pair = task["stage"].split(":")[1]
            left, right = pair.split("-")
            preferred = "P" if "P" in (left, right) else "Q"
            self.runtime.complete_pairwise_task(
                task["task_id"],
                {"preferred": preferred, "confidence": 0.95, "reason": "fixture"},
            )
        action = self.runtime.orchestrate_next(run)
        self.assertEqual(action["runtime_action"], "tournament-select")
        result = self.runtime.select_tournament(
            run,
            action["winner_lens"],
            reason="deterministic clear tournament",
        )
        self.assertFalse(result["aggregation"]["hard_adjudication_required"])
        return result

    def _reviews_and_adjudicate(self, run: str, frozen_hash: str) -> None:
        first = self.runtime.orchestrate_next(run)
        self.assertEqual(
            {t["stage"] for t in first["tasks"]},
            {"review:continuity", "review:character-consistency", "review:foreshadowing"},
        )
        for task in first["tasks"]:
            reviewer = task["stage"].split(":", 1)[1]
            self.runtime.add_review(
                run,
                reviewer,
                {"draft_hash": frozen_hash, "findings": []},
                model=task["model"],
                reasoning_effort=task["reasoning_effort"],
            )

        second = self.runtime.orchestrate_next(run)
        self.assertEqual(
            {t["stage"] for t in second["tasks"]},
            {"review:dialogue", "review:style", "review:reader"},
        )
        for task in second["tasks"]:
            reviewer = task["stage"].split(":", 1)[1]
            review = {"draft_hash": frozen_hash, "findings": []}
            if reviewer == "reader":
                review["context_policy"] = "reader-visible-only"
                self.assertEqual(task["input"]["context"]["audience"], "reader")
                self.assertNotIn("author_preferences", task["input"]["context"])
            self.runtime.add_review(
                run,
                reviewer,
                review,
                model=task["model"],
                reasoning_effort=task["reasoning_effort"],
            )

        adjudication = self.runtime.orchestrate_next(run)
        task = adjudication["tasks"][0]
        self.assertEqual(task["stage"], "review-adjudication")
        self.runtime.adjudicate_reviews(
            run,
            {"repairs": []},
            model=task["model"],
            reasoning_effort=task["reasoning_effort"],
        )

    def _complete_chapter(
        self,
        run: str,
        delta: dict,
        *,
        repair_level: str = "none",
    ) -> dict:
        self._write_candidates(run, delta)
        self._pass_preflights(run)
        tournament = self._complete_clear_tournament(run)
        self._reviews_and_adjudicate(run, tournament["frozen_hash"])

        repair_task = self.runtime.orchestrate_next(run)["tasks"][0]
        self.assertEqual(repair_task["stage"], "repair")
        if repair_level == "none":
            self.runtime.put_revision_manifest(
                run,
                {"repairs": []},
                repair_level="none",
            )
        else:
            self.runtime.put_revision_manifest(
                run,
                {"repairs": [{"level": repair_level}]},
                repair_level=repair_level,
                repair_attempts=1,
                repaired_prose="수정된 최종 본문",
                repaired_declared_delta=delta,
            )
            regression = self.runtime.orchestrate_next(run)["tasks"][0]
            self.assertEqual(regression["stage"], "regression")
            self.runtime.add_regression_check(
                run,
                {"continuity": "pass"},
                attempt=regression["attempt"],
                passed=True,
            )

        scan = self.runtime.orchestrate_next(run)["tasks"][0]
        self.assertEqual(scan["stage"], "state-scan")
        self.assertNotIn("plan", scan["input"])
        self.runtime.record_state_scan(
            run,
            delta,
            self._good_observation(),
            model=scan["model"],
            reasoning_effort=scan["reasoning_effort"],
        )
        validated = self.runtime.validate_delta(run)
        self.assertTrue(validated["passed"])
        return self.runtime.publish(run)

    def test_semantic_write_without_runtime_task_is_blocked(self) -> None:
        run = self._plan_and_run(1)
        with self.assertRaises(ValidationBlocked):
            self.runtime.add_candidate(
                run,
                "A",
                "본문",
                {"changes": []},
                model="gpt-6-luna",
                reasoning_effort="low",
            )

    def test_full_runtime_authority_and_pending_memory(self) -> None:
        delta = {
            "changes": [
                {
                    "kind": "fact",
                    "entity_key": "secret",
                    "field": "owner",
                    "new_value": "B",
                    "effective_chapter": 1,
                }
            ]
        }
        run = self._plan_and_run(1, delta)
        published = self._complete_chapter(run, delta)
        self.assertFalse(published["canon_mutated"])
        self.assertEqual(self.runtime.status()["project"]["canonical_revision"], 0)
        pending = self.runtime.list_memory("pending")
        self.assertEqual(len(pending), 1)
        approved = self.runtime.approve_memory(pending[0]["id"])
        self.assertEqual(approved["canonical_revision"], 1)
        self.assertTrue(self.runtime.context_compile("secret", chapter_no=1)["documents"])

    def test_preflight_failure_requires_abort(self) -> None:
        run = self._plan_and_run(1, required_beats=["must"])
        self._write_candidates(run, {"changes": []})
        tasks = self.runtime.orchestrate_next(run)["tasks"]
        for task in tasks:
            lens = task["stage"].split(":")[1]
            self.runtime.candidate_preflight(
                run,
                lens,
                {"realized_beats": [], "exit_state": {}},
            )
        action = self.runtime.orchestrate_next(run)
        self.assertEqual(action["runtime_action"], "abort-run")
        aborted = self.runtime.abort_run(run, action["reason"])
        self.assertEqual(aborted["stage"], RunStage.ABORTED.value)

    def test_hard_tournament_is_runtime_issued(self) -> None:
        run = self._plan_and_run(1)
        self._write_candidates(run, {"changes": []})
        self._pass_preflights(run)
        tasks = self.runtime.orchestrate_next(run)["tasks"]
        # P>Q, Q>R, R>P produces a cycle; both orders agree.
        winner = {
            frozenset({"P", "Q"}): "P",
            frozenset({"Q", "R"}): "Q",
            frozenset({"P", "R"}): "R",
        }
        for task in tasks:
            pair = task["stage"].split(":")[1]
            a, b = pair.split("-")
            self.runtime.complete_pairwise_task(
                task["task_id"],
                {
                    "preferred": winner[frozenset({a, b})],
                    "confidence": 0.95,
                    "reason": "cycle fixture",
                },
            )
        hard = self.runtime.orchestrate_next(run)
        task = hard["tasks"][0]
        self.assertEqual(task["stage"], "tournament-hard")
        self.assertEqual(task["model"], "gpt-6-sol")
        self.runtime.complete_tournament_hard_task(
            task["task_id"], {"winner_alias": "P", "reason": "hard resolve"}
        )
        action = self.runtime.orchestrate_next(run)
        self.assertEqual(action["runtime_action"], "tournament-select-hard")
        result = self.runtime.select_tournament(
            run,
            action["winner_lens"],
            reason="hard tournament",
        )
        self.assertTrue(result["aggregation"]["hard_adjudication_required"])
        self.assertEqual(result["hard_adjudication"]["winner_alias"], "P")

    def test_two_stage_review_gate_and_reader_attestation(self) -> None:
        run = self._plan_and_run(1)
        self._write_candidates(run, {"changes": []})
        self._pass_preflights(run)
        tournament = self._complete_clear_tournament(run)

        narrative = self.runtime.orchestrate_next(run)["tasks"]
        # A craft reviewer cannot be inserted before narrative gate because no task exists.
        with self.assertRaises(ValidationBlocked):
            self.runtime.add_review(
                run,
                "style",
                {"draft_hash": tournament["frozen_hash"], "findings": []},
                model="gpt-6-luna",
                reasoning_effort="medium",
            )
        for task in narrative:
            reviewer = task["stage"].split(":", 1)[1]
            self.runtime.add_review(
                run,
                reviewer,
                {"draft_hash": tournament["frozen_hash"], "findings": []},
                model=task["model"],
                reasoning_effort=task["reasoning_effort"],
            )
        craft = self.runtime.orchestrate_next(run)["tasks"]
        reader = next(task for task in craft if task["stage"] == "review:reader")
        with self.assertRaises(ValidationBlocked):
            self.runtime.add_review(
                run,
                "reader",
                {"draft_hash": tournament["frozen_hash"], "findings": []},
                model=reader["model"],
                reasoning_effort=reader["reasoning_effort"],
            )

    def test_review_conflict_escalates_to_sol_hard_task(self) -> None:
        run = self._plan_and_run(1)
        self._write_candidates(run, {"changes": []})
        self._pass_preflights(run)
        tournament = self._complete_clear_tournament(run)
        frozen_hash = tournament["frozen_hash"]

        for task in self.runtime.orchestrate_next(run)["tasks"]:
            reviewer = task["stage"].split(":", 1)[1]
            self.runtime.add_review(
                run,
                reviewer,
                {"draft_hash": frozen_hash, "findings": []},
                model=task["model"],
                reasoning_effort=task["reasoning_effort"],
            )
        for task in self.runtime.orchestrate_next(run)["tasks"]:
            reviewer = task["stage"].split(":", 1)[1]
            review = {"draft_hash": frozen_hash, "findings": []}
            if reviewer == "reader":
                review["context_policy"] = "reader-visible-only"
            self.runtime.add_review(
                run,
                reviewer,
                review,
                model=task["model"],
                reasoning_effort=task["reasoning_effort"],
            )

        normal = self.runtime.orchestrate_next(run)["tasks"][0]
        staged = self.runtime.adjudicate_reviews(
            run,
            {"repairs": ["resolve conflict"]},
            conflicts=[
                {
                    "issue": "motivation vs continuity",
                    "reviewers": ["continuity", "character-consistency"],
                }
            ],
            model=normal["model"],
            reasoning_effort=normal["reasoning_effort"],
        )
        self.assertTrue(staged["requires_hard_adjudication"])
        hard = self.runtime.orchestrate_next(run)["tasks"][0]
        self.assertEqual(hard["stage"], "review-adjudication-hard")
        self.assertEqual(hard["model"], "gpt-6-sol")
        self.assertEqual(hard["reasoning_effort"], "high")

        with self.assertRaises(ValidationBlocked):
            self.runtime.complete_review_hard_task(
                hard["task_id"],
                {
                    "decision": {"repairs": []},
                    "cross_exam": {
                        "round": 1,
                        "participants": ["style"],
                    },
                },
            )
        completed = self.runtime.complete_review_hard_task(
            hard["task_id"],
            {
                "decision": {"repairs": ["clarify motive without changing event"]},
                "cross_exam": {
                    "round": 1,
                    "participants": ["continuity", "character-consistency"],
                },
            },
        )
        self.assertTrue(completed["cross_exam_used"])
        self.assertEqual(
            self.runtime.resume(run)["run"]["stage"],
            RunStage.ADJUDICATED.value,
        )

    def test_nontrivial_repair_requires_regression_then_state_scan(self) -> None:
        run = self._plan_and_run(1)
        published = self._complete_chapter(run, {"changes": []}, repair_level="sentence")
        self.assertEqual(published["chapter_revision"]["chapter_no"], 1)

    def test_time_scoped_author_and_reader_context(self) -> None:
        run = self._plan_and_run(1)
        hidden = self.runtime.stage_memory(
            source_run_id=run,
            source_chapter_no=1,
            kind="fact",
            entity_key="hidden",
            field="value",
            new_value="author-only",
            effective_chapter=1,
        )
        self.runtime.approve_memory(hidden["id"])
        future = self.runtime.stage_memory(
            source_run_id=run,
            source_chapter_no=1,
            kind="fact",
            entity_key="future",
            field="value",
            new_value="later",
            effective_chapter=3,
        )
        self.runtime.approve_memory(future["id"])
        author = self.runtime.context_compile("hidden", chapter_no=1)
        reader = self.runtime.reader_context("hidden", 1)
        future_at_two = self.runtime.context_compile("future", chapter_no=2)
        self.assertTrue(author["documents"])
        self.assertEqual(reader["documents"], [])
        self.assertEqual(future_at_two["documents"], [])

    def test_style_voice_and_emergent_replan(self) -> None:
        profile = self.runtime.create_style_profile(
            "sample",
            "나는 문을 열었다. 비가 왔다.\n\n\"왔어?\" 나는 물었다.",
        )
        self.assertEqual(profile["profile"]["schema"], "novel-author-style/v1")
        comparison = self.runtime.compare_style("나는 걸었다. 나는 멈췄다.")
        self.assertIn("drift_score", comparison)

        self.runtime.put_voice_bible(
            "Alice",
            "Bob",
            {
                "baseline_register": "polite",
                "pressure_register": "banmal",
                "address_forms": ["선배", "밥"],
            },
        )
        report = self.runtime.preflight_check(
            chapter_no=1,
            contract={"required_beats": [], "read_points": []},
            observation={
                "dialogue_registers": [
                    {
                        "character": "Alice",
                        "relationship_key": "Bob",
                        "register": "formal_polite",
                        "address_form": "당신",
                    }
                ],
                "realized_beats": [],
                "exit_state": {},
            },
        )
        codes = {item["code"] for item in report["issues"]}
        self.assertIn("KOREAN_REGISTER_DRIFT", codes)
        self.assertIn("KOREAN_ADDRESS_FORM_DRIFT", codes)

        sandbox_task = self.runtime.sandbox_next(2, "Alice")
        simulation = {
            "perception": {"threat": "Bob"},
            "memory": {"last": "betrayal"},
            "intention": {"goal": "confront"},
            "action": {"type": "leave-plan"},
            "consequence": {"future_outline_conflict": True},
        }
        sim = self.runtime.character_sandbox(
            2,
            "Alice",
            simulation,
            task_id=sandbox_task["task_id"],
            model=sandbox_task["model"],
            reasoning_effort=sandbox_task["reasoning_effort"],
        )
        req = self.runtime.accept_emergence(
            sim["id"],
            reason="character motivation is stronger than the local outline",
            impact={"chapters": [2, 3]},
        )
        self.assertFalse(req["canon_mutated"])
        applied = self.runtime.apply_replan(
            req["replan_request_id"],
            {"anchor_chapter": 2, "horizon_chapters": 4, "outline": {"direction": "new"}},
        )
        self.assertEqual(applied["status"], "applied")

    def test_idea_review_is_runtime_issued_and_author_controlled(self) -> None:
        idea = self.runtime.idea_propose(
            "조력자가 첫 사건을 의도적으로 설계했다."
        )
        task = idea["review_task"]
        self.assertEqual(task["role"], "novel-story-director")
        self.assertEqual(task["status"], "pending")

        critique = {
            "story_fit": "conditional",
            "novelty": "medium",
            "motivation_fit": "requires prior grievance",
            "causality": "can explain the inciting incident",
            "setup_debt": ["1장 단서 필요"],
            "payoff_opportunities": ["중반 관계 붕괴"],
            "continuity_conflicts": [],
            "reader_information_impact": "delays culprit certainty",
            "opportunity_cost": "reduces alternative culprit space",
            "stale_blast_radius": [1, 2],
            "improvements": ["설계자와 실행자를 분리"],
        }

        with self.assertRaises(ValidationBlocked):
            self.runtime.idea_review(
                idea["id"],
                critique,
                model="gpt-6-sol",
                reasoning_effort=task["reasoning_effort"],
            )

        reviewed = self.runtime.idea_review(
            idea["id"],
            critique,
            model=task["model"],
            reasoning_effort=task["reasoning_effort"],
        )
        self.assertEqual(reviewed["status"], "reviewed")

        accepted = self.runtime.idea_decide(idea["id"], True)
        self.assertEqual(accepted["namespace"], "AuthorIntent")
        self.assertFalse(accepted["canon_mutated"])

    def test_sandbox_requires_runtime_issued_task(self) -> None:
        simulation = {
            "perception": {"threat": "Bob"},
            "memory": {"last": "betrayal"},
            "intention": {"goal": "confront"},
            "action": {"type": "leave-plan"},
            "consequence": {"future_outline_conflict": True},
        }
        with self.assertRaises(ValidationBlocked):
            self.runtime.character_sandbox(
                2,
                "Alice",
                simulation,
                task_id="missing-task",
                model="gpt-6-luna",
                reasoning_effort="medium",
            )

        task = self.runtime.sandbox_next(2, "Alice")
        result = self.runtime.character_sandbox(
            2,
            "Alice",
            simulation,
            task_id=task["task_id"],
            model=task["model"],
            reasoning_effort=task["reasoning_effort"],
        )
        self.assertEqual(result["character_key"], "Alice")

    def test_quality_and_writer_effort_benchmark(self) -> None:
        audit = self.runtime.quality_eval(
            "나는 갔다. 나는 봤다. 나는 섰다. 나는 웃었다. 나는 울었다. 나는 멈췄다.",
            {"over_explained_theme": True},
        )
        codes = {w["code"] for w in audit["warnings"]}
        self.assertIn("REPEATED_SENTENCE_OPENINGS", codes)
        self.assertIn("OVER_EXPLAINED_THEME", codes)
        bench = self.runtime.benchmark_writer_effort(
            [{"continuity_passed": True, "latency_seconds": 1, "output_tokens": 100}],
            [{"continuity_passed": True, "latency_seconds": 2, "output_tokens": 110}],
        )
        self.assertEqual(bench["schema"], "novel-writer-effort-benchmark/v1")
        self.assertIn("delta_low_minus_none", bench)

    def test_backup_export_and_status(self) -> None:
        backup = self.root / "backup.sqlite3"
        exported = self.root / "state.json"
        self.assertEqual(len(self.runtime.backup(backup)["sha256"]), 64)
        summary = self.runtime.export_state(exported)
        self.assertEqual(summary["schema_version"], 4)
        self.assertIn("agent_tasks", summary["tables"])
        status = self.runtime.status()
        self.assertIn("pending_agent_tasks", status)
        self.assertIn("pending_replans", status)


if __name__ == "__main__":
    unittest.main()
