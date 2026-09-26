from __future__ import annotations

import re
from collections import Counter
from typing import Any

from .style import extract_style_profile, compare_to_profile

_SENTENCE_RE = re.compile(r"[^.!?。！？]+[.!?。！？]*", re.MULTILINE)
_WORD_RE = re.compile(r"[가-힣A-Za-z0-9']+")

_GENERIC_PATTERNS = [
    "알 수 없는",
    "묘한 감정",
    "설명할 수 없는",
    "왠지 모르게",
    "숨을 삼켰",
    "심장이 내려앉",
    "시간이 멈춘 듯",
]


def _sentence_opening(sentence: str) -> str:
    tokens = _WORD_RE.findall(sentence)
    return tokens[0] if tokens else ""


def audit_prose(
    text: str,
    *,
    style_profile: dict[str, Any] | None = None,
    narrative_observation: dict[str, Any] | None = None,
) -> dict[str, Any]:
    sentences = [m.group(0).strip() for m in _SENTENCE_RE.finditer(text) if m.group(0).strip()]
    openings = Counter(_sentence_opening(s) for s in sentences if _sentence_opening(s))
    repeated_openings = [
        {"opening": key, "count": count}
        for key, count in openings.most_common()
        if count >= 3
    ]

    words = _WORD_RE.findall(text)
    trigrams = Counter(tuple(words[i : i + 3]) for i in range(max(0, len(words) - 2)))
    repeated_trigrams = [
        {"phrase": " ".join(key), "count": count}
        for key, count in trigrams.most_common(20)
        if count >= 3
    ]

    generic_hits = [
        {"pattern": pattern, "count": text.count(pattern)}
        for pattern in _GENERIC_PATTERNS
        if text.count(pattern)
    ]

    sentence_lengths = [len(s) for s in sentences]
    short_run = 0
    max_short_run = 0
    long_run = 0
    max_long_run = 0
    for length in sentence_lengths:
        if length <= 18:
            short_run += 1
            long_run = 0
        elif length >= 80:
            long_run += 1
            short_run = 0
        else:
            short_run = long_run = 0
        max_short_run = max(max_short_run, short_run)
        max_long_run = max(max_long_run, long_run)

    warnings: list[dict[str, Any]] = []
    if repeated_openings:
        warnings.append(
            {
                "code": "REPEATED_SENTENCE_OPENINGS",
                "severity": "advisory",
                "evidence": repeated_openings[:8],
            }
        )
    if repeated_trigrams:
        warnings.append(
            {
                "code": "REPEATED_PHRASES",
                "severity": "advisory",
                "evidence": repeated_trigrams[:8],
            }
        )
    if generic_hits:
        warnings.append(
            {
                "code": "GENERIC_PHRASE_DENSITY",
                "severity": "advisory",
                "evidence": generic_hits,
                "note": "Do not mechanically delete phrases when they are intentional voice.",
            }
        )
    if max_short_run >= 6 or max_long_run >= 4:
        warnings.append(
            {
                "code": "RHYTHM_MONOTONY",
                "severity": "advisory",
                "max_short_sentence_run": max_short_run,
                "max_long_sentence_run": max_long_run,
            }
        )

    narrative_observation = narrative_observation or {}
    for key, code in (
        ("over_explained_theme", "OVER_EXPLAINED_THEME"),
        ("moral_neatness", "MORAL_NEATNESS"),
        ("single_track_resolution", "SINGLE_TRACK_RESOLUTION"),
        ("formulaic_resolution", "FORMULAIC_RESOLUTION"),
    ):
        if narrative_observation.get(key) is True:
            warnings.append(
                {
                    "code": code,
                    "severity": "advisory",
                    "note": "Flag only; never inject complexity mechanically.",
                }
            )

    style_comparison = (
        compare_to_profile(text, style_profile) if style_profile is not None else None
    )
    if style_comparison and style_comparison["drift_score"] > 0.55:
        warnings.append(
            {
                "code": "AUTHOR_STYLE_DRIFT",
                "severity": "advisory",
                "drift_score": style_comparison["drift_score"],
            }
        )

    return {
        "schema": "novel-prose-quality/v1",
        "profile": extract_style_profile(text),
        "style_comparison": style_comparison,
        "warnings": warnings,
        "warning_count": len(warnings),
    }
