# Temporal Memory Index Plan

## Context

The hub should store memory as timestamped events, not as a hidden user-profile
database. Deterministic extraction can still create a fact index for faster
recall, but that index must remain a derived projection over immutable memories.

Here, "deterministic extraction" means rule-based extraction that runs without a
model, for example parsing a sentence into a structured derived row. It should be
treated as indexing plumbing, not as a decision to collect a user profile.

QA examples such as "favorite food" and "likes potato chips" are useful
fixtures, but they should not become product semantics. The implementation
should not assume ai-memory-hub is collecting preferences, likes, dislikes, or
profile fields as a primary goal.

## Target Model

- [x] Treat stored conversations as the source of truth.
- [x] Treat extracted facts as derived, rebuildable index rows.
- [x] Preserve each observed value with the source memory timestamp.
- [x] Compute the latest applicable value at read time from source timestamps.
- [x] Do not store `current=true`, `valid_from`, or `valid_until` as source-of-truth state.
- [x] Return the latest value, latest stored timestamp, and author in compact/default reads.
- [x] Return the value timeline, sources, authors, and timestamps in detailed reads.
- [x] Use corrections as timestamped memory events, not as historical erasure.
- [ ] Keep fact extraction generic enough for arbitrary memory content.
- [ ] Avoid fixture-shaped product behavior for favorites, likes, and profile fields.

## Phase 1: Inventory Current Profile-Shaped Behavior

- [x] List every deterministic extraction rule that creates user-profile-style facts.
- [x] Identify tests that rely on favorite/likes/profile examples only as QA fixtures.
- [x] Identify tests that encode actual public API compatibility.
- [x] Separate generic temporal-memory expectations from fixture-specific wording.
- [x] Document current concise versus detailed response fields for ask/search/retrieve/fact reads.

Current deterministic extraction rules include ownership, favorites, likes,
creator, subject creator, command name, indexing strategy, generic project
attributes, profile name, profile identity, profile role, profile location,
recurring topics, and optional external extractor rows. Favorites, likes, and
profile examples remain compatibility and QA fixtures; the temporal projection
tests use command/indexing facts to avoid encoding preferences as product
semantics.

## Phase 2: Define Temporal Fact Semantics

- [x] Add explicit terminology for stored memory event, observed value, latest projection, and historical value.
- [x] Use the source memory timestamp as the only temporal source of truth.
- [x] Define how correction phrases are stored as ordinary timestamped memory events.
- [x] Define how compact reads choose the latest value when multiple active historical values exist.
- [x] Define how detailed reads expose older values without calling them deleted or invalid.
- [x] Define how conflicts differ from normal temporal changes.

Implemented semantics: a stored memory event is the conversation payload; an
observed value is a derived fact row pointing back to a stored memory event; the
latest projection is computed at read time from the source memory timestamp; a
historical value is any older observed value for the same question. Conflicts
are limited to multiple distinct values in the same latest timestamp group.

## Phase 3: Refactor Extraction Away From Favorite Plumbing

- [ ] Replace favorite-specific helpers with generic attribute extraction helpers.
- [x] Keep named regex groups or parser fields local to extraction, not spread through product logic.
- [x] Avoid hardcoded `favorite_...` predicates outside compatibility adapters and tests.
- [ ] Re-evaluate whether profile-like deterministic extraction should run by default.
- [ ] Keep "likes" extraction only if it behaves as generic memory indexing, not profile collection.
- [ ] Make fixture tests assert temporal behavior rather than a special favorite domain.

## Phase 4: Update Read Behavior

- [x] Make compact `memory_ask` prefer the latest derived projection when a question asks for a current value.
- [x] Include `value`, `stored_at`, and `author` for latest-value compact answers.
- [x] Make detailed `memory_ask` include timeline evidence for older observed values.
- [x] Keep `memory_search` and `memory_retrieve` grounded in stored memory records.
- [x] Synchronize detailed `memory_retrieve` index chunk state after embedding so
      audit reads match searchable/indexed storage state.
- [x] Retry generic active temporal fact projection before direct chunk fallback
      when a specific question parser finds no active facts.
- [x] Deduplicate human-readable profile summary lines at read time while
      preserving observed fact rows for timeline and audit views.
- [ ] Ensure `memory_fact_search` can filter or group by latest-only versus timeline views.
- [x] Add free-text `memory_fact_search.query` for agent-facing lookup before a client knows the subject or predicate.
- [x] Pin concise fact search to emit deduplicated rows by default, not only when a custom limit is supplied.
- [x] Preserve backwards-compatible detailed/audit payloads where clients may depend on them.

## Phase 5: Migration And Compatibility

- [x] Decide whether existing fact rows need a schema migration or can be interpreted with existing timestamps.
- [x] Preserve existing public field names unless a compatibility adapter can bridge them.
- [x] Add tests for older favorite/likes fixtures so compatibility does not regress accidentally.
- [x] Add tests that prove new temporal semantics with non-profile examples.
- [ ] Document any deprecated fixture-shaped behavior.

## Phase 6: Validation

- [x] Run focused extraction and ask tests.
- [x] Run MCP concise/detailed response shape tests.
- [x] Run SQLite-backed integration tests for temporal projections.
- [x] Add agent-agnostic MCP ask contract tests for concise latest-value and direct-memory fallback shapes.
- [x] Add agent-agnostic fact-search contract tests for free-text lookup and default concise dedupe.
- [x] Add regression coverage for detailed retrieve index-state consistency and
      corrected temporal facts winning over stale direct chunks.
- [x] Add an MCP insert stability contract for repeated fresh timestamped
      memory inserts.
- [x] Run `uv run python -m ruff check memory tests tools`.
- [x] Run `uv run python -m pyright`.
- [x] Run `uv run pytest tests/unit tests/integration -q`.
- [x] Update the PR description with the completed phase and validation results.
- [x] Push and monitor CI before marking the phase done.

## Decisions From Review

- [x] Compute current-ness at read time from timestamps; do not expose or persist `current=true`.
- [x] Do not set `valid_until` on older derived facts; memory is stored in time and read as a timeline.
- [x] Compact latest-value payloads should include the latest value, the latest stored timestamp, and the author/agent that wrote the source memory.
- [ ] Decide whether deterministic extraction should remain enabled by default for profile-shaped sentences.
- [x] Keep fact-like writes on timestamped memory inserts; do not add direct fact upsert in this phase.
