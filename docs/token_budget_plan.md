# Token Budgeting Plan (`tiktoken`)

Implementation plan for adding token-aware context budgeting and optional token-aware chunking to `ai-memory-hub`.

## Goals

- Improve `ask` reliability by preventing oversized context payloads.
- Improve retrieval quality by preferring high-signal chunks within a token budget.
- Keep backward compatibility for existing API/MCP clients.

## Non-Goals

- Replacing current search/ranking logic.
- Making `tiktoken` a hard runtime dependency for all modes.
- Changing external contracts in a breaking way.

## Phase 1: `ask` Token Budgeting (recommended first)

### 1) Config and defaults

Add config keys (safe defaults):

- `tokenizer.enabled: false`
- `tokenizer.encoding: cl100k_base`
- `ask.max_context_tokens: 2000`

Behavior:

- If tokenizer is disabled or unavailable, keep current behavior.
- Existing requests without new fields behave exactly as today.

### 2) Tokenizer adapter module

Create a small wrapper (for example `memory/ingestion/tokenizer.py`) with:

- `count_tokens(text: str, encoding: str) -> int`
- `truncate_to_tokens(text: str, max_tokens: int, encoding: str) -> str`

Requirements:

- Lazy import `tiktoken`.
- If import/encoding fails, fall back to deterministic heuristic (word/char-based) and log warning.

### 3) Token-budgeted context builder in `ask`

Update `memory/ingestion/mvp_ingestion.py`:

- Keep current search ranking.
- Accumulate retrieved chunks until `max_context_tokens` is reached.
- Skip or truncate chunks that exceed remaining budget.
- Build answer from included chunks only.

Response additions (non-breaking):

- `citations` remain as-is (only selected chunks).
- Optional diagnostics fields:
  - `context_tokens_used`
  - `chunks_selected`
  - `chunks_dropped`
  - `tokenizer_used`

### 4) API and MCP wiring

Add optional `max_context_tokens` to:

- `POST /memory/ask`
- MCP tool `memory_ask`

Defaults:

- If omitted, use config default.
- If invalid, return existing-style `invalid_input` errors.

## Phase 2: Optional token-aware ingestion chunking

### 5) Token-based chunk strategy

Add optional chunker in ingestion:

- Split long message text by token window (with overlap).
- Preserve metadata (`chunk_index`, `role`, maybe `token_count`).

Gate behind config:

- `chunking.strategy: message|token`
- `chunking.max_tokens`
- `chunking.overlap_tokens`

### 6) Compatibility

- Default remains current message-level chunking.
- Token-based chunking is opt-in only.

## Testing Guidelines

## A. Unit tests

Tokenizer adapter tests:

- Counts tokens deterministically for fixture strings.
- Truncates text to budget and never exceeds budget.
- Fallback path works when `tiktoken` is unavailable.
- Invalid encoding returns controlled fallback/error behavior.

Context budgeting tests (`ask`):

- Includes highest-ranked chunks first within budget.
- Drops or truncates overflow chunks predictably.
- Returns citations only for included chunks.
- Empty search results return graceful answer with empty citations.

## B. API tests

For `POST /memory/ask`:

- Works without `max_context_tokens` (backward compatible).
- Honors provided `max_context_tokens`.
- Rejects invalid values (0, negative, non-integer).
- Response shape remains stable (`status`, `answer`, `citations`).

## C. MCP tool tests

For `memory_ask`:

- Works with existing args (`question`, `top_k`).
- Accepts optional `max_context_tokens`.
- Invalid inputs return consistent envelope:
  - `status=error`
  - `error_code=invalid_input`
  - `error_message` present.

## D. Regression/compatibility tests

- Existing ingestion/search/retrieve tests must still pass unchanged.
- Existing MCP prompt/tool/resource tests must still pass.
- Deterministic ordering in search and citations is preserved.

## E. Performance checks (lightweight)

On fixed test corpus:

- Compare average `ask` latency before/after.
- Compare selected-context size distribution.
- Ensure no significant slowdown in default mode (`tokenizer.enabled=false`).

## Rollout

1. PR1: tokenizer adapter + ask budgeting + API/MCP optional arg + tests.
2. PR2: optional token-based ingestion chunking + tests.
3. PR3: docs/examples polish (`README`, roadmap references, config docs).

## Acceptance Criteria

- `ask` respects context token budget when enabled.
- No breaking changes for current API/MCP consumers.
- Full test suite passes, including new token-budget tests.
- Clear fallback behavior exists when `tiktoken` is not installed.
