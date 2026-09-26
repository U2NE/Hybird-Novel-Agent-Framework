from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path

from novel_agent_framework.db import open_database
from novel_agent_framework.retrieval import ContextCompiler
from novel_agent_framework.runtime import NovelRuntime
from novel_agent_framework.vectors import DeterministicHashProvider, rebuild_vector_embeddings

SOURCE = Path(__file__).resolve().parents[1]


class RetrievalTests(unittest.TestCase):
    def test_vector_path_is_actually_used(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            cfg = root / ".novel" / "config"
            cfg.mkdir(parents=True)
            shutil.copy2(SOURCE / "config" / "model-routing.json", cfg / "model-routing.json")
            shutil.copy2(SOURCE / "config" / "retrieval.json", cfg / "retrieval.json")
            rt = NovelRuntime(root)
            rt.init("retrieval", approval_mode="autonomous")
            rt.put_plan(
                1, "1장", {"beats": []},
                {"required_beats": [], "read_points": []},
                {"changes": []},
            )
            run = rt.start_run(1, mode="auto")["id"]
            proposal = rt.stage_memory(
                source_run_id=run,
                source_chapter_no=1,
                kind="fact",
                entity_key="blue-door",
                field="state",
                new_value="locked",
                effective_chapter=1,
            )
            rt.approve_memory(proposal["id"])

            provider = DeterministicHashProvider()
            with open_database(root) as con:
                rebuilt = rebuild_vector_embeddings(con, provider)
                con.commit()
                self.assertGreater(rebuilt["documents"], 0)
                compiler = ContextCompiler(con, vector_provider=provider)
                result = compiler.search(
                    "전혀 다른 검색어",
                    limit=5,
                    chapter_no=1,
                    audience="author",
                )
                self.assertTrue(result)
                self.assertIn("fusion_score", result[0])

    def test_historical_relationship_revision_survives_future_change(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            runtime = NovelRuntime(root)
            runtime.init("관계 시점", approval_mode="autonomous")
            runtime.put_plan(
                1, "1장", {"beats": []}, {"read_points": []}, {"changes": []}
            )
            run = runtime.start_run(1, mode="auto")["id"]

            first = runtime.stage_memory(
                source_run_id=run,
                source_chapter_no=1,
                kind="relationship",
                entity_key="Alice->Bob",
                field="trust",
                new_value={
                    "subject": "Alice",
                    "object": "Bob",
                    "relation": "trust",
                    "value": "low",
                },
                effective_chapter=1,
            )
            runtime.approve_memory(first["id"])

            future = runtime.stage_memory(
                source_run_id=run,
                source_chapter_no=1,
                kind="relationship",
                entity_key="Alice->Bob",
                field="trust",
                new_value={
                    "subject": "Alice",
                    "object": "Bob",
                    "relation": "trust",
                    "value": "high",
                },
                effective_chapter=5,
            )
            runtime.approve_memory(future["id"])

            old_ctx = runtime.context_compile("Alice trust Bob", chapter_no=3)
            new_ctx = runtime.context_compile("Alice trust Bob", chapter_no=5)
            self.assertTrue(any("low" in row["content"] for row in old_ctx["documents"]))
            self.assertFalse(any("high" in row["content"] for row in old_ctx["documents"]))
            self.assertTrue(any("high" in row["content"] for row in new_ctx["documents"]))

            old_edges = runtime.graph_query(
                "neighbors", key="character:Alice", chapter_no=3
            )
            new_edges = runtime.graph_query(
                "neighbors", key="character:Alice", chapter_no=5
            )
            self.assertTrue(any('"value":"low"' in row["payload_json"] or '"value": "low"' in row["payload_json"] for row in old_edges))
            self.assertFalse(any('"value":"high"' in row["payload_json"] or '"value": "high"' in row["payload_json"] for row in old_edges))
            self.assertTrue(any('"value":"high"' in row["payload_json"] or '"value": "high"' in row["payload_json"] for row in new_edges))

    def test_future_promise_and_foreshadow_state_do_not_leak_backward(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            rt = NovelRuntime(root)
            rt.init("복선 시점", approval_mode="autonomous")
            rt.put_plan(1, "1장", {"beats": []}, {"read_points": []}, {"changes": []})
            run = rt.start_run(1, mode="auto")["id"]

            promise_open = rt.stage_memory(
                source_run_id=run,
                source_chapter_no=1,
                kind="promise",
                entity_key="promise-1",
                field="state",
                new_value={"status": "open", "description": "문을 다시 연다", "due_chapter": 2},
                effective_chapter=1,
            )
            rt.approve_memory(promise_open["id"])
            promise_closed = rt.stage_memory(
                source_run_id=run,
                source_chapter_no=1,
                kind="promise",
                entity_key="promise-1",
                field="state",
                new_value={"status": "paid_off", "description": "문을 다시 연다", "due_chapter": 2},
                effective_chapter=4,
            )
            rt.approve_memory(promise_closed["id"])

            fs_open = rt.stage_memory(
                source_run_id=run,
                source_chapter_no=1,
                kind="foreshadow",
                entity_key="foreshadow-1",
                field="state",
                new_value={
                    "state": "PLANTED",
                    "description": "깨진 시계",
                    "planted_chapter": 1,
                    "due_chapter": 2,
                },
                effective_chapter=1,
            )
            rt.approve_memory(fs_open["id"])
            fs_paid = rt.stage_memory(
                source_run_id=run,
                source_chapter_no=1,
                kind="foreshadow",
                entity_key="foreshadow-1",
                field="state",
                new_value={
                    "state": "PAID_OFF",
                    "description": "깨진 시계",
                    "planted_chapter": 1,
                    "due_chapter": 2,
                    "payoff_chapter": 4,
                },
                effective_chapter=4,
            )
            rt.approve_memory(fs_paid["id"])

            old_promise = rt.context_compile("문을 다시 연다", chapter_no=2)
            new_promise = rt.context_compile("문을 다시 연다", chapter_no=4)
            self.assertTrue(any("open" in row["content"] for row in old_promise["documents"]))
            self.assertFalse(any("paid_off" in row["content"] for row in old_promise["documents"]))
            self.assertTrue(any("paid_off" in row["content"] for row in new_promise["documents"]))

            old_fs = rt.context_compile("깨진 시계", chapter_no=2)
            new_fs = rt.context_compile("깨진 시계", chapter_no=4)
            self.assertTrue(any("PLANTED" in row["content"] for row in old_fs["documents"]))
            self.assertFalse(any("PAID_OFF" in row["content"] for row in old_fs["documents"]))
            self.assertTrue(any("PAID_OFF" in row["content"] for row in new_fs["documents"]))

            self.assertTrue(rt.graph_query("promises-due", chapter_no=2))
            self.assertEqual(rt.graph_query("promises-due", chapter_no=4), [])
            self.assertTrue(rt.graph_query("foreshadow-due", chapter_no=2))
            self.assertEqual(rt.graph_query("foreshadow-due", chapter_no=4), [])

    def test_temporal_graph_edge_tombstones_preserve_past_and_remove_future(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            rt = NovelRuntime(root)
            rt.init("그래프 시점", approval_mode="autonomous")
            rt.put_plan(1, "1장", {"beats": []}, {"read_points": []}, {"changes": []})
            run = rt.start_run(1, mode="auto")["id"]

            event_first = rt.stage_memory(
                source_run_id=run,
                source_chapter_no=1,
                kind="event",
                entity_key="event-b",
                field="event",
                new_value={
                    "summary": "B 사건",
                    "occurrence_index": 20,
                    "caused_by": ["event-a"],
                },
                effective_chapter=1,
            )
            rt.approve_memory(event_first["id"])
            event_revised = rt.stage_memory(
                source_run_id=run,
                source_chapter_no=1,
                kind="event",
                entity_key="event-b",
                field="event",
                new_value={
                    "summary": "B 사건 수정",
                    "occurrence_index": 20,
                    "caused_by": [],
                },
                effective_chapter=5,
            )
            rt.approve_memory(event_revised["id"])

            ancestors_old = rt.graph_query(
                "causal-ancestors", key="event:event-b", chapter_no=3
            )
            ancestors_new = rt.graph_query(
                "causal-ancestors", key="event:event-b", chapter_no=5
            )
            self.assertTrue(any(row["src_key"] == "event:event-a" for row in ancestors_old))
            self.assertEqual(ancestors_new, [])

            obj_first = rt.stage_memory(
                source_run_id=run,
                source_chapter_no=1,
                kind="object_state",
                entity_key="key-1",
                field="state",
                new_value={
                    "owner": "Alice",
                    "location": "room-1",
                    "state": {"destroyed": False},
                },
                effective_chapter=1,
            )
            rt.approve_memory(obj_first["id"])
            obj_removed = rt.stage_memory(
                source_run_id=run,
                source_chapter_no=1,
                kind="object_state",
                entity_key="key-1",
                field="state",
                new_value={"state": {"destroyed": True}},
                effective_chapter=5,
            )
            rt.approve_memory(obj_removed["id"])

            old_edges = rt.graph_query("neighbors", key="object:key-1", chapter_no=3)
            new_edges = rt.graph_query("neighbors", key="object:key-1", chapter_no=5)
            self.assertEqual(
                {row["edge_type"] for row in old_edges},
                {"owned_by", "located_at"},
            )
            self.assertEqual(new_edges, [])


if __name__ == "__main__":
    unittest.main()
