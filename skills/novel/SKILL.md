---
name: novel
description: Operate the installed deterministic Korean long-form fiction framework.
---

# Novel Runtime Lead

The deterministic runtime is the workflow authority. Do not invent stages, skip gates, or mutate the SQLite database directly.

## Bootstrap

Run doctor and status first. If the DB is older than the installed runtime, run migrate. Fail closed on doctor errors. Codex authentication readiness is separate from deterministic runtime validity.

## Golden rule

For chapter work, always call workflow-next --chapter N. Before and after every run-stage task batch, call orchestrate-next --run RUN_ID.

Spawn only tasks returned by those commands. Use exactly the returned role, model, reasoning_effort, input, and output_schema. Do not substitute a role/model/effort and do not inherit the current session model. Narrative workers never spawn other workers.

## Planning task commits

planning:director tasks return JSON with roles chosen only from plot-architect, world-builder, character-agent, plus reason. Commit with planning-complete.

planning specialist tasks return a focused JSON proposal. Commit with planning-complete.

planning:chapter tasks return title, plan, contract, expected_delta. Contract must include required_beats, forbidden_reveals, exit_conditions, read_points and relevant POV/location/time. Commit with planning-complete.

If runtime returns human_gate chapter-plan-approval, show the plan and require explicit author approval before plan-approve --kind human. In autonomous mode use the runtime start-run-auto action.

## Run task commits

write:A/B/C uses novel-writer. Return JSON with prose and declared_delta. A is external action/conflict, B is interiority/subtext/relationship pressure, C is unusual but contract-compliant atmosphere/execution. Commit with candidate-add. Exactly three candidates.

preflight:A/B/C uses novel-state-scanner. Independently inspect prose without Writer DeclaredDelta. Return StructuredContinuityObservation covering applicable actors, travel, knowledge uses, object uses, relationship claims, dialogue registers, world-rule checks, foreshadow payoffs, reveals, realized beats and exit state. Commit with candidate-preflight. If runtime returns abort-run, abort and begin a bounded new run.

tournament P/Q/R tasks contain anonymous prose. Return preferred alias, confidence 0..1 and reason. Commit with pairwise-complete. Execute runtime tournament-select action. If tournament-hard is issued, spawn its Sol task, return winner_alias P/Q/R plus reason, commit with tournament-hard-complete, call orchestrate-next again, then execute tournament-select-hard.

## Review gates

Narrative wave: continuity, character-consistency, foreshadowing.
Craft/reader wave only after narrative gate: dialogue, style, reader.

Every ReviewArtifact contains exact draft_hash and findings. Each finding contains claim, evidence, severity, affected_state, suggested_repair and confidence. Reader also returns context_policy reader-visible-only. Never give Reader future plot, hidden Canon, AuthorIntent, AuthorPreference or future foreshadow plans.

Commit reviews with review-add.

review-adjudication synthesizes the independent bundle. If no genuine conflict, commit with review-adjudicate and empty conflicts. If genuine conflicts exist, identify conflict reviewer names. The runtime stages review-adjudication-hard with Sol High. Spawn that task. It may use one cross-examination round only and only conflict reviewers. Commit with review-hard-complete.

## Repair and independent scanner

repair follows sentence, paragraph, chapter patch, then whole-chapter regeneration only as last resort. Return RevisionManifest, repair_level, final prose when nontrivial, and revised DeclaredDelta. Commit with revision-manifest.

After nontrivial repair, runtime issues regression. Commit pass/fail with regression-add. Maximum three attempts.

state-scan receives final prose but not Planner/Writer delta sidecars. Return independent observed_delta and observation. Commit with state-scan.

Then call delta-validate. ExpectedDelta, repaired DeclaredDelta and immutable StateScan ObservedDelta must agree or publication is blocked.

## Publication and Canon

publish commits exact prose and stages PendingMemoryProposal. Publication never automatically mutates Canon.

Even in autonomous mode, memory-list proposals require explicit human memory-approve or memory-reject. After Canon approval inspect stale-list before continuing dependent chapters.

## Human ideas/preferences

idea-propose persists the raw human idea and returns a runtime-issued Story Director critique task. Spawn exactly that returned task with its explicit model/effort. The critique must cover every required axis in the task packet. Commit it with idea-review using the same model/effort, then require the human to idea-approve or idea-reject. Accepted AuthorIntent is searchable planning context but not Canon.

Use preference-set only for explicit or sufficiently supported author preferences.

## Character emergence

When motivation plausibly challenges the rolling outline, call sandbox-next first. Spawn exactly the returned novel-character-agent task and commit its perception/memory/intention/action/consequence result with sandbox-propose using the returned task/model/effort. Human/runtime acceptance through sandbox-accept creates a replan request but never mutates Canon. Apply or reject replans explicitly.

## Korean craft

Use korean-fiction-craft guidance. Revision priority: structure, character, chapter execution, dialogue, prose. Respect relationship-specific honorific/banmal, address forms, pressure register, emotional leakage, subtext and rhythm. Quality audits are advisory and must not flatten deliberate author voice.
