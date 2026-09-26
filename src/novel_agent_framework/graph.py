from __future__ import annotations

import json
import sqlite3
from collections import deque
from typing import Any


def latest_revision_subquery(table: str, key_col: str) -> str:
    return (
        f"SELECT {key_col}, MAX(revision) AS revision FROM {table} "
        f"GROUP BY {key_col}"
    )


class NarrativeGraph:
    def __init__(self, con: sqlite3.Connection) -> None:
        self.con = con

    def neighbors(
        self,
        node_key: str,
        *,
        edge_type: str | None = None,
        max_chapter: int | None = None,
    ) -> list[dict[str, Any]]:
        clauses = ["e.src_key = ?", "e.active = 1"]
        params: list[Any] = [node_key]
        if edge_type:
            clauses.append("e.edge_type = ?")
            params.append(edge_type)
        if max_chapter is not None:
            clauses.append("e.effective_chapter <= ?")
            params.append(max_chapter)
        if max_chapter is None:
            latest_sql = """
              SELECT edge_key, MAX(revision) AS revision
              FROM graph_edges
              GROUP BY edge_key
            """
            latest_params: list[Any] = []
        else:
            latest_sql = """
              SELECT edge_key, MAX(revision) AS revision
              FROM graph_edges
              WHERE effective_chapter <= ?
              GROUP BY edge_key
            """
            latest_params = [max_chapter]
        sql = f"""
        SELECT e.*
        FROM graph_edges e
        JOIN ({latest_sql}) latest
          ON latest.edge_key=e.edge_key AND latest.revision=e.revision
        WHERE {' AND '.join(clauses)}
        ORDER BY e.edge_type, e.dst_key
        """
        return [dict(row) for row in self.con.execute(sql, [*latest_params, *params])]

    def traverse(
        self,
        start_key: str,
        *,
        edge_types: set[str] | None = None,
        max_hops: int = 3,
        max_chapter: int | None = None,
    ) -> list[dict[str, Any]]:
        if max_hops < 1 or max_hops > 12:
            raise ValueError("max_hops must be in 1..12")
        queue = deque([(start_key, 0)])
        visited = {start_key}
        found: list[dict[str, Any]] = []
        while queue:
            current, depth = queue.popleft()
            if depth >= max_hops:
                continue
            for edge in self.neighbors(current, max_chapter=max_chapter):
                if edge_types and edge["edge_type"] not in edge_types:
                    continue
                item = {**edge, "hop": depth + 1}
                found.append(item)
                dst = edge["dst_key"]
                if dst not in visited:
                    visited.add(dst)
                    queue.append((dst, depth + 1))
        return found

    def character_knowledge(
        self, character_key: str, chapter_no: int
    ) -> list[dict[str, Any]]:
        sql = """
        SELECT k.*
        FROM knowledge_states k
        JOIN (
          SELECT character_key, fact_key, MAX(revision) AS revision
          FROM knowledge_states
          WHERE since_chapter <= ?
          GROUP BY character_key, fact_key
        ) latest
          ON latest.character_key=k.character_key
         AND latest.fact_key=k.fact_key
         AND latest.revision=k.revision
        WHERE k.character_key=?
        ORDER BY k.fact_key
        """
        rows = []
        for row in self.con.execute(sql, (chapter_no, character_key)):
            item = dict(row)
            if item.get("belief_value_json"):
                item["belief_value"] = json.loads(item["belief_value_json"])
            rows.append(item)
        return rows


    def causal_ancestors(
        self,
        event_key: str,
        max_hops: int = 3,
        max_chapter: int | None = None,
    ) -> list[dict[str, Any]]:
        if max_hops < 1 or max_hops > 12:
            raise ValueError("max_hops must be in 1..12")
        queue = deque([(event_key, 0)])
        visited = {event_key}
        found: list[dict[str, Any]] = []
        while queue:
            current, depth = queue.popleft()
            if depth >= max_hops:
                continue
            if max_chapter is None:
                latest_sql = """
                  SELECT edge_key, MAX(revision) AS revision
                  FROM graph_edges GROUP BY edge_key
                """
                params: list[Any] = [current]
                chapter_clause = ""
            else:
                latest_sql = """
                  SELECT edge_key, MAX(revision) AS revision
                  FROM graph_edges
                  WHERE effective_chapter <= ?
                  GROUP BY edge_key
                """
                params = [max_chapter, current, max_chapter]
                chapter_clause = "AND e.effective_chapter <= ?"
            rows = self.con.execute(
                f"""
                SELECT e.*
                FROM graph_edges e
                JOIN ({latest_sql}) latest
                  ON latest.edge_key=e.edge_key AND latest.revision=e.revision
                WHERE e.dst_key=?
                  AND e.active=1
                  AND e.edge_type IN ('causes','enables','blocks','depends_on')
                  {chapter_clause}
                ORDER BY e.src_key
                """,
                params,
            )
            for row in rows:
                edge = dict(row)
                edge["hop"] = depth + 1
                found.append(edge)
                src = edge["src_key"]
                if src not in visited:
                    visited.add(src)
                    queue.append((src, depth + 1))
        return found

    def causal_descendants(
        self,
        event_key: str,
        max_hops: int = 3,
        max_chapter: int | None = None,
    ) -> list[dict[str, Any]]:
        return self.traverse(
            event_key,
            edge_types={"causes", "enables", "blocks", "depends_on"},
            max_hops=max_hops,
            max_chapter=max_chapter,
        )
