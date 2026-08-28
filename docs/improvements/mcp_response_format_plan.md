# MCP Response Format Plan

Source date: `24-08-2026`

Status: implemented

Priority: P0 before `v0.1.0`

## Goal

Make ai-memory-hub's MCP read tools token-efficient and easy for agents to
parse without removing the detailed audit shape from the underlying API.

This is release-blocking because MCP is the primary agent integration surface.
If common reads return full conversation envelopes, generated-summary metadata,
auto-tag provenance, and chunk manifests by default, agents spend too much
context budget on fields that rarely help answer the user's question.

## Source Feedback

- Hermes reported that `memory_profile_get` and `memory_search` return heavy
  metadata such as nested qualifiers, duplicated summary fields, `tag_sources`,
  and `index_chunks`.
- Hermes asked for a compact output mode that returns high-signal facts,
  summaries, and citations.
- Roland Huss' MCP tool-design guidance recommends treating MCP tools as an
  agent-facing translation layer, not a thin API mirror. In particular, prefer
  response-format enums over booleans because enum values are easier for models
  to select correctly.

## Design Decision

Add an MCP-facing `response_format` enum instead of `compact: true`.

Initial values:

- `concise`: agent-default shape with only answer-critical fields.
- `detailed`: current audit-friendly shape for clients that need full records.

Reserve any debug/admin-heavy output for later. Do not add it to the first
release contract unless a real client needs it.

Keep `result_mode` separate from `response_format`:

- `result_mode` controls grouping: `chunks`, `compact`, `conversations`, or
  `threads`.
- `response_format` controls payload size and field selection: `concise` or
  `detailed`.

## Scope

Apply `response_format` to the MCP read tools that agents call during recall:

- `memory_search`
- `memory_ask`
- `memory_fact_search`
- `memory_profile_get`

HTTP endpoints may accept the same option later for parity, but the first
release priority is the MCP surface. The HTTP API can keep detailed output as
its stable administrative/audit shape.

## Concise Response Shape

For `memory_search`, concise rows should keep:

- `id`
- `score`
- `text`
- `role`
- `chunk_index`
- `matching_chunks` and `evidence_chunks` when present
- a small citation object with conversation `id`, `source`, `title`,
  `timestamp`, `thread_id`, and generated summary text when available

Concise search rows should drop:

- full `conversation`
- `metadata.index_chunks`
- `metadata.tag_sources`
- duplicate generated summary envelopes
- internal ranking fields

For `memory_profile_get`, concise output should keep:

- `status`
- `subject`
- `summary.text`
- `summary.active_fact_count`
- `summary.freshest_at`
- `summary.confidence_counts`
- `summary.source_quality_counts`
- facts reduced to `subject`, `predicate`, `object`, `object_normalized`,
  `confidence`, `source_quality`, `last_confirmed_at`, and supersession status
- fact rows deduplicated and capped by the concise default or explicit `limit`

For `memory_fact_search`, concise output should keep the same reduced fact
shape used by profile reads, plus total/unique/returned/omitted counts.

For `memory_ask`, concise output should keep:

- `answer`
- `confidence`
- `confidence_reason`
- `answer_basis`
- small memory/fact/citation counts

It should omit citation, evidence, provenance, structured evidence, selected
fact, selected chunk, and token-budget diagnostic arrays unless the client asks
for `response_format="detailed"`.

## Non-Goals

- Do not change storage schema for this work.
- Do not remove detailed/audit output.
- Do not expose general delete/update tools through MCP.
- Do not solve semantic fact deduplication here, except for harmless
  presentational collapse of identical active facts if detailed output still
  exposes the source records.
- Do not add `memory_lookup` or `memory_fact_upsert`; track those separately.

## Implementation Sequence

- [x] Add a shared response-format enum and validation helper.
- [x] Add MCP tool parameters with explicit enum descriptions.
- [x] Keep `detailed` equivalent to the current MCP output.
- [x] Add concise presenter helpers for facts, profile summaries, search rows,
      ask summaries, and conversation citations.
- [x] Strip full conversations and noisy metadata from concise search and ask
      responses.
- [x] Deduplicate and limit concise fact/profile rows while preserving detailed
      full provenance.
- [x] Update MCP initialize instructions and tool descriptions to recommend
      `response_format="concise"` for normal agent recall.
- [x] Update `docs/agents.md` and `docs/overview.md` with concise versus
      detailed examples.
- [x] Update real-client smoke arguments to use `response_format="concise"`
      where supported.
- [x] Add unit and integration coverage for concise and detailed parity.
- [x] Add regression tests that concise output excludes `metadata.index_chunks`,
      `metadata.tag_sources`, and full `conversation` payloads.

## Acceptance Criteria

- MCP read tools expose `response_format` as an enum, not a boolean.
- `response_format="concise"` returns enough information for an agent to answer
  common recall/profile questions without secondary filtering.
- Concise MCP search and ask responses do not include full conversations,
  citation/evidence/provenance arrays, or internal metadata manifests.
- Concise MCP fact/profile responses deduplicate facts and include returned
  counts so clients can see when rows were omitted.
- `response_format="detailed"` preserves the existing audit-friendly shape.
- Tool descriptions explain when to use `concise` versus `detailed` without
  bloating schemas.
- Existing MCP, API, Bruno, and real-client smoke coverage passes after the
  response-format change.
