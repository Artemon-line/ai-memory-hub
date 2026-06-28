# Token Budgeting Plan (`tiktoken`)

Implementation plan for adding token-aware context budgeting and optional token-aware chunking to `ai-memory-hub`.

## Goals

- Improve `ask` reliability by preventing oversized context payloads.
- Improve retrieval quality by preferring high-signal chunks within a token budget.
- Keep backward compatibility for existing API/MCP clients.

## Non-Goals

- Replacing current search/ranking logic.
- Making `tiktoken` a hard runtime dependency for all modes.
- Adding an ai-memory-hub-specific downloader for tokenizer encoding files.
- Changing external contracts in a breaking way.

## Current Status

Implemented:

- [x] Config defaults for `tokenizer.enabled`, `tokenizer.encoding`, and `ask.max_context_tokens`.
- [x] Lazy tokenizer adapter in `memory/ingestion/tokenizer.py`.
- [x] Deterministic fallback tokenizer when `tiktoken` is missing or an encoding cannot load.
- [x] Token-budgeted `ask` context selection in `memory/ingestion/mvp_ingestion.py`.
- [x] Optional diagnostics for budgeted ask responses: `context_tokens_used`, `chunks_selected`, `chunks_dropped`, and `tokenizer_used`.
- [x] Optional `max_context_tokens` on HTTP `POST /memory/ask`.
- [x] Optional `max_context_tokens` on MCP `memory_ask`.
- [x] Config defaults for `chunking.strategy`, `chunking.max_tokens`, and `chunking.overlap_tokens`.
- [x] Opt-in token-window ingestion chunking with overlap.
- [x] SQLite chunk-state tracking for token chunk manifests through `metadata.index_chunks`.
- [x] README and core docs updated for token-budgeted ask and token-aware chunking.
- [x] Unit, API, MCP, integration, and regression tests for implemented behavior.
- [x] Dedicated fixed-corpus performance benchmark for ask latency and selected-context size distribution.
- [x] Dedicated default-mode slowdown benchmark for `tokenizer.enabled=false`.
- [x] Decision recorded: `cl100k_base` is an encoding name, not a model path; rely on `tiktoken` cache/prewarm behavior instead of a custom downloader.
- [x] Optional `tiktoken` package extra.
- [x] README/config guidance for `TIKTOKEN_CACHE_DIR` and one-time cache prewarm.
- [x] Tokenizer preflight check that reports whether the configured encoding resolves through `tiktoken` or the heuristic fallback.

Remaining work:

- None.

## Phase 1: `ask` Token Budgeting

### 1) Config and defaults

- [x] Add `tokenizer.enabled: false`.
- [x] Add `tokenizer.encoding: cl100k_base`.
- [x] Add `ask.max_context_tokens: 2000`.
- [x] Keep budgeting inactive by default.
- [x] Preserve existing request behavior when no new fields are sent.
- [x] Allow request-level `max_context_tokens` to activate budgeting even when `tokenizer.enabled` is `false`.

Implemented details:

- Config models are in `memory/config.py`.
- Defaults are present in `config.yaml`.
- Existing unbudgeted ask behavior remains unchanged unless budgeting is enabled by config or request.

### 2) Tokenizer adapter module

- [x] Add `count_tokens(text: str, encoding: str) -> int`.
- [x] Add `truncate_to_tokens(text: str, max_tokens: int, encoding: str) -> str`.
- [x] Add `split_token_windows(text: str, *, max_tokens: int, overlap_tokens: int, encoding: str) -> list[str]`.
- [x] Add `tokenizer_used(encoding: str) -> str`.
- [x] Lazy import `tiktoken`.
- [x] Cache encoding lookup in-process.
- [x] Fall back to a deterministic heuristic if import or encoding load fails.
- [x] Log one warning when the fallback path is used.
- [x] Add optional install extra for precise `tiktoken` token counts.
- [x] Document `TIKTOKEN_CACHE_DIR` for persistent/offline tokenizer cache.
- [x] Document one-time cache prewarm for `tokenizer.encoding`, such as `cl100k_base`.
- [x] Add an optional preflight/diagnostic command for tokenizer availability.

Implemented details:

- Adapter lives at `memory/ingestion/tokenizer.py`.
- `tiktoken` remains optional and is not a hard runtime dependency.
- `cl100k_base` is loaded through `tiktoken.get_encoding("cl100k_base")` when `tiktoken` is installed.
- `tiktoken` handles encoding file download/cache lookup internally; ai-memory-hub should not download tokenizer files itself.
- Offline deployments should provide a persistent `TIKTOKEN_CACHE_DIR` that has been prewarmed during build or setup.

### 3) Token-budgeted context builder in `ask`

- [x] Keep current search ranking.
- [x] Accumulate retrieved chunks until `max_context_tokens` is reached.
- [x] Truncate chunks that exceed the remaining budget.
- [x] Drop chunks that cannot fit.
- [x] Build answers from included chunks only.
- [x] Return citations only for included chunks.
- [x] Return selected `results` only when budgeting is active.
- [x] Return optional diagnostics only when budgeting is active.
- [x] Cover config-enabled budgeting with tests.

Implemented details:

- Implemented in `memory/ingestion/mvp_ingestion.py`.
- Budgeted ask preserves ranked order and uses deterministic overflow handling.
- Diagnostics are `context_tokens_used`, `chunks_selected`, `chunks_dropped`, and `tokenizer_used`.

### 4) API and MCP wiring

- [x] Add optional `max_context_tokens` to `POST /memory/ask`.
- [x] Add optional `max_context_tokens` to MCP tool `memory_ask`.
- [x] Use config default when omitted and tokenizer budgeting is enabled.
- [x] Reject invalid MCP values with the stable `invalid_input` envelope.
- [x] Use existing FastAPI/Pydantic request validation for invalid HTTP request fields.

Implemented details:

- HTTP wiring is in `memory/api/server.py`.
- MCP wiring is in `memory/interfaces/mcp_server.py`.
- Agent interface propagation is in `memory/ingestion/base_agent.py` and `memory/ingestion/mvp_ingestion_agent.py`.

## Phase 2: Optional token-aware ingestion chunking

### 5) Token-based chunk strategy

- [x] Add optional token-window chunker in ingestion.
- [x] Split long message text by token window.
- [x] Support window overlap.
- [x] Preserve `chunk_index`, `role`, `message_hash`, stable `chunk_id`, and chunk text.
- [x] Gate behavior behind `chunking.strategy: message|token`.
- [x] Add `chunking.max_tokens`.
- [x] Add `chunking.overlap_tokens`.
- [x] Validate `chunking.overlap_tokens < chunking.max_tokens`.
- [x] Track token chunk manifests for SQLite chunk index state.

Implemented details:

- Config models and validation are in `memory/config.py`.
- Defaults are present in `config.yaml`:
  - `chunking.strategy: message`
  - `chunking.max_tokens: 800`
  - `chunking.overlap_tokens: 80`
- `chunking.strategy: token` splits each long message into overlapping token windows.
- Token windows use `tiktoken` when available and the deterministic fallback otherwise.
- SQLite metadata indexing uses `metadata.index_chunks` when present so chunk state tracks token windows rather than only message-level chunks.
- Public conversation `messages` remain unchanged; token chunking affects indexing chunks only.

### 6) Compatibility

- [x] Keep default message-level chunking.
- [x] Keep token-based chunking opt-in only.
- [x] Preserve existing API and MCP client contracts.
- [x] Preserve existing ingestion/search/retrieve/ask behavior under the default `message` strategy.
- [x] Continue append-only chunk indexes correctly when token chunking is enabled.

## Testing

### A. Unit tests

- [x] Tokenizer fallback counts and truncates deterministically.
- [x] Tokenizer fallback splits overlapping windows.
- [x] Ask budgeting includes ranked chunks first within budget.
- [x] Ask budgeting truncates or drops overflow chunks predictably.
- [x] Ask budgeting returns citations only for included chunks.
- [x] Config-enabled ask budgeting is covered.
- [x] Token chunking splits long messages with overlap.
- [x] Token chunking append flow continues chunk indexes.
- [x] Config validation covers defaults, invalid strategies, invalid budgets, and invalid overlap.

Implemented details:

- Tokenizer tests are in `tests/unit/test_tokenizer.py`.
- Ask context budgeting tests are in `tests/unit/test_mvp_ask_budget.py`.
- Token chunking and append behavior tests are in `tests/unit/test_mvp_ingestion.py`.
- Config validation tests are in `tests/unit/test_config.py`.

### B. API tests

- [x] `POST /memory/ask` works without `max_context_tokens`.
- [x] `POST /memory/ask` honors provided `max_context_tokens`.
- [x] Response shape remains stable with `status`, `answer`, and `citations`.
- [x] Invalid HTTP request fields use the existing FastAPI/Pydantic validation path.

Implemented details:

- API budgeted ask coverage is in `tests/integration/test_api_endpoints.py`.

### C. MCP tool tests

- [x] `memory_ask` works with existing args: `question`, `top_k`.
- [x] `memory_ask` accepts optional `max_context_tokens`.
- [x] Invalid `max_context_tokens` returns `status=error`.
- [x] Invalid `max_context_tokens` returns `error_code=invalid_input`.
- [x] Invalid `max_context_tokens` returns `error_message`.

Implemented details:

- MCP coverage is in `tests/unit/test_mcp_tools.py`.

### D. Regression/compatibility tests

- [x] Existing ingestion/search/retrieve tests pass unchanged.
- [x] Existing MCP prompt/tool/resource tests pass.
- [x] Deterministic ordering in search and citations is preserved.
- [x] SQLite metadata chunk manifest tracking is covered.
- [x] Full test suite passes after implementation.

Implemented details:

- SQLite metadata chunk manifest coverage is in `tests/integration/test_storage_features.py`.
- Latest full run after these changes: `92 passed, 5 skipped`.

### E. Performance checks

- [x] Compare average `ask` latency before and after on a fixed test corpus.
- [x] Compare selected-context size distribution on a fixed test corpus.
- [x] Verify no significant slowdown in default mode with `tokenizer.enabled=false`.

Additional details:

- Benchmark module is `memory/benchmarks/token_budget.py`.
- Run it with `python -m memory.benchmarks.token_budget --iterations 200`.
- The benchmark uses a fixed in-memory retrieval corpus to avoid storage, embedding, and network variance.
- The JSON report includes default unbudgeted ask latency, request-budgeted ask latency, config-enabled ask latency, selected context size, dropped chunk counts, and token chunking output.
- Optional CLI thresholds `--max-budgeted-ratio` and `--max-config-enabled-ratio` can fail the benchmark when budgeted paths exceed a local slowdown limit.
- Unit coverage for benchmark structure and threshold failures is in `tests/unit/test_token_budget_benchmark.py`.

## Rollout

- [x] PR1-equivalent: tokenizer adapter, ask budgeting, API/MCP optional arg, and tests.
- [x] PR2-equivalent: optional token-based ingestion chunking and tests.
- [x] PR3-equivalent: README, roadmap references, config docs, and plan status updates.
- [x] Optional follow-up: lightweight performance benchmark.
- [x] Follow-up: optional `tiktoken` extra, cache/prewarm docs, and tokenizer preflight diagnostics.

## Acceptance Criteria

- [x] `ask` respects request-level context token budgets.
- [x] `ask` respects config-enabled context token budgets.
- [x] No breaking changes for current API/MCP consumers.
- [x] Full test suite passes, including token-budget and token-chunking tests.
- [x] Clear fallback behavior exists when `tiktoken` is not installed.
- [x] Precise tokenizer setup is documented without adding hidden runtime downloads.
- [x] Offline tokenizer cache setup is documented for `TIKTOKEN_CACHE_DIR`.
- [x] Token chunking is opt-in and default message chunking remains unchanged.
- [x] Dedicated lightweight performance benchmark exists.
