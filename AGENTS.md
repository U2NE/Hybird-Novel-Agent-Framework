# Repository instructions

This repository implements the Novel Agent Framework itself.

- This is an executable runtime, not a Markdown prompt collection.
- Installed .novel/state/story.sqlite3 is the sole canonical story-state database.
- Creative LLM work never owns deterministic state-transition authority.
- Prose output alone never mutates Canon.
- Publishing prose and approving PendingMemoryProposal into Canon are separate.
- Keep Codex dispatch flat; only the lead spawns sibling narrative agents.
- Every controlled inference uses an explicit allowlisted model and reasoning effort.
- Fail closed on unsupported schema, corrupt state, invalid transitions, duplicate immutable artifacts, or unavailable explicit routes.
- Chapter is the canonical generation/revision unit.
- Generate exactly A/B/C candidates.
- Reviewer artifacts are immutable and isolated.
- Reader Simulator is blind to future and author-only state.
- Non-trivial repair requires final prose, revised DeclaredDelta, and passing regression check before delta validation.
- Prefer deterministic tests for authority/state rules and live model smoke only for model-dependent behavior.
- Do not copy restrictive-license reference code.
