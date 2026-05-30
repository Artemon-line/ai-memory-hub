# MCP Native Integration Plan

<details>
<summary><strong>mcp_plan</strong></summary>

## Goal

Make `ai-memory-hub` feel native to MCP clients (including Codex) by tightening the current MCP contract, fixing the insert ID flow, and improving discoverability and reliability without breaking existing clients.

## Scope

- Keep the current MCP tool set working:
  - `memory_validate`
  - `memory_insert`
  - `memory_search`
  - `memory_retrieve`
  - `memory_ask`
- Keep current resources and prompts working:
  - `memory://conversation/example`
  - `memory://conversation/{id}`
  - `memory://search/{query}`
  - `memory://timeline/{day}`
  - `save_conversation`
  - `search_memory`
  - `ask_memory`
  - `summarize_conversation`
- Tighten tool I/O schemas and structured outputs.
- Fix the insert ID contract so Codex can omit `memory_id` when the backend generates IDs.
- Add protocol and regression coverage for the current MCP surface.

## Non-Goals

- Replacing the existing FastAPI REST endpoints.
- Removing current MCP tools or changing their names.
- Introducing a new UI or new ingestion source in this doc.

## Workstreams

### 1) Insert ID Contract

_Status: implemented_

- Make the insert path tolerant of omitted `id`/`memory_id` when the backend is allowed to assign the identifier.
- Preserve UUID validation when an ID is provided explicitly.
- Make the returned inserted ID the canonical ID used by metadata and vector storage.
- Update prompt text and docs so Codex does not fabricate an ID.

Current code to align with:
- `memory.ingestion.mvp_ingestion.normalize_conversation_json()` already assigns a UUID when `id` is absent.
- `memory.backend.metadata_store.SQLiteMetadataStore` and `PostgresMetadataStore` still require valid UUIDs on write.
- MCP `memory_insert` currently normalizes then validates before insert.

Acceptance criteria:
- Codex can call `memory_insert` without supplying an ID and still succeed.
- Explicit non-UUID IDs still fail fast with a clear validation error.
- Retrieval/search continue to return the same canonical ID that was stored.

### 2) MCP Tool Contract

_Status: implemented_

- Keep existing tool names for compatibility.
- Keep the current structured envelope:
  - `status`, `id`, `results`, `cursor`, `error_code`, `error_message`
- Keep `memory_validate` as the preflight path for malformed conversations.
- Keep `memory_search` pagination/filtering semantics:
  - `limit`, `cursor`, `source`, `date_from`, `date_to`, `tags`

Acceptance criteria:
- Tool responses preserve the envelope shape already used in tests.
- Validation and runtime errors remain machine-readable and consistent.

### 3) Resources and Prompts

_Status: implemented_

- Keep the current resource set and prompt set stable.
- Verify prompt content still guides MCP clients through `memory_validate` before `memory_insert`.
- Keep search and timeline resources returning predictable metadata and filtered result sets.

Acceptance criteria:
- `resources/list`, `resources/read`, `prompts/list`, and prompt invocation continue to work as implemented.
- Prompt text does not regress into direct REST or config-file workflows.

### 4) Protocol Reliability

_Status: implemented_

- Preserve strict MCP JSON-RPC behavior on the streamable HTTP endpoint.
- Keep required headers and session handling documented:
  - `Accept: application/json, text/event-stream`
  - `Mcp-Session-Id` after `initialize`
- Keep transport error handling actionable for invalid envelopes and session misuse.

Acceptance criteria:
- Initialize, tools, resources, and prompts work end-to-end over the MCP transport.
- Common protocol errors remain deterministic and test-covered.

### 5) Test and Verification

_Status: implemented_

- Extend existing tests around the insert-ID flow first.
- Keep MCP tool/resource/prompt tests aligned with the actual server surface.
- Add regression coverage for:
  - omitted ID on insert
  - explicit invalid UUID
  - insert result ID round-tripping through retrieve/search

Acceptance criteria:
- CI covers the UUID-less insert path as the primary regression target.
- The docs and tests agree with the implementation.

## Suggested File-Level Changes

- `memory/interfaces/mcp_server.py`
  - keep tool envelopes and update prompt text for UUID-less inserts
  - align resource/prompt docs with current behavior
- `memory/ingestion/mvp_ingestion.py`
  - make insert ID generation and normalization explicit in code paths
- `memory/backend/metadata_store.py`
  - clarify UUID validation boundaries and error messages
- `memory/backend/postgres_metadata_store.py`
  - keep behavior consistent with SQLite metadata storage
- `tests/unit/test_mcp_tools.py`
  - add omitted-ID and explicit-invalid-ID coverage
- `tests/integration/test_storage_features.py`
  - keep UUID validation coverage for storage adapters
- `README.md`
  - document that Codex should omit the ID by default when backend-generated IDs are enabled

## Delivery Plan (Phased)

1. Phase 1: Fix insert ID contract and add regression coverage
2. Phase 2: Align docs and prompt text with the implemented MCP surface
3. Phase 3: Tighten protocol/docs where tests reveal drift
4. Phase 4: Only add new MCP resources/prompts if the current surface needs them

## Rollout/Risk Controls

- Keep current tool names and payload compatibility.
- Prefer additive changes and doc updates over breaking schema changes.
- Gate insert-ID changes with targeted tests before release.
- Treat Codex insert behavior as the highest-priority regression surface.

</details>
