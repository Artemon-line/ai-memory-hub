# Client Feedback Improvement Plan

Source date: `15-06-2026`

Status: partial

## Goal

Turn real opencode and Codex MCP usage feedback into a concrete improvement backlog that makes ai-memory-hub easier for agents to use, easier for users to trust, and less surprising at the response-schema level.

## Feedback Summary

Observed positives:

- opencode found the MCP straightforward because the tools are simple and the schemas are predictable.
- Codex found fact answers easy to use because normalized facts returned citations and confidence.
- Both clients could use the core insert/search/profile/ask flow without a new integration layer.

Observed improvement themes:

- Nested conversation JSON is easy enough, but clients still need guidance to structure messages and tags correctly.
- `memory_ask` should avoid surprising shapes such as a successful fact answer with `results: []`.
- Fact answers need cleaner user-facing text while preserving raw stored memory.
- Confidence should explain why it is high, medium, low, or absent.
- Personal facts need freshness fields such as creation time, update time, and last confirmation.
- Stored facts need light normalization for spelling/casing without destroying provenance.
- Auto-tagging, conversation threading, mutation workflows, richer filters, bulk insert, and summaries would reduce manual work.

## P0: Response Shape Clarity

Source feedback:

- Codex saw `results: []` even though the answer came from the fact layer.
- Codex wanted raw facts and polished answers separated.

Implementation sequence:

- [x] Make `memory_ask` return structured evidence for fact-backed successful answer paths:
  - [x] fact evidence for `fact_layer`
  - [x] conflicting fact evidence for `conflict`
  - [x] fact evidence plus retrieved chunks for `mixed`
  - [x] general `evidence` entries for pure `direct_memory` chunk answers
- [x] Keep `answer` as the user-facing polished string.
- [x] Add stable `evidence` and `structured_evidence.facts` sections for normalized fact-layer evidence instead of overloading chunk-shaped `results`.
- [x] Document the exact relationship between `answer`, `results`, `citations`, `provenance`, and fact evidence in public API docs.
- [x] Add tests for fact-only answers, mixed answers, and conflict answers.
- [x] Add explicit no-hit response-shape tests for `structured_evidence`.

Acceptance criteria:

- A fact-layer answer no longer looks like a miss to clients that inspect structured fields.
- Raw stored text, normalized facts, citations, and polished answer text are distinguishable.
- Existing clients that read `answer`, `status`, and `confidence` continue to work.

## P0: Fact Freshness And Source Quality

Source feedback:

- Codex wanted to know why confidence is high, such as direct user statement versus inference.
- Codex wanted freshness fields for personal facts.

Implementation sequence:

- [x] Add `source_quality` and `confidence_reason` to fact and ask responses.
- [x] Start with deterministic values:
  - [x] `direct_user_statement`
  - [x] `assistant_statement`
  - [x] `inferred_from_conversation` for recurring-topic facts
  - [x] `corrected_by_user`
  - [x] conflict answers expose a confidence reason for disagreeing active facts
- [x] Add or expose fact freshness fields:
  - [x] `created_at`
  - [x] `updated_at`
  - [x] `last_confirmed_at`
  - [x] `superseded_at` where applicable
- [x] Update correction/supersession flows so direct user confirmations refresh `last_confirmed_at`.
- [x] Add API and MCP tests for freshness and source-quality fields.
- [x] Add SQLite and Postgres storage-level tests for persisted freshness/source-quality fields.

Acceptance criteria:

- A client can explain why confidence is high without reading raw conversation chunks.
- A user can tell whether a personal fact is recent, stale, corrected, or superseded.

## P1: Fact Text Normalization And Answer Polish

Source feedback:

- Codex saw misspelled or awkward answer text such as `aniversary`.
- Codex asked for spelling/casing normalization or separate raw and polished fields.

Implementation sequence:

- [x] Preserve raw source text exactly in provenance and source conversations.
- [ ] Add normalized fact fields for display and matching:
  - [x] `object_raw`
  - [x] `object_normalized`
  - [ ] optional structured qualifiers for dates, names, casing, and common spelling fixes
- [x] Use normalized fact values for answer wording when confidence is high.
- [x] Keep normalization deterministic at first; add hosted or local LLM cleanup only behind explicit configuration.
- [x] Add regression tests for common spelling/casing cleanup and no-overwrite provenance behavior.

Acceptance criteria:

- User-facing answers are readable and consistently cased.
- The system can still cite and audit the original stored text.
- Normalization never silently changes source conversation payloads.

## P1: Auto-Tagging

Source feedback:

- opencode suggested inferring tags instead of requiring manual tags.

Implementation sequence:

- [ ] Generate deterministic tags from extracted topics, entities, source, and fact predicates.
- [ ] Store generated tags separately from user-supplied tags:
  - [ ] `tags`
  - [ ] `auto_tags`
  - [ ] `tag_sources`
- [ ] Use auto-tags for retrieval reranking only after tests show no precision regression.
- [ ] Expose auto-tags in search/retrieve responses and CLI output.
- [ ] Add tests for generated tags, manual tag preservation, and metadata-aware reranking.

Acceptance criteria:

- Users and agents can omit tags for common conversations and still get useful metadata.
- Manual tags remain authoritative and are not overwritten by generated tags.

## P1: Conversation Threading

Source feedback:

- opencode suggested linking related conversations or continuing a thread over time.

Implementation sequence:

- [ ] Promote existing upstream-thread metadata into a documented cross-client thread model.
- [ ] Add `thread_id`, `parent_conversation_id`, and `related_conversation_ids` where storage adapters can support them.
- [ ] Add append-or-continue behavior for clients that send a stable upstream thread id.
- [ ] Add search filters and result grouping by thread.
- [ ] Add tests for cross-client handoff, append-only updates, and thread-aware retrieval.

Acceptance criteria:

- A user can ask about a project or topic across multiple Codex/opencode conversations and receive thread-aware context.
- Threading does not collapse unrelated conversations just because they share broad tags.

## P2: Mutation Workflows

Source feedback:

- opencode wanted delete/update in addition to supersession.

Implementation sequence:

- [ ] Define mutation semantics before adding tools:
  - [ ] soft delete conversation
  - [ ] soft delete fact
  - [ ] edit metadata only
  - [ ] append messages
  - [ ] supersede fact
- [ ] Keep source conversation payloads immutable by default.
- [ ] Add admin-scoped API and MCP tools only after auth scope handling is clear.
- [ ] Add audit fields for who/what performed a mutation.
- [ ] Add provider contract tests for metadata and vector cleanup parity.

Acceptance criteria:

- Users can remove or hide incorrect memory without losing auditability by default.
- Vector and metadata stores remain consistent after delete/update operations.

## P2: Advanced Search Filters And Bulk Operations

Source feedback:

- opencode wanted filters by date range, tags, and source.
- opencode wanted bulk conversation insert.

Current status:

- Source, date range, and tag filters already exist for `memory_search`.
- Bulk insert is not yet a first-class operation.

Implementation sequence:

- [x] Document current filters in MCP tool descriptions and initialize instructions.
- [ ] Add filter support to `memory_ask` where it can preserve answer quality.
- [ ] Add fact/profile filters for source, predicate, date range, confidence, active/superseded status, and freshness.
- [ ] Add `memory_insert_many` or batch HTTP ingest with per-item success/error envelopes.
- [ ] Add tests for partial batch failure, deduplication, and stable ordering.

Acceptance criteria:

- Agents can narrow retrieval without post-filtering client-side.
- Bulk insert can import many conversations while reporting item-level failures clearly.

## P2: Summaries And Profile Views

Source feedback:

- opencode wanted a built-in way to get a profile summary without fetching raw chunks.

Implementation sequence:

- [ ] Add per-conversation summaries as planned in the summaries roadmap.
- [ ] Add a profile summary response that combines active facts, freshness, and compact provenance.
- [ ] Add topic and project summaries after conversation summaries are reliable.
- [ ] Keep summary storage separate from raw chunks and normalized facts.
- [ ] Add tests showing summaries cite their source conversations or facts.

Acceptance criteria:

- Agents can fetch a compact profile or project summary without reading every raw chunk.
- Summaries are traceable and refreshable.

## Priority Mapping

- P0: response shape clarity, fact freshness, and source-quality explanation.
- P1: fact text normalization, answer polish, auto-tagging, and conversation threading.
- P2: mutation workflows, advanced filters, bulk operations, and summaries.

## Done When

- The highest-priority feedback items are represented as tracked roadmap work.
- The MCP response contract is clearer for both chunk-backed and fact-backed answers.
- Fact answers expose source quality, freshness, raw values, and polished values without breaking existing clients.
- Manual tagging and single-conversation insert remain supported, but agents can rely on auto-tagging and batch workflows where available.
