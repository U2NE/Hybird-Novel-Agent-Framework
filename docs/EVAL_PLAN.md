# Evaluation and verification

Automated regression covers installer/config merge, schema migration, runtime-issued task authority, explicit model routing, planning specialist selection, exact A/B/C cardinality, candidate preflight, abort/retry budget, blinded two-order tournament, hard escalation, two-stage review, blind-reader isolation, reviewer immutability, review conflict escalation, repair/regression/state-scan order, three-way delta disagreement, pending Canon authority, temporal author/reader retrieval, vector retrieval, historical relationship reconstruction, future promise/foreshadow non-leakage, graph edge tombstones, style/voice, character emergence/replan, quality audit, writer none-vs-low benchmark, backup/export, and fail-closed schema handling.

scripts/runtime_smoke.py validates an installed target.

scripts/long_horizon_smoke.py executes complete deterministic chapter workflows repeatedly, validating canonical accumulation, historical retrieval, resume/idempotency, and stale propagation.

Default smoke uses 30 chapters. Extended:

    python3 scripts/long_horizon_smoke.py --chapters 100

Model-quality surfaces:
- quality-eval
- benchmark-writer
- blind Reader review
- style drift against author sample

These produce evidence and never replace author judgment.
