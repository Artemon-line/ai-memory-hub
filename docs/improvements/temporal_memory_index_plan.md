# Temporal Memory Index Plan

## Context

The hub should store memory as timestamped events, not as a hidden user-profile
database. Deterministic extraction can still create a fact index for faster
recall, but that index must remain a derived projection over immutable memories.

QA examples such as "favorite food" and "likes potato chips" are useful
fixtures, but they should not become product semantics. The implementation
should not assume ai-memory-hub is collecting preferences, likes, dislikes, or
profile fields as a primary goal.

## Target Model

- [ ] Treat stored conversations as the source of truth.
- [ ] Treat extracted facts as derived, rebuildable index rows.
- [ ] Preserve each observed value with the source memory timestamp.
- [ ] Return the latest applicable value in compact/default reads.
- [ ] Return the value timeline, sources, and correction links in detailed reads.
- [ ] Use corrections to link or bound derived values, not erase historical memory.
- [ ] Keep fact extraction generic enough for arbitrary memory content.
- [ ] Avoid fixture-shaped product behavior for favorites, likes, and profile fields.

## Phase 1: Inventory Current Profile-Shaped Behavior

- [ ] List every deterministic extraction rule that creates user-profile-style facts.
- [ ] Identify tests that rely on favorite/likes/profile examples only as QA fixtures.
- [ ] Identify tests that encode actual public API compatibility.
- [ ] Separate generic temporal-memory expectations from fixture-specific wording.
- [ ] Document current concise versus detailed response fields for ask/search/retrieve/fact reads.

## Phase 2: Define Temporal Fact Semantics

- [ ] Add explicit terminology for observed value, current projection, correction link, and historical value.
- [ ] Decide whether derived rows use `observed_at`, `valid_from`, and `valid_until`, or a smaller timestamp/link model.
- [ ] Define how a correction phrase maps to a new observation plus a link to prior matching observations.
- [ ] Define how compact reads choose the latest value when multiple active historical values exist.
- [ ] Define how detailed reads expose older values without calling them deleted or invalid.
- [ ] Define how conflicts differ from normal temporal changes.

## Phase 3: Refactor Extraction Away From Favorite Plumbing

- [ ] Replace favorite-specific helpers with generic attribute extraction helpers.
- [ ] Keep named regex groups or parser fields local to extraction, not spread through product logic.
- [ ] Avoid hardcoded `favorite_...` predicates outside compatibility adapters and tests.
- [ ] Keep "likes" extraction as either a generic statement pattern or remove it from default deterministic extraction.
- [ ] Make fixture tests assert temporal behavior rather than a special favorite domain.

## Phase 4: Update Read Behavior

- [ ] Make compact `memory_ask` prefer the latest derived projection when a question asks for a current value.
- [ ] Make detailed `memory_ask` include timeline evidence for older observed values.
- [ ] Keep `memory_search` and `memory_retrieve` grounded in stored memory records.
- [ ] Ensure `memory_fact_search` can filter or group by latest-only versus timeline views.
- [ ] Preserve backwards-compatible detailed/audit payloads where clients may depend on them.

## Phase 5: Migration And Compatibility

- [ ] Decide whether existing fact rows need a schema migration or can be interpreted with existing timestamps.
- [ ] Preserve existing public field names unless a compatibility adapter can bridge them.
- [ ] Add tests for older favorite/likes fixtures so compatibility does not regress accidentally.
- [ ] Add tests that prove new temporal semantics with non-profile examples.
- [ ] Document any deprecated fixture-shaped behavior.

## Phase 6: Validation

- [ ] Run focused extraction and ask tests.
- [ ] Run MCP concise/detailed response shape tests.
- [ ] Run SQLite-backed integration tests for temporal projections.
- [ ] Run `uv run python -m ruff check memory tests tools`.
- [ ] Run `uv run python -m pyright`.
- [ ] Run `uv run pytest tests/unit tests/integration -q`.
- [ ] Update the PR description with the completed phase and validation results.
- [ ] Push and monitor CI before marking the phase done.

## Open Questions

- [ ] Should the fact index expose `current=true` rows, or should current-ness be computed at read time?
- [ ] Should corrections set `valid_until` on older derived facts, or only add `superseded_by`/`corrected_by` links?
- [ ] Should deterministic extraction remain enabled by default for user-profile-like sentences?
- [ ] Should direct fact upsert exist as a separate tool, or should all fact-like updates come from timestamped memory inserts?
- [ ] What exact compact payload should Hermes-style clients receive for latest value plus timestamp?
