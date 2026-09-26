from __future__ import annotations

from typing import Any

from .models import StoryDelta


def compare_deltas(
    expected: StoryDelta,
    declared: StoryDelta,
    observed: StoryDelta,
) -> dict[str, Any]:
    exp = expected.as_map()
    dec = declared.as_map()
    obs = observed.as_map()
    identities = sorted(set(exp) | set(dec) | set(obs))
    mismatches: list[dict[str, Any]] = []
    for identity in identities:
        e, d, o = exp.get(identity), dec.get(identity), obs.get(identity)
        if e != d or d != o:
            mismatches.append(
                {
                    "identity": list(identity),
                    "expected": e,
                    "declared": d,
                    "observed": o,
                }
            )
    return {
        "passed": not mismatches,
        "expected_count": len(exp),
        "declared_count": len(dec),
        "observed_count": len(obs),
        "mismatches": mismatches,
    }
