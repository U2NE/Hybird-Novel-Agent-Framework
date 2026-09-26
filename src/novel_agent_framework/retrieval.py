from __future__ import annotations

import json
import math
import sqlite3
from typing import Any, Iterable

from .graph import NarrativeGraph
from .vectors import VectorProvider


class ContextCompiler:
    """Query-conditioned narrative retrieval with explicit author/reader scopes."""

    def __init__(
        self,
        con: sqlite3.Connection,
        vector_provider: VectorProvider | None = None,
    ) -> None:
        self.con = con
        self.graph = NarrativeGraph(con)
        self.vector_provider = vector_provider

    def _audience_clause(self, audience: str) -> tuple[str, tuple[str, str]]:
        if audience == "author":
            return "audience IN (?,?)", ("author", "both")
        if audience == "reader":
            return "audience IN (?,?)", ("reader", "both")
        raise ValueError("audience must be author or reader")

    def _allowed_doc_ids(self, cutoff: int, audience: str) -> set[str]:
        clause, args = self._audience_clause(audience)
        rows = self.con.execute(
            f"""
            SELECT s.doc_id
            FROM search_docs s
            JOIN (
              SELECT entity_key, MAX(canonical_revision) AS canonical_revision
              FROM search_docs
              WHERE available_chapter <= ? AND {clause}
              GROUP BY entity_key
            ) latest
              ON latest.entity_key=s.entity_key
             AND latest.canonical_revision=s.canonical_revision
            WHERE s.available_chapter <= ? AND {clause}
            """,
            (cutoff, *args, cutoff, *args),
        )
        return {row["doc_id"] for row in rows}

    def _fts_search(
        self,
        query: str,
        allowed: set[str],
        limit: int,
    ) -> list[dict[str, Any]]:
        if not allowed:
            return []
        fts = self.con.execute(
            "SELECT value FROM schema_meta WHERE key='fts5_available'"
        ).fetchone()
        if fts and fts["value"] == "1":
            try:
                rows = self.con.execute(
                    "SELECT doc_id,doc_type,entity_key,content,bm25(search_fts) AS score "
                    "FROM search_fts WHERE search_fts MATCH ? ORDER BY score LIMIT ?",
                    (query, max(limit * 8, 32)),
                ).fetchall()
                filtered = [dict(row) for row in rows if row["doc_id"] in allowed][:limit]
                if filtered:
                    return filtered
            except sqlite3.OperationalError:
                pass
        placeholders = ",".join("?" for _ in allowed)
        rows = self.con.execute(
            f"SELECT doc_id,doc_type,entity_key,content,0.0 AS score "
            f"FROM search_docs WHERE content LIKE ? AND doc_id IN ({placeholders}) "
            f"ORDER BY doc_id LIMIT ?",
            [f"%{query}%", *sorted(allowed), limit],
        ).fetchall()
        return [dict(row) for row in rows]

    def _vector_search(
        self,
        query: str,
        allowed: set[str],
        limit: int,
    ) -> list[dict[str, Any]]:
        if not self.vector_provider or not allowed:
            return []
        query_vec = self.vector_provider.embed([query])[0]
        rows = self.con.execute(
            """
            SELECT v.doc_id,v.vector_json,d.doc_type,d.entity_key,d.content
            FROM vector_embeddings v
            JOIN search_docs d ON d.doc_id=v.doc_id
            WHERE v.provider=? AND v.model=?
            """,
            (self.vector_provider.name, self.vector_provider.model),
        )
        scored: list[dict[str, Any]] = []
        qnorm = math.sqrt(sum(x * x for x in query_vec)) or 1.0
        for row in rows:
            if row["doc_id"] not in allowed:
                continue
            vec = [float(x) for x in json.loads(row["vector_json"])]
            if len(vec) != len(query_vec):
                continue
            norm = math.sqrt(sum(x * x for x in vec)) or 1.0
            cosine = sum(a * b for a, b in zip(query_vec, vec, strict=True)) / (qnorm * norm)
            scored.append(
                {
                    "doc_id": row["doc_id"],
                    "doc_type": row["doc_type"],
                    "entity_key": row["entity_key"],
                    "content": row["content"],
                    "score": cosine,
                }
            )
        return sorted(scored, key=lambda item: item["score"], reverse=True)[:limit]

    @staticmethod
    def _rrf(*rankings: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
        scores: dict[str, float] = {}
        docs: dict[str, dict[str, Any]] = {}
        for ranking in rankings:
            for rank, item in enumerate(ranking, start=1):
                doc_id = str(item["doc_id"])
                docs[doc_id] = item
                scores[doc_id] = scores.get(doc_id, 0.0) + 1.0 / (60 + rank)
        ordered = sorted(scores, key=scores.get, reverse=True)[:limit]
        return [{**docs[doc_id], "fusion_score": scores[doc_id]} for doc_id in ordered]

    def search(
        self,
        query: str,
        limit: int = 12,
        chapter_no: int | None = None,
        audience: str = "author",
    ) -> list[dict[str, Any]]:
        cutoff = chapter_no if chapter_no is not None else 2**31 - 1
        allowed = self._allowed_doc_ids(cutoff, audience)
        lexical = self._fts_search(query, allowed, limit)
        semantic = self._vector_search(query, allowed, limit)
        if semantic:
            return self._rrf(lexical, semantic, limit=limit)
        return lexical

    def compile(
        self,
        *,
        query: str,
        focus_nodes: list[str] | None = None,
        chapter_no: int | None = None,
        max_docs: int = 16,
        graph_hops: int = 2,
    ) -> dict[str, Any]:
        docs = self.search(query, max_docs, chapter_no=chapter_no, audience="author")
        graph_edges: list[dict[str, Any]] = []
        for node in focus_nodes or []:
            graph_edges.extend(
                self.graph.traverse(
                    node,
                    max_hops=graph_hops,
                    max_chapter=chapter_no,
                )
            )
        preferences = [
            dict(row)
            for row in self.con.execute(
                """
                SELECT preference_key,value_json,confidence,evidence_json
                FROM author_preferences WHERE confidence >= 0.65
                ORDER BY confidence DESC,preference_key
                """
            )
        ]
        style_row = self.con.execute(
            "SELECT id,name,profile_json FROM style_profiles WHERE active=1 ORDER BY created_at DESC LIMIT 1"
        ).fetchone()
        style_profile = (
            {
                "id": style_row["id"],
                "name": style_row["name"],
                "profile": json.loads(style_row["profile_json"]),
            }
            if style_row
            else None
        )
        voice_bibles = [
            {
                **dict(row),
                "profile": json.loads(row["profile_json"]),
            }
            for row in self.con.execute(
                """
                SELECT character_key,relationship_key,profile_json,revision
                FROM voice_bibles WHERE active=1
                ORDER BY character_key,relationship_key
                """
            )
        ]
        return {
            "schema": "novel-context-packet/v2",
            "audience": "author",
            "query": query,
            "chapter_no": chapter_no,
            "documents": docs,
            "graph_edges": graph_edges,
            "author_preferences": preferences,
            "author_style_profile": style_profile,
            "voice_bibles": voice_bibles,
            "source": "sqlite-canonical+fts+optional-vector+temporal-graph",
        }

    def compile_reader(
        self,
        *,
        query: str,
        chapter_no: int,
        max_docs: int = 16,
    ) -> dict[str, Any]:
        docs = self.search(query, max_docs, chapter_no=chapter_no, audience="reader")
        forbidden_types = {
            "author_intent",
            "author_preference",
            "character_state",
            "knowledge",
            "relationship",
            "world_rule",
            "hidden_fact",
            "future_outline",
        }
        leaked = [doc for doc in docs if doc["doc_type"] in forbidden_types]
        if leaked:
            raise RuntimeError("reader context projection leaked author-only document types")
        return {
            "schema": "novel-reader-context/v1",
            "audience": "reader",
            "chapter_no": chapter_no,
            "documents": docs,
            "source": "published-prose+reader-reveals+reader-questions-only",
        }


def _latest_rows(
    con: sqlite3.Connection,
    table: str,
    key_columns: tuple[str, ...],
) -> Iterable[sqlite3.Row]:
    keys = ",".join(key_columns)
    join = " AND ".join(f"l.{key}=t.{key}" for key in key_columns)
    return con.execute(
        f"""
        SELECT t.*
        FROM {table} t
        JOIN (
          SELECT {keys},MAX(revision) revision FROM {table} GROUP BY {keys}
        ) l ON {join} AND l.revision=t.revision
        """
    )


def rebuild_search_projection(con: sqlite3.Connection) -> dict[str, Any]:
    project = con.execute(
        "SELECT canonical_revision FROM project WHERE id='default'"
    ).fetchone()
    revision = int(project["canonical_revision"])
    docs: list[tuple[str, str, str, str, str, int, int]] = []

    def add(
        doc_id: str,
        doc_type: str,
        entity_key: str,
        content: str,
        audience: str,
        chapter: int,
        rev: int,
    ) -> None:
        docs.append((doc_id, doc_type, entity_key, content, audience, chapter, rev))

    for row in con.execute(
        "SELECT entity_key,field,value_json,effective_chapter,revision FROM canonical_facts ORDER BY revision"
    ):
        key = f"fact:{row['entity_key']}:{row['field']}"
        add(
            f"{key}:r{row['revision']}",
            "hidden_fact",
            key,
            f"{row['entity_key']} {row['field']} {json.loads(row['value_json'])}",
            "author",
            int(row["effective_chapter"]),
            int(row["revision"]),
        )

    for row in con.execute(
        "SELECT character_key,fact_key,state,belief_value_json,since_chapter,revision FROM knowledge_states ORDER BY revision"
    ):
        key = f"knowledge:{row['character_key']}:{row['fact_key']}"
        add(
            f"{key}:r{row['revision']}",
            "knowledge",
            key,
            f"{row['character_key']} {row['state']} {row['fact_key']} {row['belief_value_json'] or ''}",
            "author",
            int(row["since_chapter"]),
            int(row["revision"]),
        )

    character_names = {
        row["id"]: row["name"] for row in con.execute("SELECT id,name FROM characters")
    }
    for row in con.execute(
        "SELECT * FROM character_states ORDER BY revision"
    ):
        name = character_names.get(row["character_id"], row["character_id"])
        key = f"character-state:{name}:{row['field']}"
        add(
            f"{key}:r{row['revision']}",
            "character_state",
            key,
            f"{name} {row['field']} {json.loads(row['value_json'])}",
            "author",
            int(row["effective_chapter"]),
            int(row["revision"]),
        )

    for row in con.execute(
        "SELECT * FROM relationship_states ORDER BY revision"
    ):
        key = f"relationship:{row['subject_key']}:{row['relation']}:{row['object_key']}"
        add(
            f"{key}:r{row['revision']}",
            "relationship",
            key,
            f"{row['subject_key']} {row['relation']} {row['object_key']} {json.loads(row['value_json'])}",
            "author",
            int(row["effective_chapter"]),
            int(row["revision"]),
        )

    for row in con.execute("SELECT * FROM events ORDER BY revision"):
        key = f"event:{row['id']}"
        add(
            f"{key}:r{row['revision']}",
            "event",
            key,
            f"{row['summary']} {row['payload_json']}",
            "author",
            int(row["chapter_no"]),
            int(row["revision"]),
        )

    for row in con.execute("SELECT * FROM reveals ORDER BY revision"):
        key = f"reveal:{row['fact_key']}"
        add(
            f"{key}:r{row['revision']}",
            "reveal",
            key,
            f"{row['fact_key']} revealed {row['payload_json']}",
            "reader",
            int(row["chapter_no"]),
            int(row["revision"]),
        )

    for row in con.execute("SELECT * FROM object_states ORDER BY revision"):
        key = f"object:{row['object_key']}"
        add(
            f"{key}:r{row['revision']}",
            "object_state",
            key,
            f"{row['object_key']} owner={row['owner_key']} location={row['location_key']} {row['state_json']}",
            "author",
            int(row["effective_chapter"]),
            int(row["revision"]),
        )

    for row in con.execute("SELECT * FROM world_rules ORDER BY revision"):
        key = f"world-rule:{row['rule_key']}"
        add(
            f"{key}:r{row['revision']}",
            "world_rule",
            key,
            row["rule_text"],
            "author",
            int(row["effective_chapter"]),
            int(row["revision"]),
        )

    for table, key_col, doc_type in (
        ("plot_threads", "thread_key", "plot_thread"),
        ("foreshadows", "foreshadow_key", "foreshadow"),
        ("promises", "promise_key", "promise"),
        ("reader_questions", "question_key", "reader_question"),
    ):
        rows = con.execute(
            f"""
            SELECT t.*,COALESCE(pm.effective_chapter,pm.source_chapter_no,0) AS source_chapter
            FROM {table} t
            LEFT JOIN pending_memory pm ON pm.id=t.source_memory_id
            ORDER BY t.revision
            """
        )
        for row in rows:
            key = f"{doc_type}:{row[key_col]}"
            if table == "plot_threads":
                content = f"{row['status']} {row['description']}"
                chapter = int(row["effective_chapter"])
                audience = "author"
            elif table == "foreshadows":
                content = f"{row['state']} {row['description']} due={row['due_chapter']}"
                chapter = int(row["source_chapter"])
                audience = "author"
            elif table == "promises":
                content = f"{row['status']} {row['description']} due={row['due_chapter']}"
                chapter = int(row["source_chapter"])
                audience = "author"
            else:
                content = f"{row['status']} {row['text']}"
                chapter = int(row["source_chapter"])
                audience = "reader"
            add(
                f"{key}:r{row['revision']}",
                doc_type,
                key,
                content,
                audience,
                chapter,
                int(row["revision"]),
            )

    for row in con.execute(
        "SELECT id,idea_text,critique_json FROM author_intents WHERE status='accepted'"
    ):
        add(
            f"author-intent:{row['id']}",
            "author_intent",
            f"author-intent:{row['id']}",
            f"{row['idea_text']} {row['critique_json'] or ''}",
            "author",
            0,
            revision,
        )

    for row in con.execute(
        "SELECT preference_key,value_json,confidence,evidence_json FROM author_preferences"
    ):
        add(
            f"author-preference:{row['preference_key']}",
            "author_preference",
            f"author-preference:{row['preference_key']}",
            f"{row['preference_key']} {row['value_json']} confidence={row['confidence']}",
            "author",
            0,
            revision,
        )

    for row in con.execute(
        """
        SELECT cr.id,cr.chapter_no,cr.revision,cr.prose
        FROM publications p
        JOIN chapter_revisions cr ON cr.id=p.chapter_revision_id
        ORDER BY cr.chapter_no
        """
    ):
        key = f"published-chapter:{row['chapter_no']}"
        add(
            f"{key}:r{row['revision']}",
            "published_prose",
            key,
            row["prose"],
            "reader",
            int(row["chapter_no"]),
            int(row["revision"]),
        )

    con.execute("DELETE FROM search_docs")
    con.executemany(
        """
        INSERT INTO search_docs(
          doc_id,doc_type,entity_key,content,audience,available_chapter,canonical_revision
        ) VALUES(?,?,?,?,?,?,?)
        """,
        docs,
    )
    fts = con.execute(
        "SELECT value FROM schema_meta WHERE key='fts5_available'"
    ).fetchone()
    if fts and fts["value"] == "1":
        con.execute("DELETE FROM search_fts")
        con.executemany(
            "INSERT INTO search_fts(doc_id,doc_type,entity_key,content) VALUES(?,?,?,?)",
            [(a, b, c, d) for a, b, c, d, _, _, _ in docs],
        )
    con.execute(
        """
        INSERT INTO projection_meta(name,canonical_revision,status)
        VALUES('search',?,'ready')
        ON CONFLICT(name) DO UPDATE SET
          canonical_revision=excluded.canonical_revision,
          status='ready',
          updated_at=CURRENT_TIMESTAMP
        """,
        (revision,),
    )
    counts: dict[str, int] = {}
    for _, doc_type, *_ in docs:
        counts[doc_type] = counts.get(doc_type, 0) + 1
    return {"documents": len(docs), "by_type": counts, "canonical_revision": revision}
