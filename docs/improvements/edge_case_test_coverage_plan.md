# Edge Case Test Coverage Plan

This plan tracks public-contract edge cases that are not fully covered by the
current unit, integration, E2E, and Bruno suites.

## Goals

- Prove API and MCP surfaces interoperate over the same runtime and storage
  boundaries.
- Lock down degraded storage behavior for metadata and vector providers.
- Make auth-mode transitions explicit so local development convenience does not
  accidentally become a data exposure path.
- Add regression tests for error paths that are realistic in Docker, LAN, and
  real-client MCP use.

## Current Coverage Snapshot

Implemented coverage already includes:

- HTTP API insert, search, retrieve, ask, facts, profile, bearer auth, OAuth
  resource-server auth, and shared-project behavior.
- MCP protocol initialize, session handling, tools/list pagination, prompt and
  resource listing, and MCP tool handler behavior.
- MCP client-shaped payload compatibility for Codex, Gemini, Copilot, ChatGPT,
  and opencode-style payloads.
- Storage provider contracts, live provider gates, vector fallback startup
  behavior, metadata startup failure, schema invariants, dimension checks, dry
  run, and secret redaction.
- Bruno black-box API and MCP smoke flows against a running service.

Main gaps are cross-surface workflows, auth-mode transition behavior, mid-run
provider failure behavior, and explicit negative cases for degraded state.

## P0: API And MCP Cross-Surface Workflows

Add tests proving API and MCP are interchangeable clients for the same memory
store.

- [x] API insert, MCP search, MCP retrieve, MCP ask.
- [x] MCP insert, API search, API retrieve, API ask.
- [x] API insert with `project_id`, MCP read with the same `project_id`.
- [x] MCP insert with `project_id`, API read with the same `project_id`.
- [x] API bearer-token insert, MCP bearer-token read under the same owner.
- [x] MCP bearer-token insert, API bearer-token read under the same owner.
- [x] Cross-owner negative checks for both directions: API writes must not leak
  through MCP reads, and MCP writes must not leak through API reads.
- [x] Response-shape equivalence checks for shared fields: `status`, `id`,
  `results`, `memory`, `answer`, `citations`, `confidence`, and
  `answer_basis`.

Implemented in `tests/integration/test_api_mcp_interop.py`. Note: MCP search
exposes `cursor`; HTTP API search currently does not, so the interop test checks
`cursor` on MCP and compares the actual shared public fields across both
surfaces.

Preferred location:

- `tests/integration/test_api_mcp_interop.py`

Use `TestClient` against `create_app(config={"interfaces": {"api": True, "mcp":
True}})` so both surfaces share one agent/runtime.

## P0: Storage Outage And Fallback Policy

Expected policy:

- Metadata provider startup failure is fatal. Do not fallback from Postgres or
  MongoDB metadata to SQLite or in-memory metadata.
- Vector provider startup failure may fallback to in-memory vectors only when
  `storage.vector.allow_fallback: true`.
- Runtime health must report degraded state when vector fallback is active.
- Secrets must never appear in health reasons, logs, API responses, or MCP tool
  errors.

Add or verify tests:

- [ ] Postgres metadata unavailable at startup fails app/runtime creation.
- [ ] Redis vector unavailable at startup with `allow_fallback=false` fails
  app/runtime creation.
- [ ] Redis vector unavailable at startup with `allow_fallback=true` starts in
  degraded mode and reports `requested_vector_provider=redis`,
  `vector_provider=memory`, and `vector_fallback_active=true`.
- [ ] PGVector unavailable at startup follows the same disabled/enabled fallback
  policy.
- [ ] Degraded vector fallback does not claim durable vector persistence.
- [ ] API `/ready` reflects degraded vector state consistently.
- [ ] MCP `memory://health` reflects degraded vector state consistently.
- [ ] Provider credentials are redacted in startup exceptions, health reasons,
  and MCP/API error envelopes.

Preferred locations:

- Startup and provider policy: `tests/integration/test_storage_features.py`
- API/MCP health exposure: `tests/integration/test_api_mcp_interop.py`

Follow-up candidate:

- Add mid-run outage tests with fake providers that fail on `insert` or `search`.
  Startup fallback does not cover this case. Runtime operation failures should
  return deterministic API/MCP errors and should not silently switch providers
  after writes have started.

## P0: Auth-Mode Transition Behavior

Question: if a user writes data while auth is enabled, then the server restarts
with `api.auth: none`, should that data be readable without credentials?

Expected answer: no for user-scoped data. `auth=none` may be used for local
development and unauthenticated local-default data, but it should not become a
credential bypass for rows previously stamped with an authenticated owner or a
non-local project.

The current implementation resolves unauthenticated requests to the local
default project and denies explicit access to non-local projects. Add tests to
lock this down.

Add tests:

- [ ] Bearer-token insert into owner default project, restart/recreate app with
  the same persistent store and `api.auth: none`, unauthenticated API search
  returns no owner-scoped result.
- [ ] Same scenario for unauthenticated API retrieve by ID returns 404.
- [ ] Same scenario for unauthenticated API ask does not use owner-scoped facts
  or conversations.
- [ ] Same scenario through MCP search, retrieve, and ask.
- [ ] Explicit `project_id` for the authenticated owner's default project is
  rejected under `auth=none`.
- [ ] OAuth resource-server insert using `sub=owner-a`, restart/recreate app
  with `auth=none`, unauthenticated API/MCP reads do not return owner-scoped
  data.
- [ ] Authenticated shared-project data is not readable under `auth=none` unless
  the project is the synthetic `local-default` project.
- [ ] Existing unauthenticated local-default data remains readable after restart
  with `auth=none`.

Preferred location:

- `tests/integration/test_auth_mode_transitions.py`

Use SQLite metadata and a temporary vector store path so restart behavior uses
real persistence, not only in-process stubs.

## P1: Payload, Validation, And Injection Edges

Existing tests cover invalid schemas, malformed MCP payloads, invalid IDs, and
some injection-like IDs. Add cross-surface negative cases:

- [ ] API accepts no client-supplied `owner_id`; server stamps authenticated
  owner.
- [ ] MCP accepts no client-supplied `owner_id`; server stamps authenticated
  owner.
- [ ] API and MCP reject invalid `project_id` formats consistently.
- [ ] API and MCP reject unknown `result_mode` consistently.
- [ ] API and MCP cap or reject extreme `top_k`, `limit`, and
  `max_context_tokens` consistently.
- [ ] API and MCP preserve date/tag/thread filters consistently.
- [ ] Tool and API error messages remain actionable but do not include payload
  text, credentials, full queries, or provider URLs with secrets.

## P1: Deduplication And Append Across Surfaces

Add tests for mixed client workflows:

- [ ] API inserts a conversation, MCP retries the same conversation and gets the
  deduplicated result.
- [ ] MCP inserts a conversation, API retries the same conversation and gets the
  deduplicated result.
- [ ] API inserts an initial thread, MCP appends messages using the same upstream
  thread metadata.
- [ ] MCP inserts an initial thread, API appends messages using the same upstream
  thread metadata.
- [ ] Same-id/different-content conflicts return deterministic errors through
  both surfaces.
- [ ] Same conversation hash in different projects is allowed and remains
  isolated.

## P1: Facts And Profile Cross-Surface Behavior

Add tests proving the fact layer obeys the same boundaries:

- [ ] API insert extracts facts, MCP profile/fact search can read them under the
  same owner/project.
- [ ] MCP insert extracts facts, API profile/fact endpoints can read them under
  the same owner/project.
- [ ] Cross-owner fact/profile queries do not leak facts.
- [ ] `auth=none` cannot read owner-scoped facts created under bearer or OAuth
  auth.
- [ ] Supersession through one surface is visible through the other.

## P1: Multilingual And Embedding Model Drift

Expected policy:

- ai-memory-hub should not declare English-only behavior. The hub stores and
  searches Unicode text, and multilingual retrieval should work when the
  configured embedding model supports the languages involved.
- Multilingual quality is an embedding-model capability, not an API/MCP contract
  special case.
- The same configured embedding provider, model, dimension, and embedding
  options must be used for both ingestion and query-time retrieval.
- Changing the embedding model, dimension, provider, or model options for an
  existing vector index requires an explicit reindex or a separate vector
  namespace/index. Do not silently mix vectors from different embedding spaces.

Current guardrails:

- Different dimensions are caught by startup and runtime vector dimensionality
  checks.
- Same-dimension model swaps are not reliably detectable from vector shape alone
  and can silently degrade or corrupt retrieval ranking.

Add tests and implementation checks:

- [ ] English insert, English query works with the configured model.
- [ ] Non-English insert, same-language query works when using the same
  multilingual-capable test embedder.
- [ ] Non-English insert, English query works only as a model-quality smoke test,
  not as a deterministic correctness guarantee.
- [ ] Unicode normalization edge cases do not crash ingestion, hashing, chunking,
  search, ask, facts, or profile extraction.
- [ ] Startup records embedding provider, model, dimension, and relevant options
  as vector-index metadata where the provider supports it.
- [ ] Startup fails or enters an explicit "reindex required" state when existing
  vector-index metadata does not match the configured embedding provider/model,
  even if dimensions match.
- [ ] API and MCP health expose the active embedding provider/model/dimension and
  any embedding-index mismatch state without leaking provider credentials.
- [ ] Reindex command or runbook is documented before allowing a configured model
  swap on persistent vector stores.

Preferred locations:

- Embedding/index compatibility: `tests/integration/test_storage_features.py`
- Public health exposure: `tests/integration/test_api_mcp_interop.py`
- Unicode and cross-language public behavior:
  `tests/integration/test_api_mcp_interop.py`

## P2: Operational And Observability Edges

Add tests or smoke checks for:

- [ ] `/health` and `/ready` remain public and secret-free under every auth mode.
- [ ] MCP protected-resource metadata remains public and secret-free in OAuth
  mode.
- [ ] Request failures emit structured logs without payload, token, query, or DSN
  leakage.
- [ ] Startup logs clearly state fallback policy and dry-run state.
- [ ] Production-oriented config with `allow_fallback=true` emits a warning once
  that fallback may make vector data non-durable.

## Suggested Execution Order

1. Add `test_api_mcp_interop.py` with unauthenticated cross-surface happy paths.
2. Extend it with bearer-token cross-surface isolation.
3. Add `test_auth_mode_transitions.py` using persistent SQLite/LanceDB temp
   storage.
4. Fill Redis and PGVector outage assertions in existing storage tests.
5. Add embedding-index compatibility checks for model/provider/dimension drift.
6. Add cross-surface fact/profile and dedupe/append regressions.
7. Add mid-run fake-provider failure tests after startup behavior is locked down.

## Acceptance Criteria

- Every public memory operation is covered in both directions: API -> MCP and
  MCP -> API.
- Authenticated data is not exposed by restarting with `api.auth: none`.
- Metadata provider outages fail startup; vector provider outages only fallback
  when explicitly allowed.
- Multilingual retrieval is documented as model-dependent, and persistent vector
  stores cannot silently mix same-dimension vectors from different embedding
  models.
- Degraded mode is observable from health surfaces and never silently changes
  response contracts.
- No edge-case test exposes secrets or sensitive payloads in failures.
