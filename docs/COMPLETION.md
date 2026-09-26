# Completion audit

This document maps the locked product design to executable implementation and verification.

| Requirement | Implementation | Verification |
|---|---|---|
| Install into another Git repo | scripts/install_project.py snapshots .novel runtime, roles, skills, configs | test_installer + runtime_smoke |
| Korean-only fiction | Korean craft skill, style/voice logic, Korean register continuity checks | test_style_voice_and_emergent_replan |
| Chapter-first canonical unit | ChapterPlan, chapter_runs, chapter_revisions/publications | runtime and long-horizon smoke |
| Exactly A/B/C candidates | runtime cardinality + immutable lens rows | test_full_runtime_authority_and_pending_memory |
| Runtime-driven Codex orchestration | agent_tasks, workflow-next, orchestrate-next | planning/runtime tests |
| Explicit model/effort, fail closed | model-routing.json + issued task route validation | semantic-write and routing tests |
| Candidate hard gate | state-scanner preflight for every candidate | preflight tests |
| Anonymous tournament | A/B/C -> P/Q/R, both-order pairwise aggregation | hard tournament/runtime tests |
| Ambiguous tournament escalation | Sol High hard-adjudication task | test_hard_tournament_is_runtime_issued |
| Independent two-stage review | narrative wave then craft/reader wave | test_two_stage_review_gate_and_reader_attestation |
| Bounded conflict cross-exam | one hard Sol path, conflict participants only | test_review_conflict_escalates_to_sol_hard_task |
| Surgical revision | none/sentence/paragraph/chapter durable final draft | repair tests |
| Regression after nontrivial repair | regression task and REGRESSION_PASSED stage | nontrivial repair test |
| Independent ObservedDelta | isolated state-scanner final-prose task | full runtime + repair tests |
| Expected/Declared/Observed equality | state_tx compare + publication gate | state_tx/runtime tests |
| Publish != Canon | publish stages PendingMemoryProposal only | full runtime test + smoke |
| Human Canon approval in auto mode | memory-approve/reject authority boundary | full runtime test + smoke |
| SQLite sole Canon | versioned schema, migrations, FK/WAL checks | migration/schema/runtime tests |
| Temporal narrative graph | revisioned graph nodes/edges, as-of traversal | retrieval tests |
| Edge removal history | graph edge active tombstones | temporal graph tombstone test |
| FTS/BM25 | FTS5 projection, LIKE fallback | retrieval/runtime tests |
| Optional vector retrieval | provider abstraction, embedding persistence, RRF fusion | test_vector_path_is_actually_used |
| Canon/AuthorIntent/AuthorPreference separation | separate tables/namespaces | runtime/retrieval tests |
| Human idea critical review | runtime-issued Story Director IdeaCritique task | idea authority test + installed smoke |
| Accepted idea not Canon | AuthorIntent projection, canon_mutated false | idea authority test |
| Epistemic state | knows/suspects/falsely_believes/unknown | continuity/graph/retrieval |
| Event order != reveal order | separate events/reveals tables and queries | continuity/retrieval |
| Foreshadow lifecycle | typed state storage and due queries | eval/retrieval |
| Future state non-leakage | effective-chapter projections, as-of retrieval | promise/foreshadow/relationship tests |
| Blind Reader | separate reader projection/context + forbidden-type guard | reader-context/review tests |
| Stale downstream detection | exact read_points + stale propagation | 30/100 chapter smoke |
| Character sandbox | runtime-issued character simulation task | sandbox authority/style-emergence tests |
| Emergent rolling replan | explicit replan request/apply/reject | style-emergence test |
| Author style fingerprint | deterministic sample metrics + active profile | style test |
| Korean relationship voice bible | register/address forms by character/relation | style/continuity test |
| Narrative texture audit | advisory quality flags | quality benchmark test |
| Writer none-vs-low benchmark | benchmark surface | quality benchmark test |
| Checkpoint/resume/idempotency | transitions, checkpoints, immutable artifacts | runtime + long-horizon smoke |
| Bounded retry/fail closed | run abort/retry budget, schema and route guards | preflight/schema/runtime tests |
| Backup/export | SQLite backup + JSON state export | backup/export test |
| Schema upgrades | v1 -> v4 and v3 -> v4 preservation | migration tests |
| 30+ chapter horizon | full deterministic chapter workflow repeated | check.py --smoke |
| 100 chapter horizon | extended deterministic workflow | long_horizon_smoke.py --chapters 100 |

## External runtime boundary

The codebase and deterministic integration are complete without network/model authentication.

A true Codex model call requires the local Codex installation to be authenticated. The local environment currently reports Not logged in; therefore authenticated live-model output is not represented as passing evidence. This is an environment credential state, not a missing framework path. doctor exposes this separately as auth-pending.
