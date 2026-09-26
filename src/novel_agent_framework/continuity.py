from __future__ import annotations

import json
import sqlite3
from typing import Any

from .contracts import ChapterContract
from .graph import NarrativeGraph


def _latest_character_state(
    con: sqlite3.Connection, character: str, field: str, chapter_no: int
) -> Any | None:
    row = con.execute(
        """
        SELECT cs.value_json
        FROM character_states cs
        JOIN characters c ON c.id=cs.character_id
        WHERE c.name=? AND cs.field=? AND cs.effective_chapter <= ?
        ORDER BY cs.revision DESC LIMIT 1
        """,
        (character, field, chapter_no),
    ).fetchone()
    return json.loads(row["value_json"]) if row else None


def _latest_object_state(
    con: sqlite3.Connection, object_key: str, chapter_no: int
) -> dict[str, Any] | None:
    row = con.execute(
        """
        SELECT * FROM object_states
        WHERE object_key=? AND effective_chapter <= ?
        ORDER BY revision DESC LIMIT 1
        """,
        (object_key, chapter_no),
    ).fetchone()
    if not row:
        return None
    return {
        "owner": row["owner_key"],
        "location": row["location_key"],
        "state": json.loads(row["state_json"]),
    }


def validate_structured_continuity(
    con: sqlite3.Connection,
    *,
    chapter_no: int,
    observation: dict[str, Any],
    contract: ChapterContract,
) -> dict[str, Any]:
    """Fail-closed deterministic checks over a scanner-produced structured observation."""
    issues: list[dict[str, Any]] = []
    graph = NarrativeGraph(con)

    for actor in observation.get("actors", []):
        name = str(actor["character"])
        alive = _latest_character_state(con, name, "alive", chapter_no)
        if alive is False:
            issues.append(
                {
                    "code": "DEAD_CHARACTER_ACTS",
                    "severity": "critical",
                    "entity": name,
                    "evidence": actor.get("evidence", ""),
                }
            )

    for travel in observation.get("travel", []):
        elapsed = float(travel.get("elapsed_minutes", 0))
        minimum = float(travel.get("minimum_minutes", 0))
        if elapsed < minimum:
            issues.append(
                {
                    "code": "IMPOSSIBLE_TRAVEL",
                    "severity": "critical",
                    "entity": travel.get("character"),
                    "evidence": travel,
                }
            )

    knowledge_by_character: dict[str, dict[str, str]] = {}
    for use in observation.get("knowledge_uses", []):
        character = str(use["character"])
        fact_key = str(use["fact_key"])
        if character not in knowledge_by_character:
            knowledge_by_character[character] = {
                item["fact_key"]: item["state"]
                for item in graph.character_knowledge(character, chapter_no)
            }
        state = knowledge_by_character[character].get(fact_key, "unknown")
        allowed = set(map(str, use.get("allowed_states", ["knows", "suspects"])))
        if state not in allowed:
            issues.append(
                {
                    "code": "KNOWLEDGE_LEAK",
                    "severity": "critical",
                    "entity": f"{character}:{fact_key}",
                    "canonical_state": state,
                    "evidence": use.get("evidence", ""),
                }
            )

    for obj in observation.get("object_uses", []):
        key = str(obj["object_key"])
        state = _latest_object_state(con, key, chapter_no)
        if state and state["state"].get("destroyed") is True:
            issues.append(
                {
                    "code": "DESTROYED_OBJECT_REAPPEARS",
                    "severity": "critical",
                    "entity": key,
                    "evidence": obj.get("evidence", ""),
                }
            )
        expected_location = obj.get("expected_location")
        if state and expected_location and state["location"] != expected_location:
            issues.append(
                {
                    "code": "OBJECT_LOCATION_CONFLICT",
                    "severity": "high",
                    "entity": key,
                    "canonical_location": state["location"],
                    "observed_location": expected_location,
                }
            )

    for line in observation.get("dialogue_registers", []):
        character = str(line["character"])
        relationship = str(line.get("relationship_key", "*"))
        row = con.execute(
            """
            SELECT profile_json
            FROM voice_bibles
            WHERE character_key=? AND relationship_key IN (?, '*') AND active=1
            ORDER BY CASE WHEN relationship_key=? THEN 0 ELSE 1 END, revision DESC
            LIMIT 1
            """,
            (character, relationship, relationship),
        ).fetchone()
        if not row:
            continue
        profile = json.loads(row["profile_json"])
        observed_register = str(line.get("register", ""))
        expected_register = str(profile.get("baseline_register", "contextual"))
        pressure = bool(line.get("under_pressure", False))
        if pressure and profile.get("pressure_register"):
            expected_register = str(profile["pressure_register"])
        if (
            expected_register not in {"contextual", "mixed"}
            and observed_register
            and observed_register != expected_register
        ):
            issues.append(
                {
                    "code": "KOREAN_REGISTER_DRIFT",
                    "severity": "high",
                    "entity": f"{character}:{relationship}",
                    "expected": expected_register,
                    "observed": observed_register,
                    "evidence": line.get("evidence", ""),
                }
            )
        allowed_addresses = set(map(str, profile.get("address_forms", [])))
        address = line.get("address_form")
        if allowed_addresses and address and str(address) not in allowed_addresses:
            issues.append(
                {
                    "code": "KOREAN_ADDRESS_FORM_DRIFT",
                    "severity": "high",
                    "entity": f"{character}:{relationship}",
                    "allowed": sorted(allowed_addresses),
                    "observed": str(address),
                    "evidence": line.get("evidence", ""),
                }
            )

    for rel in observation.get("relationship_claims", []):
        subject = str(rel["subject"])
        obj = str(rel["object"])
        relation = str(rel["relation"])
        row = con.execute(
            """
            SELECT value_json
            FROM relationship_states
            WHERE subject_key=? AND object_key=? AND relation=? AND effective_chapter <= ?
            ORDER BY revision DESC LIMIT 1
            """,
            (subject, obj, relation, chapter_no),
        ).fetchone()
        if row is not None:
            canonical = json.loads(row["value_json"])
            observed_value = rel.get("value")
            if observed_value != canonical:
                issues.append(
                    {
                        "code": "RELATIONSHIP_STATE_RESET",
                        "severity": "high",
                        "entity": f"{subject}:{relation}:{obj}",
                        "canonical": canonical,
                        "observed": observed_value,
                    }
                )

    for check in observation.get("world_rule_checks", []):
        if check.get("compliant") is False:
            issues.append(
                {
                    "code": "WORLD_RULE_VIOLATION",
                    "severity": "critical",
                    "entity": check.get("rule_key"),
                    "evidence": check.get("evidence", ""),
                }
            )

    latest_foreshadow = {
        row["foreshadow_key"]: dict(row)
        for row in con.execute(
            """
            SELECT f.*
            FROM foreshadows f
            JOIN (
              SELECT foreshadow_key,MAX(revision) revision
              FROM foreshadows GROUP BY foreshadow_key
            ) l ON l.foreshadow_key=f.foreshadow_key AND l.revision=f.revision
            """
        )
    }
    for payoff in observation.get("foreshadow_payoffs", []):
        key = str(payoff["foreshadow_key"])
        state = latest_foreshadow.get(key)
        if not state or state["state"] in {"PLANNED"}:
            issues.append(
                {
                    "code": "PAYOFF_WITHOUT_PLANT",
                    "severity": "high",
                    "entity": key,
                    "evidence": payoff.get("evidence", ""),
                }
            )

    for key, row in latest_foreshadow.items():
        due = row.get("due_chapter")
        if (
            due is not None
            and int(due) <= chapter_no
            and row["state"] not in {"PAID_OFF", "DELAYED", "ABANDONED"}
        ):
            issues.append(
                {
                    "code": "FORESHADOW_DUE_UNRESOLVED",
                    "severity": "medium",
                    "entity": key,
                    "due_chapter": int(due),
                }
            )

    revealed = set(map(str, observation.get("revealed_facts", [])))
    forbidden_hit = sorted(revealed.intersection(contract.forbidden_reveals))
    for fact in forbidden_hit:
        issues.append(
            {
                "code": "FORBIDDEN_REVEAL",
                "severity": "critical",
                "entity": fact,
            }
        )

    occurrence = {
        row["id"]: int(row["occurrence_index"])
        for row in con.execute(
            """
            SELECT e.id,e.occurrence_index
            FROM events e
            JOIN (SELECT id,MAX(revision) revision FROM events GROUP BY id) l
              ON l.id=e.id AND l.revision=e.revision
            WHERE e.chapter_no <= ?
            """,
            (chapter_no,),
        )
    }
    for reveal in observation.get("reader_reveals", []):
        source_event = reveal.get("source_event")
        if source_event and source_event not in occurrence:
            issues.append(
                {
                    "code": "REVEAL_BEFORE_EVENT_OCCURRENCE",
                    "severity": "critical",
                    "entity": reveal.get("fact_key"),
                    "source_event": source_event,
                }
            )

    observed_exit = observation.get("exit_state", {})
    for key, expected in contract.exit_conditions.items():
        actual = observed_exit.get(key)
        if actual != expected:
            issues.append(
                {
                    "code": "EXIT_STATE_MISMATCH",
                    "severity": "high",
                    "entity": key,
                    "expected": expected,
                    "observed": actual,
                }
            )

    required_realized = set(map(str, observation.get("realized_beats", [])))
    for beat in contract.required_beats:
        if beat not in required_realized:
            issues.append(
                {
                    "code": "REQUIRED_BEAT_MISSING",
                    "severity": "high",
                    "entity": beat,
                }
            )

    return {
        "passed": not any(item["severity"] in {"critical", "high"} for item in issues),
        "issues": issues,
        "issue_count": len(issues),
        "chapter_no": chapter_no,
    }
