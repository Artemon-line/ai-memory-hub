# Conversation Grouping Plan

Source date: `28-05-2026`

Status: implemented

## Goal

Improve how related chunks from the same conversation are surfaced so grouped context is easier to consume.

## Scope

- Add conversation-level scoring.
- Ensure chunks from the same conversation can be grouped or prioritized together.
- Keep this focused on grouping behavior rather than search precision in general.

## Phases

### Phase 1: Scoring model

- Define how conversation-level score should be computed.
- Decide whether it acts as a boost, a grouping signal, or both.
- Keep the per-chunk score visible so the behavior stays explainable.

Acceptance criteria:

- The grouping/scoring rule is clearly defined.
- Related chunks can be identified as belonging together.
- The score model does not obscure the base retrieval signal.

### Phase 2: Group-aware ranking

- Apply conversation-level scoring after the base retrieval step.
- Prefer sets of chunks that form a coherent conversation when appropriate.
- Keep unrelated but individually strong chunks from being incorrectly merged.

Acceptance criteria:

- Chunks from the same conversation are surfaced together when relevant.
- Strong unrelated matches are not accidentally suppressed.
- Ranking remains stable and understandable.

### Phase 3: Output validation

- Ensure grouped results remain compatible with `memory_ask`.
- Confirm citations and summaries still point to the correct underlying chunks.
- Keep the grouping behavior optional or conservative if needed.

Acceptance criteria:

- `memory_ask` can consume grouped context without breaking output shape.
- Citations remain accurate at the chunk level.
- Grouping improves readability without damaging precision.

## Testing

- Unit tests for conversation score calculation.
- Retrieval tests for grouped results from the same conversation.
- Regression tests to ensure unrelated results are not over-grouped.

## Done When

- Related chunks are surfaced together more consistently.
- The grouped view improves readability of search/ask output.
- Existing result accuracy is preserved.
