# State model

Schema version: 4.

## Canon

Canonical history is revisioned rather than overwritten.

First-class state:
- character definition and mutable state
- knows / suspects / falsely_believes / unknown
- relationships
- events and occurrence order
- reader reveals and disclosure order
- object ownership/location/state
- world rules
- plot threads
- foreshadows
- promises/payoffs
- reader questions

Event occurrence and reader disclosure are intentionally separate.

Graph edges are revisioned with an active flag. Removed causal, ownership, or location links create tombstones, so as-of-chapter queries reconstruct historical state.

## Foreshadow lifecycle

    PLANNED -> PLANTED -> HINTED -> REINFORCED -> DUE -> PAID_OFF

Alternatives: DELAYED, ABANDONED.

State revisions are scoped by source-memory effective chapter, preventing a future payoff from leaking into earlier context.

## Author namespaces

AuthorIntent stores human ideas and critique/decision lifecycle.

AuthorPreference stores reusable preferences with confidence/evidence.

Neither is Canon.

## Runtime state

Runtime tables track plans, runs, transitions, checkpoints, agent tasks, candidates/preflights, tournament aggregation, frozen/repaired drafts, reviews/adjudication, regression, state scans, delta validation, revisions/publications, model route decisions, style profiles, voice bibles, character simulations, and replan requests.

## Pending authority

Publish creates pending memory only. memory-approve is the authority boundary that applies a proposal and increments canonical revision.

## Dependencies and projections

Published revisions store read points. Later Canon changes mark dependent future revisions stale.

Rebuildable projections:
- FTS/search docs
- optional vector embeddings
- typed narrative graph query surface
