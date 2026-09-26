# Codex integration

## Installed roles

Narrative roles:
- novel-story-director
- novel-plot-architect
- novel-world-builder
- novel-character-agent
- novel-chapter-planner
- novel-writer
- novel-dialogue-specialist
- novel-continuity-reviewer
- novel-character-consistency-reviewer
- novel-foreshadowing-tracker
- novel-style-critic
- novel-reader-simulator

Isolation/helper:
- novel-state-scanner

## Lead contract

The installed $novel skill starts from runtime state.

Use workflow-next for project/planning semantic tasks and orchestrate-next --run ID for chapter tasks.

The runtime envelope is authoritative for role, model, reasoning effort, input, output contract, and attempt. Workers never recursively delegate.

Project-level semantic work follows the same authority model: idea-propose returns a Story Director IdeaCritique task, and sandbox-next returns a Character Agent simulation task. idea-review and sandbox-propose reject outputs that do not match their issued task/model/effort.

## Model routing

Routing is fail-closed. Runtime semantic mutation methods validate the exact task model/effort before accepting output.

## Reader isolation

The Reader task receives a reader-scoped context packet built by runtime rather than an author packet filtered only by prompt instruction.

## Live readiness

doctor reports runtime/schema, roles, skill, Codex CLI version, and login readiness. Static/deterministic validation does not claim authenticated model execution when Codex is logged out.
