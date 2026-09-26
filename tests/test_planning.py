from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path

from novel_agent_framework.runtime import NovelRuntime

SOURCE = Path(__file__).resolve().parents[1]


class PlanningWorkflowTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        cfg = self.root / ".novel" / "config"
        cfg.mkdir(parents=True)
        shutil.copy2(SOURCE / "config" / "model-routing.json", cfg / "model-routing.json")
        shutil.copy2(SOURCE / "config" / "retrieval.json", cfg / "retrieval.json")
        self.runtime = NovelRuntime(self.root)
        self.runtime.init("계획", approval_mode="autonomous")
        self.runtime.set_story_compass(
            {
                "premise": "한 인물이 거짓말의 대가를 치른다.",
                "ending_promise": "스스로 선택한 책임을 진다.",
                "core_conflict": "욕망 대 책임",
                "themes": ["책임"],
            }
        )

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_director_selects_specialists_then_planner(self) -> None:
        first = self.runtime.workflow_next(1)
        self.assertEqual(len(first["tasks"]), 1)
        director = first["tasks"][0]
        self.assertEqual(director["role"], "novel-story-director")
        self.runtime.complete_planning_task(
            director["task_id"],
            {"roles": ["character-agent"], "reason": "character motivation is central"},
        )

        second = self.runtime.workflow_next(1)
        self.assertEqual(len(second["tasks"]), 1)
        specialist = second["tasks"][0]
        self.assertEqual(specialist["role"], "novel-character-agent")
        self.runtime.complete_planning_task(
            specialist["task_id"],
            {"motivation": "책임을 피하고 싶다", "pressure": "친구의 의심"},
        )

        third = self.runtime.workflow_next(1)
        planner = third["tasks"][0]
        self.assertEqual(planner["role"], "novel-chapter-planner")
        self.runtime.complete_planning_task(
            planner["task_id"],
            {
                "title": "첫 거짓말",
                "plan": {"beats": [{"id": "lie", "purpose": "commit"}]},
                "contract": {
                    "required_beats": ["lie"],
                    "forbidden_reveals": ["truth"],
                    "exit_conditions": {"lie_committed": True},
                    "read_points": [],
                },
                "expected_delta": {"changes": []},
            },
        )
        fourth = self.runtime.workflow_next(1)
        self.assertEqual(fourth["runtime_action"], "start-run-auto")


if __name__ == "__main__":
    unittest.main()
