# Top Priority: Improve MCP Result Shape for `memory_ask`

Source date: `28-05-2026`

Status: implemented

## Goal

Make `memory_ask` easier to consume programmatically by surfacing the useful matches in `results` while keeping `answer` and `citations` for human-readable output and provenance.

## Why This Matters

- `answer` is useful, but not structured.
- `results` is currently empty even when the tool clearly found relevant content.
- A client that only reads `results` can misinterpret a successful search as a miss.

## Scope

- Put the top matches into `results`.
- Keep `answer` as a summary response.
- Keep `citations` for provenance.
- Preserve the current success envelope and existing clients as much as possible.

## Phases

### Phase 1: Contract review and output mapping

- Identify the current `memory_ask` response fields and how they are populated.
- Decide which retrieved items should be promoted into `results`.
- Keep `answer` generation unchanged unless it depends on the old empty-results behavior.

Acceptance criteria:

- The intended `results` payload shape is defined.
- The relationship between `results`, `answer`, and `citations` is explicit.
- Existing consumers are not broken by accidental schema removal.

### Phase 2: Implement result promotion

- Populate `results` with the top matching memory hits.
- Keep `citations` aligned with the included matches.
- Ensure `answer` remains a concise synthesized response.

Acceptance criteria:

- `memory_ask` returns non-empty `results` when relevant matches exist.
- The top matches in `results` correspond to the evidence used for `answer`.
- `citations` still provide provenance for the selected matches.

### Phase 3: Compatibility and edge cases

- Keep empty-result behavior graceful.
- Keep error handling consistent with existing MCP conventions.
- Verify the payload remains stable for downstream consumers that rely on `status` and `answer`.

Acceptance criteria:

- No regression for no-hit queries.
- No regression for invalid-input handling.
- Existing MCP tool tests continue to pass or are updated intentionally.

## Testing

- Add or update unit tests for `memory_ask` result-shape behavior.
- Verify a successful query exposes meaningful structured results.
- Verify an empty query or no-hit query still returns a valid envelope.

## Done When

- `results` contains the useful matches.
- `answer` stays usable as a summary.
- `citations` remain present for provenance.
- The output is reliable for Codex and other MCP clients.
