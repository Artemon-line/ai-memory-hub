# Context Building Plan

Source date: `28-05-2026`

## Goal

Make `memory_ask` return concise, high-signal answers by building the response within a token budget.

## Scope

- [x] Add token-budgeted context building.
- [x] Keep the current retrieval pipeline intact unless it needs a small hook for budgeting.
- [x] Avoid bloating `answer` with low-value content when the query returns many candidates.

## Phases

### Phase 1: Budget definition

- [x] Define the token budget source and default.
- [x] Decide whether the budget is query-level, response-level, or both.
- [x] Keep the default behavior unchanged when budgeting is not enabled.

Acceptance criteria:

- [x] A clear token budget exists for `memory_ask`.
- [x] Existing behavior remains unchanged when the budget is not applied.
- [x] Budget defaults are documented.

### Phase 2: Budget-aware context selection

- [x] Select retrieved chunks until the budget is reached.
- [x] Skip or truncate overflow content deterministically.
- [x] Preserve the highest-signal chunks first.

Acceptance criteria:

- [x] The selected context stays within budget.
- [x] Higher-ranked chunks are preferred over lower-ranked ones.
- [x] Overflow handling is deterministic.

### Phase 3: Response shaping

- [x] Use the budgeted context to produce a cleaner answer.
- [x] Keep citations limited to what was actually included.
- [x] Make sure the final answer remains concise and readable.

Acceptance criteria:

- [x] `memory_ask` returns a concise answer under the budget.
- [x] Citations reflect only included context.
- [x] The response is still useful when the result set is small.

### Phase 4: Hardening

- [x] Validate behavior when results are sparse or empty.
- [x] Confirm the answer generation does not fail when the budget is tight.
- [x] Check that the budgeted flow remains compatible with existing clients.

Acceptance criteria:

- [x] No failures on empty or near-empty retrieval results.
- [x] Existing API/MCP contracts remain stable.
- [x] The budgeted output is predictable in tests.

## Testing

- [x] Unit tests for budget selection and overflow handling.
- [x] API/MCP tests for backward compatibility.
- [x] Regression tests for empty-result and low-budget cases.

## Done When

- [x] `memory_ask` returns concise answers within a defined token budget.
- [x] Citations remain accurate.
- [x] Existing clients continue to work without changes.
