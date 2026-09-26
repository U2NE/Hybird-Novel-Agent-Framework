from __future__ import annotations

import hashlib
import math
import re
import statistics
from collections import Counter
from typing import Any


_SENTENCE_RE = re.compile(r"[^.!?。！？]+[.!?。！？]*", re.MULTILINE)
_TOKEN_RE = re.compile(r"[가-힣A-Za-z0-9']+")
_DIALOGUE_MARKS = ('"', "'", "“", "”", "‘", "’")


def _safe_mean(values: list[float]) -> float:
    return statistics.fmean(values) if values else 0.0


def _safe_stdev(values: list[float]) -> float:
    return statistics.pstdev(values) if len(values) > 1 else 0.0


def extract_style_profile(text: str) -> dict[str, Any]:
    normalized = text.replace("\r\n", "\n").strip()
    sentences = [m.group(0).strip() for m in _SENTENCE_RE.finditer(normalized) if m.group(0).strip()]
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", normalized) if p.strip()]
    tokens = _TOKEN_RE.findall(normalized)
    sentence_chars = [float(len(s)) for s in sentences]
    sentence_tokens = [float(len(_TOKEN_RE.findall(s))) for s in sentences]
    paragraph_sentences = [
        float(len([m.group(0) for m in _SENTENCE_RE.finditer(p) if m.group(0).strip()]))
        for p in paragraphs
    ]
    dialogue_lines = [
        line for line in normalized.splitlines() if any(mark in line for mark in _DIALOGUE_MARKS)
    ]
    endings = Counter()
    for sentence in sentences:
        stripped = sentence.rstrip(".!?。！？ ").strip()
        if stripped:
            last = stripped.split()[-1]
            endings[last[-6:]] += 1
    punct = Counter(ch for ch in normalized if ch in ",;:!?…—-()[]{}·")
    chars = max(len(normalized), 1)
    lexical_diversity = len(set(tokens)) / max(len(tokens), 1)

    formal = len(re.findall(r"(습니다|습니까|십시오|합니다|하세요|입니다)", normalized))
    polite = len(re.findall(r"[가-힣]+요(?:[.!?]|$)", normalized))
    banmal = len(re.findall(r"(한다|했다|해|야|지|냐|니|라)(?:[.!?]|$)", normalized))

    openings = Counter()
    for sentence in sentences:
        first = _TOKEN_RE.findall(sentence)
        if first:
            openings[first[0]] += 1

    return {
        "schema": "novel-author-style/v1",
        "sample_hash": hashlib.sha256(normalized.encode("utf-8")).hexdigest(),
        "characters": len(normalized),
        "tokens": len(tokens),
        "sentences": len(sentences),
        "paragraphs": len(paragraphs),
        "sentence_length_chars": {
            "mean": _safe_mean(sentence_chars),
            "stdev": _safe_stdev(sentence_chars),
            "median": statistics.median(sentence_chars) if sentence_chars else 0.0,
        },
        "sentence_length_tokens": {
            "mean": _safe_mean(sentence_tokens),
            "stdev": _safe_stdev(sentence_tokens),
        },
        "paragraph_sentence_count": {
            "mean": _safe_mean(paragraph_sentences),
            "stdev": _safe_stdev(paragraph_sentences),
        },
        "dialogue_line_ratio": len(dialogue_lines) / max(len(normalized.splitlines()), 1),
        "lexical_diversity": lexical_diversity,
        "punctuation_per_1000_chars": {
            key: count * 1000.0 / chars for key, count in sorted(punct.items())
        },
        "speech_register_markers": {
            "formal_polite": formal,
            "polite_yo": polite,
            "banmal_like": banmal,
        },
        "top_sentence_openings": openings.most_common(12),
        "top_sentence_endings": endings.most_common(12),
        "first_person_count": sum(normalized.count(x) for x in ("나는", "내가", "저는", "제가")),
        "second_person_count": sum(normalized.count(x) for x in ("너는", "네가", "당신", "자네")),
    }


def compare_to_profile(text: str, profile: dict[str, Any]) -> dict[str, Any]:
    current = extract_style_profile(text)
    keys = [
        ("sentence_length_chars", "mean"),
        ("sentence_length_tokens", "mean"),
        ("paragraph_sentence_count", "mean"),
    ]
    deviations: list[dict[str, Any]] = []
    for group, metric in keys:
        expected = float(profile.get(group, {}).get(metric, 0.0))
        actual = float(current.get(group, {}).get(metric, 0.0))
        denom = max(abs(expected), 1.0)
        deviations.append(
            {
                "metric": f"{group}.{metric}",
                "expected": expected,
                "actual": actual,
                "relative_delta": (actual - expected) / denom,
            }
        )
    for metric in ("dialogue_line_ratio", "lexical_diversity"):
        expected = float(profile.get(metric, 0.0))
        actual = float(current.get(metric, 0.0))
        deviations.append(
            {
                "metric": metric,
                "expected": expected,
                "actual": actual,
                "relative_delta": actual - expected,
            }
        )
    drift = math.sqrt(
        sum(float(item["relative_delta"]) ** 2 for item in deviations) / max(len(deviations), 1)
    )
    return {
        "schema": "novel-style-comparison/v1",
        "drift_score": drift,
        "deviations": deviations,
        "current_profile": current,
    }
