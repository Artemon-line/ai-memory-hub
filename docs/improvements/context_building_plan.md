# Context Building Plan

Source date: `28-05-2026`

## Goal

Make `memory_ask` return concise, high-signal answers by building the response within a token budget.

## Scope

- Add token-budgeted context building.
- Keep the current retrieval pipeline intact unless it needs a small hook for budgeting.
- Avoid bloating `answer` with low-value content when the query returns many candidates.

## Phases

### Phase 1: Budget definition

- Define the token budget source and default.
- Decide whether the budget is query-level, response-level, or both.
- Keep the default behavior unchanged when budgeting is not enabled.

Acceptance criteria:

- A clear token budget exists for `memory_ask`.
- Existing behavior remains unchanged when the budget is not applied.
- Budget defaults are documented.

### Phase 2: Budget-aware context selection

- Select retrieved chunks until the budget is reached.
- Skip or truncate overflow content deterministically.
- Preserve the highest-signal chunks first.

Acceptance criteria:

- The selected context stays within budget.
- Higher-ranked chunks are preferred over lower-ranked ones.
- Overflow handling is deterministic.

### Phase 3: Response shaping

- Use the budgeted context to produce a cleaner answer.
- Keep citations limited to what was actually included.
- Make sure the final answer remains concise and readable.

Acceptance criteria:

- `memory_ask` returns a concise answer under the budget.
- Citations reflect only included context.
- The response is still useful when the result set is small.

### Phase 4: Hardening

- Validate behavior when results are sparse or empty.
- Confirm the answer generation does not fail when the budget is tight.
- Check that the budgeted flow remains compatible with existing clients.

Acceptance criteria:

- No failures on empty or near-empty retrieval results.
- Existing API/MCP contracts remain stable.
- The budgeted output is predictable in tests.

## Testing

- Unit tests for budget selection and overflow handling.
- API/MCP tests for backward compatibility.
- Regression tests for empty-result and low-budget cases.

## Done When

- `memory_ask` returns concise answers within a defined token budget.
- Citations remain accurate.
- Existing clients continue to work without changes.
