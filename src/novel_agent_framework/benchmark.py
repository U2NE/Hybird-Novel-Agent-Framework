from __future__ import annotations

import statistics
from typing import Any


def summarize_writer_variant(rows: list[dict[str, Any]]) -> dict[str, Any]:
    if not rows:
        raise ValueError("benchmark variant requires at least one sample")
    continuity = [1.0 if row.get("continuity_passed") else 0.0 for row in rows]
    severe = [float(row.get("severe_findings", 0)) for row in rows]
    style = [float(row.get("style_drift", 0.0)) for row in rows]
    latency = [float(row.get("latency_seconds", 0.0)) for row in rows]
    tokens = [float(row.get("output_tokens", 0)) for row in rows]
    preference = [float(row.get("human_preference", 0.0)) for row in rows]
    return {
        "samples": len(rows),
        "continuity_pass_rate": statistics.fmean(continuity),
        "mean_severe_findings": statistics.fmean(severe),
        "mean_style_drift": statistics.fmean(style),
        "mean_latency_seconds": statistics.fmean(latency),
        "mean_output_tokens": statistics.fmean(tokens),
        "mean_human_preference": statistics.fmean(preference),
    }


def compare_writer_efforts(
    none_rows: list[dict[str, Any]],
    low_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    none = summarize_writer_variant(none_rows)
    low = summarize_writer_variant(low_rows)
    return {
        "schema": "novel-writer-effort-benchmark/v1",
        "none": none,
        "low": low,
        "delta_low_minus_none": {
            key: low[key] - none[key]
            for key in (
                "continuity_pass_rate",
                "mean_severe_findings",
                "mean_style_drift",
                "mean_latency_seconds",
                "mean_output_tokens",
                "mean_human_preference",
            )
        },
        "note": (
            "No winner is chosen automatically. Calibrate the production writer route "
            "from quality/cost evidence and author preference."
        ),
    }
