# MCP Native Integration Plan

<details>
<summary><strong>mcp_plan</strong></summary>

## Goal

Make `ai-memory-hub` feel native to MCP clients (including Codex) by exposing first-class MCP resources/prompts, tightening schemas, and improving discoverability and reliability.

## Scope

- Keep existing tools (`memory_insert`, `memory_search`, `memory_retrieve`) working.
- Add MCP resources and resource templates for memory browsing/reading.
- Add MCP prompts for common user flows.
- Improve tool I/O schemas and structured outputs.
- Add MCP protocol compliance and regression tests.

## Non-Goals

- Replacing current FastAPI REST endpoints.
- Breaking changes to existing external REST consumers.

## Workstreams

### 1) Resource Model

- Define URI scheme:
  - `memory://conversation/{id}`
  - `memory://search/{query}?top_k=...&source=...`
  - `memory://timeline/{yyyy-mm-dd}`
- Implement `resources/list` with pagination and basic filters.
- Implement `resources/read` for conversation and search URIs.
- Add stable metadata fields (`id`, `source`, `timestamp`, `tags`, `updated_at`).

Acceptance criteria:
- Client can list and read at least one conversation resource.
- Resource payloads are stable and machine-readable.

### 2) Resource Templates

- Add templates for common patterns:
  - Conversation by ID
  - Search by query/top_k
  - Date/source filtered search
- Include clear parameter schema and examples.

Acceptance criteria:
- `list_mcp_resource_templates` returns non-empty templates.
- Template expansion works for valid parameters and returns predictable errors for invalid inputs.

### 3) Prompt Catalog

- Add MCP prompts:
  - `save_conversation`
  - `search_memory`
  - `summarize_conversation`
- Each prompt should produce deterministic tool-call-oriented content.

Acceptance criteria:
- `prompts/list` advertises prompt names and argument schemas.
- Each prompt can be invoked with minimal required args.

### 4) Tool Contract Hardening

- Keep existing tool names for compatibility.
- Tighten input schemas:
  - explicit required fields
  - enums/defaults where appropriate
  - validation error codes/messages
- Standardize output envelope in `structuredContent`:
  - `status`, `id`, `results`, `cursor`, `error_code`, `error_message`

Acceptance criteria:
- Tool responses always include valid `structuredContent`.
- Invalid inputs return consistent error shape.

### 5) Search Ergonomics

- Extend `memory_search` args:
  - `limit`, `cursor`, `source`, `date_from`, `date_to`, `tags`
- Add deterministic sort semantics and cursor pagination.

Acceptance criteria:
- Repeated paginated calls return complete, non-overlapping result sets.
- Filters behave consistently and are covered by tests.

### 6) Protocol and Transport Reliability

- Preserve strict MCP JSON-RPC behavior.
- Document required headers and session handling for streamable HTTP:
  - `Accept: application/json, text/event-stream`
  - `Mcp-Session-Id` after `initialize`
- Improve server-side error text for common misuses (missing headers, invalid method, session not found).

Acceptance criteria:
- Misformatted requests return actionable errors.
- Session lifecycle is stable across initialize/list/call/read flows.

### 7) Test and Verification

- Add MCP E2E tests covering:
  - `initialize`
  - `tools/list`
  - `tools/call` (`memory_insert/search/retrieve`)
  - `resources/list`
  - `resources/read`
  - `prompts/list` and prompt invocation
- Add negative tests:
  - missing Accept header
  - invalid JSON-RPC envelope
  - invalid session id
  - schema validation failures

Acceptance criteria:
- CI runs MCP test suite with deterministic pass/fail.
- Regressions in protocol compatibility are caught before merge.

## Suggested File-Level Changes

- `memory/interfaces/mcp_server.py`
  - add resources/templates/prompts registration
  - centralize schema/response helpers
- `memory/api/server.py`
  - optional improvements for MCP endpoint diagnostics/logging
- `tests/test_mcp_tools.py`
  - keep backward compatibility checks
- `tests/test_mcp_protocol.py` (new)
  - JSON-RPC/session/headers/protocol behavior
- `tests/test_mcp_resources.py` (new)
  - list/read/template coverage
- `tests/test_mcp_prompts.py` (new)
  - prompt listing and invocation
- `README.md`
  - add MCP-native usage section (JSON-RPC examples + resource/template/prompt discovery)

## Delivery Plan (Phased)

1. Phase 1: Contract hardening + test baseline (Implemented on May 17, 2026)
2. Phase 2: Resources + templates (Implemented on May 17, 2026)
3. Phase 3: Prompts + search pagination/filtering (Implemented on May 17, 2026)
4. Phase 4: Docs polish + CI reliability gates (Implemented on May 17, 2026)

## Rollout/Risk Controls

- Keep current tool names and payload compatibility.
- Use additive changes first; defer removals/deprecations.
- Gate non-trivial behavior with targeted tests before release.
- Add changelog notes for client-facing MCP contract updates.

</details>
