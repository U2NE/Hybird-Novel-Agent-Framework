from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from novel_agent_framework.db import migrate_database


class MigrationTests(unittest.TestCase):
    def test_v1_to_v4(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            path = root / ".novel" / "state" / "story.sqlite3"
            path.parent.mkdir(parents=True)
            con = sqlite3.connect(path)
            try:
                con.executescript(
                    """
                    PRAGMA foreign_keys=ON;
                    CREATE TABLE schema_meta(key TEXT PRIMARY KEY,value TEXT NOT NULL);
                    INSERT INTO schema_meta VALUES('schema_version','1');

                    CREATE TABLE chapter_runs(
                      id TEXT PRIMARY KEY,
                      chapter_no INTEGER NOT NULL,
                      plan_revision INTEGER NOT NULL,
                      mode TEXT NOT NULL,
                      stage TEXT NOT NULL,
                      retry_budget INTEGER NOT NULL DEFAULT 3,
                      started_at TEXT,
                      updated_at TEXT,
                      completed_at TEXT
                    );
                    CREATE TABLE search_docs(
                      doc_id TEXT PRIMARY KEY,
                      doc_type TEXT NOT NULL,
                      entity_key TEXT NOT NULL,
                      content TEXT NOT NULL,
                      available_chapter INTEGER NOT NULL DEFAULT 0,
                      canonical_revision INTEGER NOT NULL
                    );
                    CREATE TABLE tournaments(
                      run_id TEXT PRIMARY KEY,
                      winner_lens TEXT NOT NULL,
                      pairwise_json TEXT NOT NULL DEFAULT '{}',
                      reason TEXT NOT NULL,
                      adjudicator_model TEXT NOT NULL,
                      adjudicator_effort TEXT NOT NULL
                    );
                    """
                )
                con.commit()
            finally:
                con.close()

            applied = migrate_database(root)
            self.assertEqual(applied, [2, 3, 4])
            con = sqlite3.connect(path)
            try:
                version = con.execute(
                    "SELECT value FROM schema_meta WHERE key='schema_version'"
                ).fetchone()[0]
                self.assertEqual(version, "4")
                search_cols = {
                    row[1] for row in con.execute("PRAGMA table_info(search_docs)")
                }
                task_cols = {
                    row[1] for row in con.execute("PRAGMA table_info(agent_tasks)")
                }
                tournament_cols = {
                    row[1] for row in con.execute("PRAGMA table_info(tournaments)")
                }
                graph_cols = {
                    row[1] for row in con.execute("PRAGMA table_info(graph_edges)")
                }
                self.assertIn("audience", search_cols)
                self.assertIn("output_json", task_cols)
                self.assertIn("aggregation_json", tournament_cols)
                self.assertIn("active", graph_cols)
            finally:
                con.close()

    def test_v3_to_v4_preserves_existing_graph_edges(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            path = root / ".novel" / "state" / "story.sqlite3"
            path.parent.mkdir(parents=True)
            con = sqlite3.connect(path)
            try:
                con.executescript(
                    """
                    CREATE TABLE schema_meta(key TEXT PRIMARY KEY,value TEXT NOT NULL);
                    INSERT INTO schema_meta VALUES('schema_version','3');
                    CREATE TABLE graph_edges(
                      edge_key TEXT NOT NULL,
                      src_key TEXT NOT NULL,
                      dst_key TEXT NOT NULL,
                      edge_type TEXT NOT NULL,
                      effective_chapter INTEGER NOT NULL,
                      payload_json TEXT NOT NULL DEFAULT '{}',
                      revision INTEGER NOT NULL,
                      source_memory_id TEXT NOT NULL,
                      PRIMARY KEY(edge_key, revision)
                    );
                    INSERT INTO graph_edges(
                      edge_key,src_key,dst_key,edge_type,effective_chapter,
                      payload_json,revision,source_memory_id
                    ) VALUES('e1','a','b','causes',1,'{}',1,'m1');
                    """
                )
                con.commit()
            finally:
                con.close()

            self.assertEqual(migrate_database(root), [4])
            con = sqlite3.connect(path)
            try:
                version = con.execute(
                    "SELECT value FROM schema_meta WHERE key='schema_version'"
                ).fetchone()[0]
                self.assertEqual(version, "4")
                row = con.execute(
                    "SELECT edge_key,active FROM graph_edges WHERE edge_key='e1'"
                ).fetchone()
                self.assertEqual(row, ("e1", 1))
            finally:
                con.close()


if __name__ == "__main__":
    unittest.main()
