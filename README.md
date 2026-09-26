# Novel Agent Framework

Korean-only, Codex-first long-form fiction framework with a deterministic runtime.

This is not a prompt bundle. It installs an executable .novel runtime into another Git repository, registers project-local Codex agents and skills, and keeps story authority in a versioned SQLite database.

## Core guarantees

- Chapter is the canonical generation/revision unit.
- Exactly three candidates are generated: A / B / C.
- Candidate prose must pass deterministic scanner preflight before tournament.
- Tournament identities are blinded as P / Q / R and compared in both orders.
- Cycles, ties, or low-confidence tournaments escalate to explicit Sol adjudication.
- Review is gated in two phases: narrative validity first, then dialogue/style/blind reader.
- Reviewers are independent. A single bounded cross-examination round is available only for genuine conflicts.
- Final prose is independently scanned. ExpectedDelta, DeclaredDelta, and ObservedDelta must agree before publication.
- Publishing prose never mutates Canon. Memory proposals remain pending until explicit human approval, including autonomous mode.
- SQLite is the sole canonical truth. FTS, vector embeddings, and graph indexes are rebuildable projections.
- Retrieval is temporal and audience-scoped. Reader context cannot see author-only/future state.
- Canon changes propagate stale markers to dependent future chapter revisions.
- Run state is checkpointed, resumable, idempotent, bounded-retry, and fail-closed.
- Korean author style profiles and relationship-specific voice bibles are first-class context.
- Character sandbox emergence can create explicit rolling-replan requests without silently changing Canon.

## Installed shape

    my-novel/
      .novel/
        bin/novel.py
        runtime/novel_agent_framework/
        config/model-routing.json
        config/retrieval.json
        state/story.sqlite3
        manifest.json
      .codex/
        config.toml
        agents/novel-*.toml
      .agents/skills/
        novel/SKILL.md
        korean-fiction-craft/SKILL.md
      AGENTS.md

The installed target does not depend on the framework source checkout.

## Install

    git init /path/to/my-novel
    python3 scripts/install_project.py /path/to/my-novel
    cd /path/to/my-novel

    python3 .novel/bin/novel.py --project-root . init       --title "내 소설"       --approval-mode every_chapter

    python3 .novel/bin/novel.py --project-root . doctor

Dry run:

    python3 scripts/install_project.py /path/to/my-novel --dry-run

Open Codex in the target repository and invoke $novel.

Examples:

    $novel 새 소설 시작하자.
    $novel 다음 챕터 진행해.
    $novel 이 아이디어를 비판적으로 검토해줘: ...
    $novel 자동 진행 모드로 바꿔.
    $novel pending memory 보여줘.

## Runtime-driven orchestration

The Codex lead does not invent the next semantic step. It asks the runtime:

    python3 .novel/bin/novel.py --project-root . workflow-next
    python3 .novel/bin/novel.py --project-root . orchestrate-next --run RUN_ID

The runtime returns task envelopes containing exact role, model, reasoning effort, stage, stage-specific input packet, output contract, and retry attempt.

Semantic writes are rejected when no matching runtime-issued task exists.

Canonical chapter flow:

    planning
      -> writer A/B/C
      -> scanner preflight A/B/C
      -> anonymous two-order tournament
      -> freeze winner
      -> narrative reviewers
      -> craft/dialogue/blind-reader reviewers
      -> Story Director adjudication
      -> surgical repair
      -> regression check if repaired
      -> independent state scanner
      -> three-way delta validation
      -> publish exact prose
      -> pending memory
      -> human memory approval
      -> projection rebuild + stale propagation

## Agents

The installed role set contains the original 12 narrative roles plus one isolated state-scanner helper:

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
- novel-state-scanner

Workers do not recursively delegate.

## Memory and narrative world model

Canonical namespaces include character definitions/state, epistemic state, relationships, events, reader reveals, objects, world rules, plot threads, foreshadows, promises/payoffs, reader questions, AuthorIntent, and AuthorPreference.

Temporal graph edges support historical as-of-chapter queries and explicit tombstones for removed ownership/location/causal links.

## Retrieval

Default core retrieval:

    SQLite Canon
       -> FTS5/BM25 (LIKE fallback)
       -> temporal graph expansion

Optional vector retrieval is fused with lexical ranking by reciprocal-rank fusion.

Extras:

    python3 -m pip install -e '.[local-embeddings]'
    python3 -m pip install -e '.[openai-embeddings]'

Providers:
- disabled: default, FTS + graph only
- sentence-transformers
- OpenAI embeddings

Vector indexes are rebuildable and never become Canon.

## Korean prose / voice

The framework supports author-sample style fingerprinting, sentence/paragraph rhythm statistics, lexical diversity, punctuation profiles, Korean speech-level markers, character/relationship-specific voice bibles, address forms, advisory generic/cliche/rhythm checks, and narrative-texture flags.

Quality checks are advisory. They do not mechanically flatten deliberate author voice.

## Character emergence and replanning

A character sandbox stores:

    perception -> memory -> intention -> action -> consequence

Accepted emergence creates a pending replan request. The rolling outline changes only through an explicit replan action; it does not directly mutate Canon.

## Model routing

config/model-routing.json is fail-closed.

Typical policy:
- Luna low/medium: routine planning, writing, scanners, reviewers
- Sol high: long-range structural planning and hard adjudication
- Writer default: Luna low
- Writer none-vs-low: measurable benchmark path

Every runtime-issued task records model and reasoning effort.

## Important CLI surfaces

Project/runtime:
- doctor / migrate / init / status
- workflow-next / orchestrate-next / resume / run-abort

Planning:
- story-compass-set / outline-put / planning-complete
- plan-put / plan-approve
- sandbox-propose / sandbox-accept
- replan-list / replan-apply / replan-reject

Generation/tournament:
- candidate-add / candidate-preflight / candidates
- tournament-packets / pairwise-complete
- tournament-hard-complete / tournament-select

Review/revision:
- review-add / review-adjudicate / review-hard-complete
- revision-manifest / regression-add / state-scan
- delta-validate / publish

Memory/retrieval:
- memory-list / memory-approve / memory-reject / memory-stage
- idea-propose / idea-review / idea-approve / idea-reject
- preference-set
- context / reader-context / graph-query
- rebuild-index / rebuild-vectors

Style/eval:
- style-profile-create / style-profile-list / style-compare
- voice-bible-put
- quality-eval / benchmark-writer / eval
- backup / export

## Validation

Core runtime has no mandatory third-party dependency.

    python3 scripts/check.py
    python3 scripts/check.py --smoke

Default smoke includes an installed-target E2E and a 30-chapter long-horizon regression.

Extended long horizon:

    python3 scripts/long_horizon_smoke.py --chapters 100

Authenticated Codex live execution is checked separately by doctor. Deterministic tests do not fake live authentication.
