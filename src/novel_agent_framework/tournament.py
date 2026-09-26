from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any

from .errors import ValidationBlocked


@dataclass(frozen=True)
class PairDecision:
    first: str
    second: str
    preferred: str
    confidence: float


def alias_mapping(run_id: str) -> dict[str, str]:
    """Return alias -> lens mapping with deterministic but run-specific permutation."""
    lenses = ["A", "B", "C"]
    ranked = sorted(
        lenses,
        key=lambda lens: hashlib.sha256(f"{run_id}:{lens}".encode()).hexdigest(),
    )
    return dict(zip(["P", "Q", "R"], ranked, strict=True))


def prepare_packets(run_id: str, candidates: dict[str, str]) -> dict[str, Any]:
    mapping = alias_mapping(run_id)
    aliases = {lens: alias for alias, lens in mapping.items()}
    pairs: list[dict[str, Any]] = []
    for first, second in (("P", "Q"), ("Q", "P"), ("P", "R"), ("R", "P"), ("Q", "R"), ("R", "Q")):
        pairs.append(
            {
                "comparison_id": f"{first}-{second}",
                "left": {"alias": first, "prose": candidates[mapping[first]]},
                "right": {"alias": second, "prose": candidates[mapping[second]]},
                "instruction": (
                    "Judge narrative function first, then craft. Do not infer candidate lens. "
                    "Return preferred alias and confidence in [0,1]."
                ),
            }
        )
    return {"schema": "novel-tournament-packets/v1", "pairs": pairs, "aliases": sorted(mapping)}


def _parse_decisions(pairwise: dict[str, Any]) -> list[PairDecision]:
    required = {"P-Q", "Q-P", "P-R", "R-P", "Q-R", "R-Q"}
    if set(pairwise) != required:
        raise ValidationBlocked(
            "pairwise tournament requires both orders for P/Q, P/R, and Q/R"
        )
    decisions: list[PairDecision] = []
    for key in sorted(required):
        first, second = key.split("-")
        raw = pairwise[key]
        if not isinstance(raw, dict):
            raise ValidationBlocked(f"pairwise result {key} must be an object")
        preferred = str(raw.get("preferred", ""))
        if preferred not in {first, second}:
            raise ValidationBlocked(
                f"pairwise result {key} preferred must be {first} or {second}"
            )
        confidence = float(raw.get("confidence", 0.0))
        if not 0.0 <= confidence <= 1.0:
            raise ValidationBlocked(f"pairwise result {key} confidence must be [0,1]")
        decisions.append(PairDecision(first, second, preferred, confidence))
    return decisions


def aggregate(run_id: str, pairwise: dict[str, Any]) -> dict[str, Any]:
    mapping = alias_mapping(run_id)
    decisions = _parse_decisions(pairwise)
    unordered = [("P", "Q"), ("P", "R"), ("Q", "R")]
    pair_winners: dict[str, str | None] = {}
    pair_confidences: dict[str, float] = {}
    wins = {alias: 0 for alias in mapping}

    by_key = {f"{d.first}-{d.second}": d for d in decisions}
    for a, b in unordered:
        forward = by_key[f"{a}-{b}"]
        reverse = by_key[f"{b}-{a}"]
        score = {a: 0.0, b: 0.0}
        score[forward.preferred] += max(forward.confidence, 0.01)
        score[reverse.preferred] += max(reverse.confidence, 0.01)
        margin = abs(score[a] - score[b]) / max(score[a] + score[b], 1e-9)
        key = f"{a}/{b}"
        pair_confidences[key] = margin
        if score[a] == score[b]:
            pair_winners[key] = None
        else:
            winner = a if score[a] > score[b] else b
            pair_winners[key] = winner
            wins[winner] += 1

    condorcet = next((alias for alias, count in wins.items() if count == 2), None)
    mean_input_conf = sum(d.confidence for d in decisions) / len(decisions)
    low_confidence = mean_input_conf < 0.55 or any(v < 0.10 for v in pair_confidences.values())
    cycle_or_tie = condorcet is None
    hard_required = cycle_or_tie or low_confidence

    return {
        "schema": "novel-tournament-aggregation/v1",
        "alias_to_lens": mapping,
        "pair_winners": pair_winners,
        "pair_margins": pair_confidences,
        "wins": wins,
        "condorcet_alias": condorcet,
        "condorcet_lens": mapping[condorcet] if condorcet else None,
        "mean_judge_confidence": mean_input_conf,
        "hard_adjudication_required": hard_required,
        "reason": (
            "cycle-or-tie" if cycle_or_tie else "low-confidence" if low_confidence else "condorcet"
        ),
    }
