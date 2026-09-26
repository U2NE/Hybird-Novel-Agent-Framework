# Architecture

## Authority model

The deterministic runtime owns schema/migrations, run transitions, task issuance, explicit model/reasoning routing, candidate cardinality, candidate preflight, tournament aggregation/escalation, reviewer phase isolation, review adjudication, repair/regression ordering, state-scan provenance, three-way delta validation, publication, pending-memory authority, Canon approval, projections, read points, stale propagation, checkpoints, and retry budgets.

Agents own semantic work only. Agent text is never authoritative by itself.

## Runtime-issued tasks

workflow-next controls planning-level semantic tasks. orchestrate-next controls a chapter run.

Each task stores role, route key, model, reasoning effort, input JSON, output contract, attempt, output hash, and status. Mutation methods require the matching task, so a lead cannot skip semantic gates merely because an instruction was missed.

This applies beyond chapter generation: human-idea criticism and Character Sandbox simulations are also runtime-issued project tasks before semantic results can be persisted.

## Chapter state machine

    CREATED
      -> CANDIDATES_READY
      -> PREFLIGHT_PASSED | PREFLIGHT_FAILED
      -> FROZEN
      -> NARRATIVE_REVIEWED
      -> REVIEWED
      -> ADJUDICATED
      -> REPAIRED
      -> REGRESSION_PASSED (when needed)
      -> DELTA_VALIDATED
      -> PUBLISHED

Preflight failure is terminal for that run after explicit abort; retries are bounded.

## Tournament

A/B/C identities are mapped to run-specific P/Q/R aliases. P/Q, P/R, Q/R are each evaluated in both orders. The runtime aggregates results and uses a Condorcet-style winner when confidence is adequate. Cycles, ties, or low confidence issue a hard Sol adjudication task.

## Review

Phase 1: continuity, character consistency, foreshadowing.

Phase 2: dialogue, style, blind reader.

Story Director receives the independent bundle only after all six. Genuine conflict may use at most one hard cross-examination/adjudication path.

## Revision and state transaction

The frozen draft is immutable. Revision creates a new durable final draft. Non-trivial repair requires regression success before state scan.

The final scanner does not receive the writer's DeclaredDelta. Publication is allowed only after ExpectedDelta, repaired DeclaredDelta, and independent ObservedDelta agree.

## Storage

SQLite is the only source of truth. Current schema version is 4.

FTS, vectors, and graph/search documents are rebuildable projections. Graph edges are revisioned and may be tombstoned, enabling historical as-of-chapter traversal after ownership, location, or causal links change.

## Retrieval scopes

Author context may include Canon, accepted AuthorIntent, high-confidence AuthorPreference, style profile, voice bibles, and graph evidence.

Reader context is built separately from published prose, reader reveals, and reader questions through the cutoff chapter. Author-only/future document classes fail closed if they leak.

## Deployment

The installer snapshots runtime code/config into .novel, merges collision-safe Codex roles, installs skills, and adds bounded AGENTS instructions. The target is self-contained.
