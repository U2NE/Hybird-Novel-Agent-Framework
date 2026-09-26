from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from .errors import SchemaError

SCHEMA_VERSION = 4

BASE_SCHEMA = r"""
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS schema_meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS project (
    id TEXT PRIMARY KEY CHECK (id = 'default'),
    title TEXT NOT NULL,
    language TEXT NOT NULL CHECK (language = 'ko'),
    approval_mode TEXT NOT NULL CHECK (approval_mode IN ('every_chapter','autonomous')),
    canonical_revision INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS story_compass (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    premise TEXT NOT NULL DEFAULT '',
    ending_promise TEXT NOT NULL DEFAULT '',
    core_conflict TEXT NOT NULL DEFAULT '',
    themes_json TEXT NOT NULL DEFAULT '[]',
    revision INTEGER NOT NULL DEFAULT 1,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS volumes (
    id TEXT PRIMARY KEY,
    ordinal INTEGER NOT NULL UNIQUE,
    title TEXT NOT NULL,
    goal TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'planned',
    revision INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS arcs (
    id TEXT PRIMARY KEY,
    volume_id TEXT REFERENCES volumes(id) ON DELETE CASCADE,
    ordinal INTEGER NOT NULL,
    title TEXT NOT NULL,
    goal TEXT NOT NULL DEFAULT '',
    turning_points_json TEXT NOT NULL DEFAULT '[]',
    status TEXT NOT NULL DEFAULT 'planned',
    revision INTEGER NOT NULL DEFAULT 1,
    UNIQUE(volume_id, ordinal)
);

CREATE TABLE IF NOT EXISTS rolling_outlines (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    anchor_chapter INTEGER NOT NULL,
    horizon_chapters INTEGER NOT NULL CHECK (horizon_chapters > 0),
    outline_json TEXT NOT NULL,
    revision INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active','superseded')),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS model_route_decisions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT REFERENCES chapter_runs(id) ON DELETE CASCADE,
    stage TEXT NOT NULL,
    role TEXT NOT NULL,
    model TEXT NOT NULL,
    reasoning_effort TEXT NOT NULL,
    policy_key TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS chapter_plans (
    chapter_no INTEGER PRIMARY KEY CHECK (chapter_no > 0),
    title TEXT NOT NULL,
    plan_json TEXT NOT NULL,
    contract_json TEXT NOT NULL,
    expected_delta_json TEXT NOT NULL DEFAULT '{"changes":[]}',
    status TEXT NOT NULL DEFAULT 'generated'
        CHECK (status IN ('generated','approved','superseded')),
    approval_kind TEXT CHECK (approval_kind IN ('human','auto')),
    approved_at TEXT,
    revision INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS chapter_runs (
    id TEXT PRIMARY KEY,
    chapter_no INTEGER NOT NULL REFERENCES chapter_plans(chapter_no),
    plan_revision INTEGER NOT NULL,
    mode TEXT NOT NULL CHECK (mode IN ('manual','auto')),
    stage TEXT NOT NULL,
    retry_budget INTEGER NOT NULL DEFAULT 3,
    started_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    completed_at TEXT
);

CREATE TABLE IF NOT EXISTS transitions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL REFERENCES chapter_runs(id) ON DELETE CASCADE,
    from_stage TEXT,
    to_stage TEXT NOT NULL,
    idempotency_key TEXT NOT NULL,
    evidence_hash TEXT,
    payload_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(run_id, idempotency_key)
);

CREATE TABLE IF NOT EXISTS checkpoints (
    run_id TEXT PRIMARY KEY REFERENCES chapter_runs(id) ON DELETE CASCADE,
    stage TEXT NOT NULL,
    payload_json TEXT NOT NULL DEFAULT '{}',
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS chapter_candidates (
    id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES chapter_runs(id) ON DELETE CASCADE,
    lens TEXT NOT NULL CHECK (lens IN ('A','B','C')),
    strategy TEXT NOT NULL,
    prose TEXT NOT NULL,
    prose_hash TEXT NOT NULL,
    declared_delta_json TEXT NOT NULL DEFAULT '{"changes":[]}',
    model TEXT NOT NULL,
    reasoning_effort TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(run_id, lens)
);

CREATE TABLE IF NOT EXISTS candidate_preflights (
    run_id TEXT NOT NULL REFERENCES chapter_runs(id) ON DELETE CASCADE,
    lens TEXT NOT NULL CHECK (lens IN ('A','B','C')),
    observation_json TEXT NOT NULL,
    result_json TEXT NOT NULL,
    passed INTEGER NOT NULL CHECK (passed IN (0,1)),
    artifact_hash TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY(run_id, lens)
);

CREATE TABLE IF NOT EXISTS tournaments (
    run_id TEXT PRIMARY KEY REFERENCES chapter_runs(id) ON DELETE CASCADE,
    winner_lens TEXT NOT NULL CHECK (winner_lens IN ('A','B','C')),
    pairwise_json TEXT NOT NULL DEFAULT '{}',
    aggregation_json TEXT NOT NULL DEFAULT '{}',
    hard_adjudication_json TEXT,
    reason TEXT NOT NULL,
    adjudicator_model TEXT NOT NULL,
    adjudicator_effort TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS frozen_drafts (
    run_id TEXT PRIMARY KEY REFERENCES chapter_runs(id) ON DELETE CASCADE,
    candidate_id TEXT NOT NULL REFERENCES chapter_candidates(id),
    prose TEXT NOT NULL,
    prose_hash TEXT NOT NULL,
    frozen_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS reviews (
    id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES chapter_runs(id) ON DELETE CASCADE,
    reviewer TEXT NOT NULL,
    review_json TEXT NOT NULL,
    artifact_hash TEXT NOT NULL,
    model TEXT NOT NULL,
    reasoning_effort TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(run_id, reviewer)
);

CREATE TABLE IF NOT EXISTS review_adjudications (
    run_id TEXT PRIMARY KEY REFERENCES chapter_runs(id) ON DELETE CASCADE,
    bundle_json TEXT NOT NULL,
    decision_json TEXT NOT NULL,
    conflicts_json TEXT NOT NULL DEFAULT '[]',
    cross_exam_json TEXT,
    cross_exam_used INTEGER NOT NULL DEFAULT 0 CHECK (cross_exam_used IN (0,1)),
    artifact_hash TEXT NOT NULL,
    model TEXT NOT NULL,
    reasoning_effort TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS revision_manifests (
    run_id TEXT PRIMARY KEY REFERENCES chapter_runs(id) ON DELETE CASCADE,
    manifest_json TEXT NOT NULL,
    artifact_hash TEXT NOT NULL,
    repair_level TEXT NOT NULL CHECK (repair_level IN ('none','sentence','paragraph','chapter')),
    repair_attempts INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS repaired_drafts (
    run_id TEXT PRIMARY KEY REFERENCES chapter_runs(id) ON DELETE CASCADE,
    prose TEXT NOT NULL,
    prose_hash TEXT NOT NULL,
    declared_delta_json TEXT NOT NULL,
    repair_level TEXT NOT NULL CHECK (repair_level IN ('none','sentence','paragraph','chapter')),
    repair_attempts INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS regression_checks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL REFERENCES chapter_runs(id) ON DELETE CASCADE,
    attempt INTEGER NOT NULL,
    check_json TEXT NOT NULL,
    passed INTEGER NOT NULL CHECK (passed IN (0,1)),
    artifact_hash TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(run_id, attempt)
);

CREATE TABLE IF NOT EXISTS state_scans (
    run_id TEXT PRIMARY KEY REFERENCES chapter_runs(id) ON DELETE CASCADE,
    observed_delta_json TEXT NOT NULL,
    observation_json TEXT NOT NULL DEFAULT '{}',
    artifact_hash TEXT NOT NULL,
    model TEXT NOT NULL,
    reasoning_effort TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS delta_validations (
    run_id TEXT PRIMARY KEY REFERENCES chapter_runs(id) ON DELETE CASCADE,
    observed_delta_json TEXT NOT NULL,
    result_json TEXT NOT NULL,
    passed INTEGER NOT NULL CHECK (passed IN (0,1)),
    validated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS chapter_revisions (
    id TEXT PRIMARY KEY,
    chapter_no INTEGER NOT NULL,
    revision INTEGER NOT NULL,
    run_id TEXT NOT NULL UNIQUE REFERENCES chapter_runs(id),
    prose TEXT NOT NULL,
    prose_hash TEXT NOT NULL,
    stale INTEGER NOT NULL DEFAULT 0 CHECK (stale IN (0,1)),
    stale_reason TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(chapter_no, revision)
);

CREATE TABLE IF NOT EXISTS publications (
    chapter_no INTEGER PRIMARY KEY,
    chapter_revision_id TEXT NOT NULL UNIQUE REFERENCES chapter_revisions(id),
    published_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS read_points (
    chapter_revision_id TEXT NOT NULL REFERENCES chapter_revisions(id) ON DELETE CASCADE,
    entity_kind TEXT NOT NULL,
    entity_key TEXT NOT NULL,
    entity_revision INTEGER NOT NULL,
    PRIMARY KEY(chapter_revision_id, entity_kind, entity_key)
);

CREATE TABLE IF NOT EXISTS pending_memory (
    id TEXT PRIMARY KEY,
    source_chapter_no INTEGER NOT NULL,
    source_run_id TEXT NOT NULL REFERENCES chapter_runs(id),
    kind TEXT NOT NULL,
    entity_key TEXT NOT NULL,
    field TEXT NOT NULL,
    old_value_json TEXT,
    new_value_json TEXT NOT NULL,
    evidence TEXT,
    effective_chapter INTEGER,
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending','approved','rejected')),
    decided_at TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS canonical_facts (
    entity_key TEXT NOT NULL,
    field TEXT NOT NULL,
    value_json TEXT NOT NULL,
    effective_chapter INTEGER NOT NULL,
    revision INTEGER NOT NULL,
    source_memory_id TEXT NOT NULL REFERENCES pending_memory(id),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY(entity_key, field, revision)
);

CREATE TABLE IF NOT EXISTS characters (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    definition_json TEXT NOT NULL DEFAULT '{}',
    revision INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS character_states (
    character_id TEXT NOT NULL REFERENCES characters(id),
    field TEXT NOT NULL,
    value_json TEXT NOT NULL,
    effective_chapter INTEGER NOT NULL,
    revision INTEGER NOT NULL,
    source_memory_id TEXT NOT NULL REFERENCES pending_memory(id),
    PRIMARY KEY(character_id, field, revision)
);

CREATE TABLE IF NOT EXISTS knowledge_states (
    character_key TEXT NOT NULL,
    fact_key TEXT NOT NULL,
    state TEXT NOT NULL CHECK (state IN ('knows','suspects','falsely_believes','unknown')),
    belief_value_json TEXT,
    since_chapter INTEGER NOT NULL,
    evidence TEXT,
    revision INTEGER NOT NULL,
    source_memory_id TEXT NOT NULL REFERENCES pending_memory(id),
    PRIMARY KEY(character_key, fact_key, revision)
);

CREATE TABLE IF NOT EXISTS relationship_states (
    subject_key TEXT NOT NULL,
    object_key TEXT NOT NULL,
    relation TEXT NOT NULL,
    value_json TEXT NOT NULL,
    effective_chapter INTEGER NOT NULL,
    revision INTEGER NOT NULL,
    source_memory_id TEXT NOT NULL REFERENCES pending_memory(id),
    PRIMARY KEY(subject_key, object_key, relation, revision)
);

CREATE TABLE IF NOT EXISTS events (
    id TEXT NOT NULL,
    occurrence_index INTEGER NOT NULL,
    chapter_no INTEGER NOT NULL,
    summary TEXT NOT NULL,
    payload_json TEXT NOT NULL DEFAULT '{}',
    revision INTEGER NOT NULL,
    source_memory_id TEXT NOT NULL REFERENCES pending_memory(id),
    PRIMARY KEY(id, revision)
);

CREATE TABLE IF NOT EXISTS reveals (
    fact_key TEXT NOT NULL,
    reader_reveal_index INTEGER NOT NULL,
    chapter_no INTEGER NOT NULL,
    payload_json TEXT NOT NULL DEFAULT '{}',
    revision INTEGER NOT NULL,
    source_memory_id TEXT NOT NULL REFERENCES pending_memory(id),
    PRIMARY KEY(fact_key, revision)
);

CREATE TABLE IF NOT EXISTS object_states (
    object_key TEXT NOT NULL,
    owner_key TEXT,
    location_key TEXT,
    state_json TEXT NOT NULL DEFAULT '{}',
    effective_chapter INTEGER NOT NULL,
    revision INTEGER NOT NULL,
    source_memory_id TEXT NOT NULL REFERENCES pending_memory(id),
    PRIMARY KEY(object_key, revision)
);

CREATE TABLE IF NOT EXISTS world_rules (
    rule_key TEXT NOT NULL,
    rule_text TEXT NOT NULL,
    active INTEGER NOT NULL DEFAULT 1,
    effective_chapter INTEGER NOT NULL,
    revision INTEGER NOT NULL,
    source_memory_id TEXT NOT NULL REFERENCES pending_memory(id),
    PRIMARY KEY(rule_key, revision)
);

CREATE TABLE IF NOT EXISTS plot_threads (
    thread_key TEXT NOT NULL,
    status TEXT NOT NULL,
    description TEXT NOT NULL,
    effective_chapter INTEGER NOT NULL,
    revision INTEGER NOT NULL,
    source_memory_id TEXT NOT NULL REFERENCES pending_memory(id),
    PRIMARY KEY(thread_key, revision)
);

CREATE TABLE IF NOT EXISTS foreshadows (
    foreshadow_key TEXT NOT NULL,
    state TEXT NOT NULL,
    description TEXT NOT NULL,
    planted_chapter INTEGER,
    due_chapter INTEGER,
    payoff_chapter INTEGER,
    revision INTEGER NOT NULL,
    source_memory_id TEXT NOT NULL REFERENCES pending_memory(id),
    PRIMARY KEY(foreshadow_key, revision)
);

CREATE TABLE IF NOT EXISTS promises (
    promise_key TEXT NOT NULL,
    status TEXT NOT NULL,
    description TEXT NOT NULL,
    due_chapter INTEGER,
    payoff_event_key TEXT,
    revision INTEGER NOT NULL,
    source_memory_id TEXT NOT NULL REFERENCES pending_memory(id),
    PRIMARY KEY(promise_key, revision)
);

CREATE TABLE IF NOT EXISTS reader_questions (
    question_key TEXT NOT NULL,
    status TEXT NOT NULL,
    text TEXT NOT NULL,
    opened_chapter INTEGER NOT NULL,
    closed_chapter INTEGER,
    revision INTEGER NOT NULL,
    source_memory_id TEXT NOT NULL REFERENCES pending_memory(id),
    PRIMARY KEY(question_key, revision)
);

CREATE TABLE IF NOT EXISTS graph_nodes (
    node_key TEXT NOT NULL,
    node_type TEXT NOT NULL,
    label TEXT NOT NULL,
    payload_json TEXT NOT NULL DEFAULT '{}',
    revision INTEGER NOT NULL,
    source_memory_id TEXT NOT NULL REFERENCES pending_memory(id),
    PRIMARY KEY(node_key, revision)
);

CREATE TABLE IF NOT EXISTS graph_edges (
    edge_key TEXT NOT NULL,
    src_key TEXT NOT NULL,
    dst_key TEXT NOT NULL,
    edge_type TEXT NOT NULL,
    effective_chapter INTEGER NOT NULL,
    payload_json TEXT NOT NULL DEFAULT '{}',
    active INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0,1)),
    revision INTEGER NOT NULL,
    source_memory_id TEXT NOT NULL REFERENCES pending_memory(id),
    PRIMARY KEY(edge_key, revision)
);

CREATE TABLE IF NOT EXISTS author_intents (
    id TEXT PRIMARY KEY,
    idea_text TEXT NOT NULL,
    critique_json TEXT,
    status TEXT NOT NULL CHECK (status IN ('proposed','reviewed','accepted','rejected')),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS author_preferences (
    preference_key TEXT PRIMARY KEY,
    value_json TEXT NOT NULL,
    confidence REAL NOT NULL CHECK (confidence >= 0 AND confidence <= 1),
    evidence_json TEXT NOT NULL DEFAULT '[]',
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS agent_tasks (
    id TEXT PRIMARY KEY,
    run_id TEXT REFERENCES chapter_runs(id) ON DELETE CASCADE,
    chapter_no INTEGER,
    stage TEXT NOT NULL,
    role TEXT NOT NULL,
    route_key TEXT NOT NULL,
    model TEXT NOT NULL,
    reasoning_effort TEXT NOT NULL,
    input_json TEXT NOT NULL,
    output_schema TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending','completed','cancelled','failed')),
    attempt INTEGER NOT NULL DEFAULT 1,
    output_json TEXT,
    output_hash TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    completed_at TEXT,
    UNIQUE(run_id, stage, role, attempt)
);

CREATE TABLE IF NOT EXISTS character_simulations (
    id TEXT PRIMARY KEY,
    chapter_no INTEGER NOT NULL,
    character_key TEXT NOT NULL,
    perception_json TEXT NOT NULL,
    memory_json TEXT NOT NULL,
    intention_json TEXT NOT NULL,
    action_json TEXT NOT NULL,
    consequence_json TEXT NOT NULL,
    accepted INTEGER NOT NULL DEFAULT 0 CHECK (accepted IN (0,1)),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS replan_requests (
    id TEXT PRIMARY KEY,
    source_type TEXT NOT NULL,
    source_id TEXT NOT NULL,
    anchor_chapter INTEGER NOT NULL,
    reason TEXT NOT NULL,
    impact_json TEXT NOT NULL DEFAULT '{}',
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending','applied','rejected')),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    decided_at TEXT
);

CREATE TABLE IF NOT EXISTS style_profiles (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    profile_json TEXT NOT NULL,
    sample_hash TEXT NOT NULL,
    active INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0,1)),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS voice_bibles (
    character_key TEXT NOT NULL,
    relationship_key TEXT NOT NULL DEFAULT '*',
    profile_json TEXT NOT NULL,
    revision INTEGER NOT NULL,
    active INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0,1)),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY(character_key, relationship_key, revision)
);

CREATE TABLE IF NOT EXISTS projection_meta (
    name TEXT PRIMARY KEY,
    canonical_revision INTEGER NOT NULL,
    status TEXT NOT NULL,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS search_docs (
    doc_id TEXT PRIMARY KEY,
    doc_type TEXT NOT NULL,
    entity_key TEXT NOT NULL,
    content TEXT NOT NULL,
    audience TEXT NOT NULL DEFAULT 'author'
        CHECK (audience IN ('author','reader','both')),
    available_chapter INTEGER NOT NULL DEFAULT 0,
    canonical_revision INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS vector_embeddings (
    doc_id TEXT NOT NULL REFERENCES search_docs(doc_id) ON DELETE CASCADE,
    provider TEXT NOT NULL,
    model TEXT NOT NULL,
    dimensions INTEGER NOT NULL,
    vector_json TEXT NOT NULL,
    canonical_revision INTEGER NOT NULL,
    PRIMARY KEY(doc_id, provider, model)
);

CREATE INDEX IF NOT EXISTS idx_pending_memory_status ON pending_memory(status);
CREATE INDEX IF NOT EXISTS idx_knowledge_lookup ON knowledge_states(character_key, fact_key, since_chapter, revision);
CREATE INDEX IF NOT EXISTS idx_graph_edges_src ON graph_edges(src_key, edge_type, revision);
CREATE INDEX IF NOT EXISTS idx_graph_edges_dst ON graph_edges(dst_key, edge_type, revision);
CREATE INDEX IF NOT EXISTS idx_read_points_entity ON read_points(entity_kind, entity_key, entity_revision);
CREATE INDEX IF NOT EXISTS idx_chapter_revision_stale ON chapter_revisions(stale, chapter_no);
CREATE INDEX IF NOT EXISTS idx_agent_tasks_status ON agent_tasks(status,run_id,stage);
CREATE INDEX IF NOT EXISTS idx_replan_status ON replan_requests(status,anchor_chapter);
CREATE INDEX IF NOT EXISTS idx_search_docs_scope ON search_docs(audience,available_chapter,canonical_revision);
"""



MIGRATIONS: dict[int, str] = {
    2: r"""
    ALTER TABLE search_docs ADD COLUMN audience TEXT NOT NULL DEFAULT 'author';

    CREATE TABLE IF NOT EXISTS candidate_preflights (
        run_id TEXT NOT NULL REFERENCES chapter_runs(id) ON DELETE CASCADE,
        lens TEXT NOT NULL CHECK (lens IN ('A','B','C')),
        observation_json TEXT NOT NULL,
        result_json TEXT NOT NULL,
        passed INTEGER NOT NULL CHECK (passed IN (0,1)),
        artifact_hash TEXT NOT NULL,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY(run_id, lens)
    );
    ALTER TABLE tournaments ADD COLUMN aggregation_json TEXT NOT NULL DEFAULT '{}';
    ALTER TABLE tournaments ADD COLUMN hard_adjudication_json TEXT;

    CREATE TABLE IF NOT EXISTS review_adjudications (
        run_id TEXT PRIMARY KEY REFERENCES chapter_runs(id) ON DELETE CASCADE,
        bundle_json TEXT NOT NULL,
        decision_json TEXT NOT NULL,
        conflicts_json TEXT NOT NULL DEFAULT '[]',
        cross_exam_json TEXT,
        cross_exam_used INTEGER NOT NULL DEFAULT 0 CHECK (cross_exam_used IN (0,1)),
        artifact_hash TEXT NOT NULL,
        model TEXT NOT NULL,
        reasoning_effort TEXT NOT NULL,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    );
    CREATE TABLE IF NOT EXISTS state_scans (
        run_id TEXT PRIMARY KEY REFERENCES chapter_runs(id) ON DELETE CASCADE,
        observed_delta_json TEXT NOT NULL,
        observation_json TEXT NOT NULL DEFAULT '{}',
        artifact_hash TEXT NOT NULL,
        model TEXT NOT NULL,
        reasoning_effort TEXT NOT NULL,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    );
    CREATE TABLE IF NOT EXISTS agent_tasks (
        id TEXT PRIMARY KEY,
        run_id TEXT REFERENCES chapter_runs(id) ON DELETE CASCADE,
        chapter_no INTEGER,
        stage TEXT NOT NULL,
        role TEXT NOT NULL,
        route_key TEXT NOT NULL,
        model TEXT NOT NULL,
        reasoning_effort TEXT NOT NULL,
        input_json TEXT NOT NULL,
        output_schema TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'pending'
            CHECK (status IN ('pending','completed','cancelled','failed')),
        attempt INTEGER NOT NULL DEFAULT 1,
        output_hash TEXT,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        completed_at TEXT,
        UNIQUE(run_id, stage, role, attempt)
    );
    CREATE TABLE IF NOT EXISTS character_simulations (
        id TEXT PRIMARY KEY,
        chapter_no INTEGER NOT NULL,
        character_key TEXT NOT NULL,
        perception_json TEXT NOT NULL,
        memory_json TEXT NOT NULL,
        intention_json TEXT NOT NULL,
        action_json TEXT NOT NULL,
        consequence_json TEXT NOT NULL,
        accepted INTEGER NOT NULL DEFAULT 0 CHECK (accepted IN (0,1)),
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    );
    CREATE TABLE IF NOT EXISTS replan_requests (
        id TEXT PRIMARY KEY,
        source_type TEXT NOT NULL,
        source_id TEXT NOT NULL,
        anchor_chapter INTEGER NOT NULL,
        reason TEXT NOT NULL,
        impact_json TEXT NOT NULL DEFAULT '{}',
        status TEXT NOT NULL DEFAULT 'pending'
            CHECK (status IN ('pending','applied','rejected')),
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        decided_at TEXT
    );
    CREATE TABLE IF NOT EXISTS style_profiles (
        id TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        profile_json TEXT NOT NULL,
        sample_hash TEXT NOT NULL,
        active INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0,1)),
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    );
    CREATE TABLE IF NOT EXISTS voice_bibles (
        character_key TEXT NOT NULL,
        relationship_key TEXT NOT NULL DEFAULT '*',
        profile_json TEXT NOT NULL,
        revision INTEGER NOT NULL,
        active INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0,1)),
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY(character_key, relationship_key, revision)
    );
    CREATE INDEX IF NOT EXISTS idx_agent_tasks_status ON agent_tasks(status,run_id,stage);
    CREATE INDEX IF NOT EXISTS idx_replan_status ON replan_requests(status,anchor_chapter);
    CREATE INDEX IF NOT EXISTS idx_search_docs_scope ON search_docs(audience,available_chapter,canonical_revision);
    """,
    3: r"""
    ALTER TABLE agent_tasks ADD COLUMN output_json TEXT;
    """,
    4: r"""
    -- graph_edges.active is handled by migrate_connection because legacy v1
    -- fixtures and early installs may not have graph_edges at all.
    """,
}


def current_schema_version(con: sqlite3.Connection) -> int:
    try:
        row = con.execute("SELECT value FROM schema_meta WHERE key='schema_version'").fetchone()
    except sqlite3.OperationalError as exc:
        raise SchemaError("database is not initialized") from exc
    if not row:
        raise SchemaError("database schema_version is missing")
    return int(row["value"])


def migrate_connection(con: sqlite3.Connection, target: int = SCHEMA_VERSION) -> list[int]:
    version = current_schema_version(con)
    if version > SCHEMA_VERSION:
        raise SchemaError(
            f"database schema {version} is newer than runtime schema {SCHEMA_VERSION}"
        )
    applied: list[int] = []
    while version < target:
        next_version = version + 1
        script = MIGRATIONS.get(next_version)
        if script is None:
            raise SchemaError(f"no migration path from {version} to {next_version}")
        try:
            if next_version == 4:
                graph_table = con.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name='graph_edges'"
                ).fetchone()
                if not graph_table:
                    con.execute(
                        """
                        CREATE TABLE graph_edges (
                            edge_key TEXT NOT NULL,
                            src_key TEXT NOT NULL,
                            dst_key TEXT NOT NULL,
                            edge_type TEXT NOT NULL,
                            effective_chapter INTEGER NOT NULL,
                            payload_json TEXT NOT NULL DEFAULT '{}',
                            active INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0,1)),
                            revision INTEGER NOT NULL,
                            source_memory_id TEXT NOT NULL,
                            PRIMARY KEY(edge_key, revision)
                        )
                        """
                    )
                else:
                    columns = {
                        row["name"] for row in con.execute("PRAGMA table_info(graph_edges)")
                    }
                    if "active" not in columns:
                        con.execute(
                            "ALTER TABLE graph_edges ADD COLUMN active INTEGER NOT NULL DEFAULT 1 "
                            "CHECK (active IN (0,1))"
                        )
            con.executescript(script)
            con.execute(
                "UPDATE schema_meta SET value=? WHERE key='schema_version'",
                (str(next_version),),
            )
            con.commit()
        except sqlite3.DatabaseError:
            con.rollback()
            raise
        version = next_version
        applied.append(version)
    return applied


def migrate_database(project_root: Path) -> list[int]:
    path = db_path(project_root)
    if not path.exists():
        raise SchemaError(f"novel database not initialized: {path}")
    con = connect(path)
    try:
        return migrate_connection(con)
    finally:
        con.close()


def db_path(project_root: Path) -> Path:
    return project_root / ".novel" / "state" / "story.sqlite3"


def connect(path: Path) -> sqlite3.Connection:
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    con.execute("PRAGMA journal_mode = WAL")
    con.execute("PRAGMA synchronous = NORMAL")
    return con


def initialize_database(project_root: Path, title: str, approval_mode: str = "every_chapter") -> Path:
    path = db_path(project_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    con = connect(path)
    try:
        con.executescript(BASE_SCHEMA)
        con.execute(
            "INSERT OR IGNORE INTO schema_meta(key,value) VALUES('schema_version',?)",
            (str(SCHEMA_VERSION),),
        )
        con.execute(
            "INSERT OR IGNORE INTO project(id,title,language,approval_mode) VALUES('default',?,'ko',?)",
            (title, approval_mode),
        )
        con.execute("INSERT OR IGNORE INTO story_compass(id) VALUES(1)")
        try:
            con.execute(
                "CREATE VIRTUAL TABLE IF NOT EXISTS search_fts USING fts5("
                "doc_id UNINDEXED, doc_type UNINDEXED, entity_key UNINDEXED, content)"
            )
            fts_available = "1"
        except sqlite3.OperationalError:
            fts_available = "0"
        con.execute(
            "INSERT INTO schema_meta(key,value) VALUES('fts5_available',?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (fts_available,),
        )
        con.commit()
    finally:
        con.close()
    return path


def assert_schema(con: sqlite3.Connection) -> None:
    version = current_schema_version(con)
    if version != SCHEMA_VERSION:
        raise SchemaError(
            f"unsupported database schema version {version}; expected {SCHEMA_VERSION}"
        )
    fk = con.execute("PRAGMA foreign_key_check").fetchall()
    if fk:
        raise SchemaError(f"foreign key corruption detected: {len(fk)} violation(s)")


@contextmanager
def open_database(project_root: Path) -> Iterator[sqlite3.Connection]:
    path = db_path(project_root)
    if not path.exists():
        raise SchemaError(f"novel database not initialized: {path}")
    con = connect(path)
    try:
        assert_schema(con)
        yield con
    finally:
        con.close()


def json_dumps(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
