# P0 Live MCP Retest Follow-Up Plan

Source date: `2026-09-02`

Status: planned

Priority: P0 before claiming stable agent UX over noisy real hubs

## Goal

Close the remaining live-retest gaps found by Hermes and Codex after the MCP
response-format, temporal fact selection, and retrieval relevance fixes. The hub
should behave as a timestamped memory service: immutable memories are the source
of truth, current answers are computed at read time from stored timestamps, and
concise agent responses expose the latest value, stored timestamp, and author
without forcing clients to parse raw evidence.

This plan tracks product-quality follow-up work only. Automated MCP contract
tests should prove behavior; Hermes and other agents should be used as external
UX reviewers, not as the primary test runner.

## Source Evidence

Latest live retests saved in the hub:

| Reviewer | Saved memory | Stored at | Summary |
| --- | --- | --- | --- |
| Hermes-side review | `60a7a2df-9cff-44b8-be10-ef4d6849f63f` | `2026-09-02T10:33:29Z` | Concise/native JSON was fixed across ask, search, fact search, and profile tools. Small sequential inserts were stable. Remaining concerns were noisy fact canonicalization, repeated profile facts, incomplete superseding, inconsistent arbitrary-predicate queries, no unified lookup/direct fact upsert, and unverified large-payload stability. Stored with source `mcp`, not an explicit Hermes source label. |
| Codex review | `08911a65-e7f9-45bf-83dd-164fea26f236` | `2026-09-02T10:33:12Z` | Fact-layer routing, current temporal selection, latest correction superseding, direct-memory synthesis, concise/native JSON, pagination, and preference extraction passed. Remaining concerns were `92` active facts versus `26` unique facts, duplicate/noisy profile facts, incomplete older-correction cleanup, multiple temporal facts remaining active, and missing unified lookup/direct fact-upsert operations. |

Earlier supporting marker:

- Live Codex retest marker `AMH-QA-20260901-CODEX-RT5F19`.

## Confirmed Fixed

- [x] Read tools default to `response_format="concise"` and return native JSON.
- [x] `memory_ask` concise returns a compact answer shape instead of a long raw
      evidence narrative.
- [x] Fact-layer routing works for scoped direct questions.
- [x] New timestamped corrections prefer the latest value at read time.
- [x] Dedup/idempotency returns the original memory ID on exact replay.
- [x] Pagination and explicit filters work for the retested search paths.
- [x] Basic sequential `memory_insert` stability passed through live MCP.

## P0 Follow-Ups

### Phase 1: Canonicalize Fact Presentation

- [ ] Make profile summaries and concise fact reads group equivalent facts by
      subject, predicate, and normalized object without emitting repeated
      human-readable lines.
- [ ] Keep older timestamped facts inspectable in detailed/audit formats without
      marking them as deleted or relying on persisted `current=true` state.
- [ ] Add fixture coverage for repeated `recurring_topic`, arbitrary predicate,
      and preference-shaped facts so the summary text cannot drift away from the
      deduplicated rows.
- [ ] Report `active_count`, `unique_count`, and `omitted_count` consistently
      when compact responses collapse duplicates.

Acceptance criteria:

- Compact profile/fact output does not repeat equivalent facts.
- Detailed output still exposes timestamped history and provenance.
- Current/latest status is computed from timestamps at read time.

### Phase 2: Repair Historical Correction Projections

- [ ] Add a read-time projection or repair path for older correction fixtures
      that predate the improved superseding logic.
- [ ] Avoid destructive cleanup: do not erase memory events, do not introduce
      `valid_until`, and do not persist a broad `current=true` flag.
- [ ] Add regression tests where older noisy correction facts coexist with newer
      correction facts and compact reads still select the latest value.
- [ ] Expose enough detailed provenance to explain why an older value was not
      selected.

Acceptance criteria:

- Historical correction fixtures no longer surface stale values in compact
  answers.
- Audit/detailed reads can still show the older timestamped memory events.

### Phase 3: Tighten Exact-Query Relevance In Noisy Hubs

- [ ] Ensure exact marker searches prioritize exact text, metadata, and thread
      matches over unrelated recent large-payload facts.
- [ ] Keep concise search/fact responses bounded by default, including chunk and
      evidence fields.
- [ ] Add tests for unscoped exact-marker queries against a noisy fixture set.
- [ ] Add tests for broad questions that should return a low-confidence conflict
      instead of unrelated recent facts.

Acceptance criteria:

- Exact-marker concise search returns small, high-signal rows.
- Unscoped ask does not prefer unrelated recent facts over exact or scoped
  evidence.

### Phase 4: Add Automated MCP Contract Smoke

- [ ] Build an agent-agnostic contract smoke that starts the local MCP surface,
      inserts fresh timestamped memories, and calls tools through MCP rather
      than only through Python internals.
- [ ] Cover `memory_ask` concise/detailed, `memory_search`,
      `memory_fact_search`, `memory_profile_get`, and `memory_retrieve`.
- [ ] Assert compact ask shape includes latest value, stored timestamp, author,
      answer basis, confidence, and citations/counts as applicable.
- [ ] Include stale correction asks, exact marker search, retrieve index-state
      consistency, duplicate insert, large-payload insert, and burst insert
      scenarios.
- [ ] Keep Hermes/manual prompts as a final UX smell check only.

Acceptance criteria:

- CI can prove the behavior that Hermes and Codex currently retest manually.
- A failed contract reports the tool, fixture marker, expected shape, and actual
  compact payload without dumping private memory text.

### Phase 5: Decide Future API Surface Separately

- [ ] Design a unified lookup tool only if it gives agents a clearly simpler
      read contract than `memory_ask`, `memory_search`, and fact/profile reads.
- [ ] Keep direct fact writes out of the default MCP surface unless a future
      append-only adapter creates a timestamped memory event first.
- [ ] Document that fact-like writes normally come from `memory_insert`, not
      mutable profile upserts.
- [ ] Avoid favorite/profile-specific plumbing in the core model; use generic
      temporal memory projection and compatibility adapters where needed.

Acceptance criteria:

- The default API remains memory-event-first and timestamp-driven.
- Any future convenience tool preserves provenance, author, and stored timestamp.

## Validation

- [ ] `uv run pytest tests/unit tests/integration -q`
- [ ] `uv run python -m ruff check memory tests tools`
- [ ] `uv run python -m pyright`
- [ ] `uv run python tools/prepare_mkdocs.py`
- [ ] `uv run python -m mkdocs build --strict`
- [ ] Live local-stack MCP smoke using fresh disposable markers.

## Done When

- Compact read tools deduplicate presentation without hiding timestamped history.
- Older correction fixtures select the latest value at read time.
- Exact/noisy hub queries stay bounded and relevant.
- CI owns the MCP contract checks that were previously repeated manually through
  Hermes and Codex.
