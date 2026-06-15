# MCP Utility Compliance Plan

## Goal

Add and maintain MCP server protocol features that improve client compatibility
without distracting from the core memory workflow. MCP is the primary agent
integration boundary, so protocol compliance is a P0 quality bar.

This plan covers:

- Pagination:
  https://modelcontextprotocol.io/specification/2025-11-25/server/utilities/pagination
- Logging:
  https://modelcontextprotocol.io/specification/2025-11-25/server/utilities/logging
- Completion:
  https://modelcontextprotocol.io/specification/2025-11-25/server/utilities/completion

## Recommendation

Implement these utilities in this order:

1. Pagination.
2. Sanitized MCP logging.
3. Completion only when a client workflow needs interactive argument
   suggestions.

Pagination is the only immediate implementation priority. Logging should follow
the existing observability plan and must be conservative. Completion is useful
polish for prompt and resource-template argument UX, but it is not required for
reliable memory ingest, search, retrieve, or ask flows.

## Scope

- Preserve the existing MCP tool names and response envelopes.
- Preserve HTTP API behavior unless shared implementation helpers make a small
  internal refactor useful.
- Keep utilities additive and backward compatible.
- Treat MCP utility support as protocol surface, not product-specific memory
  behavior.

## Non-Goals

- Do not change `memory_insert`, `memory_search`, `memory_retrieve`, or
  `memory_ask` semantics as part of this work.
- Do not expose raw conversation text, full queries, embeddings, API keys, DSNs,
  or provider secrets through MCP log notifications.
- Do not implement broad fuzzy completion across all stored memory until there is
  a concrete client UX that benefits from it.

## Phase 1: Pagination

Status: Implemented.

Priority: P0.

MCP list operations that should support cursor pagination:

- [x] `tools/list`
- [x] `prompts/list`
- [x] `resources/list`
- [x] `resources/templates/list`, if resource templates are exposed by the
      server stack

Implementation requirements:

- [x] Accept an opaque `cursor` parameter where the MCP server framework exposes
      list-operation params.
- [x] Return `nextCursor` only when another page exists.
- [x] Keep page size server-controlled through `mcp.list_page_size`.
- [x] Treat cursors as opaque tokens; clients must not need to parse them.
- [x] Return standard JSON-RPC invalid params errors for malformed or expired
      cursors.
- [x] Keep list ordering stable across a paginated sequence.

Suggested cursor design:

- Encode cursor payloads as versioned, signed or HMAC-protected opaque strings if
  they can cross trust boundaries.
- Include only non-sensitive state such as utility name, offset or last item key,
  and cursor version.
- Do not include memory contents, queries, credentials, paths, hashes, or backend
  internals.

Acceptance criteria:

- [x] MCP clients can list tools, prompts, and resources without assuming a fixed
      page size.
- [x] Missing `nextCursor` means the list is complete.
- [x] Invalid cursors fail with a stable protocol error.
- [x] Existing unpaginated clients continue to work.

Tests:

- [x] Unit tests for cursor encode/decode and invalid cursor handling.
- [x] MCP transport tests for first page, follow-up page, end-of-list, and invalid
      cursor.
- [x] Regression test proving list ordering remains stable during pagination.

Implementation notes:

- FastMCP 3.2.4 provides MCP list pagination when `list_page_size` is set.
- ai-memory-hub now passes `mcp.list_page_size` to FastMCP with a default of
  `100`, which keeps current unpaginated clients working for the present tool,
  prompt, and resource counts while preserving cursor support.
- Transport tests force smaller page sizes to verify `nextCursor`, follow-up
  pages, resource-template pagination, and invalid cursor handling.

## Phase 2: Sanitized MCP Logging

Status: Implemented for `logging/setLevel` and sanitized tool notifications.

Priority: P0 after pagination, or alongside observability work.

MCP logging support should include:

- [x] Declare the MCP `logging` capability only when log notifications are
      implemented.
- [x] Implement `logging/setLevel`.
- [x] Emit `notifications/message` for safe operational events.
- [x] Respect minimum log level per client session where the framework exposes
      session state.
- [ ] Rate limit log notifications.
- [x] Reuse the repository's secret redaction behavior.

Safe event examples:

- Server started with MCP/API enabled.
- Storage backend selected.
- Vector backend selected.
- Tool started/completed with operation name and duration bucket.
- Tool failed with stable error code and exception type.
- Fallback mode enabled or disabled.

Unsafe event data:

- Full MCP tool arguments.
- Raw conversation messages.
- Raw user queries or answers.
- Embeddings.
- API keys, bearer tokens, DSNs, passwords, or provider headers.
- Full stack traces sent to clients.
- Internal file paths unless explicitly needed for local diagnostics and already
  covered by debug configuration.

Acceptance criteria:

- [x] A client can call `logging/setLevel` with valid MCP log levels.
- [x] Invalid levels return standard invalid params errors.
- [x] Log notifications include `level`, optional `logger`, and redacted
      JSON-serializable `data`.
- [x] Log output never includes secrets or raw memory payloads in default mode.
- [x] Existing application stdout logging remains available independently of MCP
      client notifications.

Tests:

- [x] Unit tests for level validation and filtering.
- [x] Unit tests for redaction before MCP notification emission.
- [x] MCP transport test for `logging/setLevel`.
- [x] Failure-path test proving an MCP tool failure emits only safe structured
      fields.

Implementation notes:

- FastMCP provides `logging/setLevel` and per-session level filtering.
- ai-memory-hub emits only safe tool completion/failure fields:
  `tool`, `status`, and optional stable `error_code`.
- Tool arguments, raw queries, raw conversation messages, embeddings, returned
  memory, stack traces, credentials, DSNs, and hashes are not included in MCP log
  notification data.
- Rate limiting remains a follow-up with the broader observability plan because
  current notification volume is one completion/failure event per selected tool
  path.

## Phase 3: Completion

Status: Deferred.

Priority: P3 unless a real client workflow needs it sooner.

Completion is for prompt and resource-template argument suggestions. It should be
implemented only after at least one concrete UX needs suggestions for fields such
as:

- prompt arguments
- resource template URI arguments
- source names
- tags
- fact types
- profile fields
- known project names

Implementation requirements when this is pulled forward:

- [ ] Declare the MCP `completions` capability only after
      `completion/complete` works.
- [ ] Support `ref/prompt` completions for known prompt argument names.
- [ ] Support `ref/resource` completions only for exposed resource templates.
- [ ] Return at most 100 suggestions.
- [ ] Sort suggestions by relevance.
- [ ] Validate reference type, reference identity, argument name, and argument
      value.
- [ ] Rate limit completion requests.
- [ ] Avoid information disclosure from private memory.

Initial useful completions:

- Prompt argument names and constrained enum-like values.
- Existing tags and sources, subject to auth and redaction policy.
- Project names or profile fields only when they are already safe to expose to
  the requesting client.

Acceptance criteria:

- [ ] Unknown prompt/resource references return invalid params errors.
- [ ] Suggestions are bounded, deterministic where practical, and sanitized.
- [ ] Completion does not become an unaudited memory enumeration endpoint.

Tests:

- [ ] Unit tests for prompt completion.
- [ ] Unit tests for resource-template completion if templates are exposed.
- [ ] Security regression tests for unauthorized or sensitive suggestions.

## Rollout

Recommended rollout:

1. Add tests that describe existing list behavior.
2. Add pagination helpers and wire MCP list pagination.
3. Add sanitized logging after the observability logging primitives exist.
4. Revisit completion after at least one MCP client exposes a useful argument
   suggestion workflow.

## Done When

- [x] Pagination works for MCP list operations without breaking current clients.
- [x] MCP logging can be enabled safely and respects client log levels.
- [x] Completion remains explicitly deferred or is implemented behind clear
      security and UX constraints.
