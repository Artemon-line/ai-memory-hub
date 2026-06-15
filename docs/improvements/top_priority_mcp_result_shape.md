# Top Priority: Improve MCP Result Shape for `memory_ask`

Source date: `28-05-2026`

Status: partial

## Goal

Make `memory_ask` easier to consume programmatically by surfacing the useful matches in `results` while keeping `answer` and `citations` for human-readable output and provenance.

## Why This Matters

- `answer` is useful, but not structured.
- `results` is currently empty even when the tool clearly found relevant content.
- A client that only reads `results` can misinterpret a successful search as a miss.
- Real Codex feedback showed the same issue for fact-layer answers: the tool returned a useful answer from normalized facts, but `results: []` made the structured evidence shape surprising.

## Scope

- Put the top matches into `results`.
- Keep `answer` as a summary response.
- Keep `citations` for provenance.
- Preserve the current success envelope and existing clients as much as possible.
- Separate raw evidence, normalized facts, and polished answer text.

## Phases

### Phase 1: Contract review and output mapping

- Identify the current `memory_ask` response fields and how they are populated.
- Decide which retrieved items should be promoted into `results`.
- Decide whether fact-layer evidence belongs in `results`, a dedicated `facts` field, or a general `evidence` field.
- Keep `answer` generation unchanged unless it depends on the old empty-results behavior.

Acceptance criteria:

- The intended `results` payload shape is defined.
- The relationship between `results`, `answer`, and `citations` is explicit.
- The relationship between chunk results and fact evidence is explicit.
- Existing consumers are not broken by accidental schema removal.

### Phase 2: Implement result promotion

- Populate `results` with the top matching memory hits.
- [x] Populate structured fact evidence for fact-layer answers.
- Keep `citations` aligned with the included matches.
- Ensure `answer` remains a concise synthesized response.

Acceptance criteria:

- `memory_ask` returns non-empty `results` when relevant matches exist.
- The top matches in `results` correspond to the evidence used for `answer`.
- [x] Fact-only answers expose the facts used for `answer` even when no chunk-style results are returned.
- `citations` still provide provenance for the selected matches.

### Phase 3: Compatibility and edge cases

- Keep empty-result behavior graceful.
- Keep error handling consistent with existing MCP conventions.
- Verify the payload remains stable for downstream consumers that rely on `status` and `answer`.
- [x] Verify fact-layer, mixed, and conflict answer paths have unsurprising structured fact fields.
- Verify not-found answer paths have unsurprising structured fields.

Acceptance criteria:

- No regression for no-hit queries.
- No regression for invalid-input handling.
- [x] No ambiguity between "no answer found" and "answer found from facts rather than chunks" for fact-backed answers.
- Existing MCP tool tests continue to pass or are updated intentionally.

## Testing

- Add or update unit tests for `memory_ask` result-shape behavior.
- Verify a successful query exposes meaningful structured results.
- [x] Verify a successful fact-layer query exposes meaningful structured fact evidence.
- Verify an empty query or no-hit query still returns a valid envelope.

## Done When

- `results` contains the useful matches.
- [x] Fact-layer evidence is not hidden behind an empty chunk result list.
- `answer` stays usable as a summary.
- `citations` remain present for provenance.
- The output is reliable for Codex and other MCP clients.
