# Workflow

## Planning

1. Runtime inspects project state through workflow-next.
2. Story Director selects planning specialists.
3. Plot/world/character specialists run only when selected.
4. Chapter Planner creates ChapterPlan, ChapterContract, and ExpectedDelta.
5. Every-chapter mode requires human plan approval; autonomous mode may approve the plan automatically.

## Chapter execution

1. Start durable chapter run.
2. orchestrate-next issues exactly three writer tasks.
3. State Scanner independently extracts observations for A/B/C.
4. All A/B/C preflights must pass.
5. Runtime issues blinded P/Q/R pairwise tasks in both orders.
6. Clear aggregate freezes a winner; ambiguity escalates to hard Sol adjudication.
7. Narrative reviewers independently inspect the same frozen draft.
8. Only after narrative gate completion are dialogue, style, and blind Reader issued.
9. Story Director adjudicates the bundle; genuine conflict allows one bounded hard path.
10. Runtime issues surgical repair.
11. Non-trivial repair must pass regression.
12. Independent scanner receives final prose without DeclaredDelta and emits ObservedDelta.
13. Three-way delta mismatch blocks publication.
14. Publication commits exact prose and stages pending memory.
15. Human accepts/rejects memory.
16. Canon advances, projections rebuild, and dependent future revisions become stale.

## Reader isolation

Reader Simulator sees the current frozen draft plus reader-visible history through chapter N-1. It never receives future outline, AuthorIntent, AuthorPreference, hidden facts, private knowledge, secret world rules, or future foreshadow plans.

## Human ideas

    propose -> critical review -> human decision -> AuthorIntent
      -> planning/realization -> scan -> publish -> pending memory -> Canon approval

Accepting an idea never means the event already happened.

## Character emergence

Character sandbox results are proposals. Accepted emergence creates a rolling-replan request; applying it updates the rolling outline, not Canon.

## Recovery

Every major transition writes a checkpoint. Resume uses durable run/task state. Identical replay is idempotent; conflicting replay fails closed.
