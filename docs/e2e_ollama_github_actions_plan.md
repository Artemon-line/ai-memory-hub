# E2E Testing Plan: Ollama in GitHub Actions

Plan for adding end-to-end tests that validate a realistic memory flow:

1. generate/use a simple conversation
2. save conversation to `ai-memory-hub`
3. call `ask`
4. assert answer/citations/topic context
5. assert persisted DB state

## Goals

- Validate full system behavior across API + storage layers.
- Validate MCP client-path behavior (JSON-RPC session + tool calls), not only REST endpoints.
- Catch regressions not visible in unit tests (routing, serialization, persistence).
- Keep tests deterministic and CI-friendly.

## Non-Goals

- Benchmarking model quality.
- Large multi-turn evaluation datasets.
- Perfect natural-language answer matching.

## Test Scenario (MVP)

Use a fixed conversation fixture with explicit topic signals:

- user: "I am building MCP memory search with FastAPI and SQLite."
- assistant: "Great, we will store and retrieve memory with MCP tools."
- user: "Please remember topic: mcp, backend, sql."

Expected outcomes:

- ingestion succeeds
- topic enrichment includes `mcp`, `backend`, `sql`
- `ask` returns grounded response and citations
- metadata row exists in SQLite
- vector rows exist for same memory id
- MCP session and tool-call flow succeeds end-to-end

## Architecture in CI

Job services/processes:

- Ollama service (local model runtime)
- `ai-memory-hub` API process
- MCP client harness process (JSON-RPC driver script or pytest helper)
- test runner process (`pytest`)

Recommended model for CI stability:

- small instruction model, pinned tag (example: `llama3.2:1b`)
- pin exact model string to avoid drift

## Implementation Steps

## 1) Add E2E test modules (REST + MCP lanes)

Create `tests/e2e/test_ollama_memory_flow.py`:

- start with temp `data_dir`
- boot app via `TestClient` or `uvicorn` subprocess
- POST `/memory/insert` with fixed conversation payload
- POST `/memory/ask` with specific question
- assert:
  - `status == "ok"`
  - `citations` non-empty
  - citation IDs match inserted conversation id
  - response text references expected context keywords
- read SQLite file and assert record exists
- query vector store and assert at least one chunk row

Note:

- keep assertions semantic but deterministic (keyword contains), not exact sentence match.

Create `tests/e2e/test_ollama_mcp_client_flow.py` (required lane):

- initialize MCP session via `/mcp/` with:
  - `Accept: application/json, text/event-stream`
  - capture `Mcp-Session-Id`
- call `tools/list` and assert required tools present:
  - `memory_insert`
  - `memory_ask`
- call `tools/call(memory_insert)` with fixed conversation payload
- call `tools/call(memory_ask)` with fixed question
- assert:
  - tool responses have `status == "ok"`
  - `answer` exists
  - `citations` non-empty
  - citations reference inserted conversation id
- assert DB/vector state as in REST lane

## 2) Add test fixtures/utilities

Add helpers (for example in `tests/e2e/conftest.py`):

- temporary config writer (`data_dir`, providers, interfaces)
- deterministic conversation factory
- polling helper for service readiness if using subprocess mode

## 3) Add DB assertions

Metadata DB checks:

- open `metadata.sqlite3`
- assert row count increased
- assert inserted `id`, `source`, `timestamp` present
- assert `metadata.topics` contains expected topics

Vector DB checks:

- if using LanceDB: assert table exists and rows for memory id > 0
- if fallback in-memory mode: assert through API-level observable checks

## 4) Wire Ollama provider mode

Config for E2E matrix should include:

- embeddings provider path used in test (`local` initially, Ollama inference optional)
- optional inference provider switch when LLM-backed ask is added

Two-phase rollout:

1. Phase A: run E2E with current deterministic `ask` + local embeddings (no model dependency)
2. Phase B: run E2E with Ollama-backed ask/inference (once provider exists)

Client requirement:

- The MCP lane is required in CI from Phase A onward.
- Ollama-backed client lane becomes required once inference provider is available.

## 4.1) Ollama-backed client harness (Codex/OpenCode-style)

Goal:

- emulate an MCP-capable coding agent that uses Ollama as model backend and `ai-memory-hub` as MCP server.

Approach:

- required CI harness: lightweight scripted MCP client (deterministic, headless).
- optional compatibility jobs: Codex/OpenCode smoke flows when reliable headless automation is available.

Required harness behavior:

- sends MCP JSON-RPC requests exactly as a client would.
- uses the same tool surface clients use (`tools/list`, `tools/call`).
- preserves session lifecycle and headers.

## 5) GitHub Actions workflow

Create workflow file: `.github/workflows/e2e-ollama.yml`

Main steps:

1. checkout
2. setup Python + cache dependencies
3. install project (`uv sync --dev`)
4. install/run Ollama
5. pull pinned model
6. run required MCP E2E lane (`pytest -m e2e_mcp`)
7. run REST E2E lane (`pytest -m e2e_rest`)
8. upload logs/artifacts on failure

Suggested trigger:

- `pull_request`
- `push` to `main`
- manual `workflow_dispatch`

## 6) Reliability controls

- add explicit startup timeout for Ollama/model pull
- retry health checks before tests
- isolate E2E tests with marker: `@pytest.mark.e2e`
- run E2E in separate job from fast unit tests
- quarantine flaky assertions (never assert full free-form answer equality)

## 7) Test markers and selection

In `pyproject.toml`/pytest config:

- register marker `e2e`
- register marker `e2e_mcp`
- register marker `e2e_rest`
- default CI unit job excludes `-m e2e`
- E2E workflow runs MCP lane as required gate

## 8) Artifacts for debugging

On failure, upload:

- API logs
- Ollama logs
- `data/metadata.sqlite3`
- any LanceDB directory snapshot
- pytest junit xml

## Assertions Checklist

Minimum required in the E2E test:

- Insert API returns `status=ok`.
- Ask API returns `status=ok` and non-empty `citations`.
- At least one citation references inserted memory id.
- Stored metadata contains expected topics (`mcp`, `backend`, `sql`).
- Metadata SQLite contains inserted conversation row.
- Vector storage contains rows for inserted memory id.

Minimum required in MCP client-path E2E:

- `initialize` succeeds and returns valid session id.
- `tools/list` includes `memory_insert` and `memory_ask`.
- `tools/call(memory_insert)` returns `status=ok`.
- `tools/call(memory_ask)` returns `status=ok` with non-empty `citations`.
- citations reference the inserted conversation id.
- persisted DB/vector assertions pass.

## Acceptance Criteria

- E2E workflow passes consistently on 3 consecutive CI runs.
- Failure output is actionable via uploaded artifacts.
- Runtime stays within acceptable CI budget (target < 10 minutes/job).
- No impact on existing unit/integration workflow duration.
- MCP client-path lane is mandatory and green on PRs.

## Rollout Plan

1. Add E2E scaffolding with required MCP client-path lane + deterministic ask.
2. Introduce Ollama service job and model pull in Actions.
3. Keep REST lane as supporting signal, MCP lane as required gate.
4. Enable Ollama-backed inference lane behind feature flag/env var.
5. Make Ollama-backed lane required after 1 week of stable runs.
