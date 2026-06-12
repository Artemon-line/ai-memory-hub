# Conversation Grouping Plan

Source date: `28-05-2026`

Status: implemented

## Goal

Improve how related chunks from the same conversation are surfaced so grouped context is easier to consume.

## Scope

- [x] Add conversation-level scoring.
- [x] Ensure chunks from the same conversation can be grouped or prioritized together.
- [x] Keep this focused on grouping behavior rather than search precision in general.

## Phases

### Phase 1: Scoring model

- [x] Define how conversation-level score should be computed.
- [x] Decide whether it acts as a boost, a grouping signal, or both.
- [x] Keep the per-chunk score visible so the behavior stays explainable.

Acceptance criteria:

- [x] The grouping/scoring rule is clearly defined.
- [x] Related chunks can be identified as belonging together.
- [x] The score model does not obscure the base retrieval signal.

### Phase 2: Group-aware ranking

- [x] Apply conversation-level scoring after the base retrieval step.
- [x] Prefer sets of chunks that form a coherent conversation when appropriate.
- [x] Keep unrelated but individually strong chunks from being incorrectly merged.

Acceptance criteria:

- [x] Chunks from the same conversation are surfaced together when relevant.
- [x] Strong unrelated matches are not accidentally suppressed.
- [x] Ranking remains stable and understandable.

### Phase 3: Output validation

- [x] Ensure grouped results remain compatible with `memory_ask`.
- [x] Confirm citations and summaries still point to the correct underlying chunks.
- [x] Keep the grouping behavior optional or conservative if needed.

Acceptance criteria:

- [x] `memory_ask` can consume grouped context without breaking output shape.
- [x] Citations remain accurate at the chunk level.
- [x] Grouping improves readability without damaging precision.

## Testing

- [x] Unit tests for conversation score calculation.
- [x] Retrieval tests for grouped results from the same conversation.
- [x] Regression tests to ensure unrelated results are not over-grouped.

## Done When

- [x] Related chunks are surfaced together more consistently.
- [x] The grouped view improves readability of search/ask output.
- [x] Existing result accuracy is preserved.

## Implementation Notes

- Implemented in `memory.ingestion.mvp_ingestion.search`,
  `group_conversation_results`, and `_apply_result_mode`.
- `conversation_score` is the best score for a conversation group, and
  `conversation_match_count` exposes how many chunks matched.
- Grouping is conservative: only chunks within the configured conversation score
  window are grouped ahead of unrelated matches.
- `result_mode` supports `chunks`, `compact`, and `conversations` through CLI,
  HTTP, MCP search, and MCP ask paths.
