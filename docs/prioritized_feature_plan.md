# Prioritized Feature Plan

This plan captures unimplemented or partial features found while reconciling `docs/` with the current codebase.

## Priority Order

| Priority | Feature | Status | Existing plan |
|----------|---------|--------|---------------|
| P0 | Token-budgeted `memory_ask` and token-aware ingestion chunking | Implemented | `token_budget_plan.md`, `improvements/context_building_plan.md` |
| P0 | Storage abstraction baseline: capabilities, schema checks, dimensions, fallback, dry-run, Postgres/PGVector | Implemented | `storage_agnostic_byoa_plan.md` |
| P0 | Retrieval precision: threshold, hybrid search, metadata rerank | Implemented | `improvements/retrieval_precision_plan.md` |
| P0 | MCP protocol compliance: initialize instructions, schemas, pagination, logging, completion when client UX needs it | Partial | `mcp_utility_compliance_plan.md`, `mcp_plan.md` |
| P0 | Bearer-token auth and per-user memory isolation | Partial | `bearer_api_key_auth_plan.md` |
| P0 | Project workspaces and shared collaboration boundaries | Partial | `project_workspace_collaboration_plan.md` |
| P0 | MCP client feedback: response shape clarity, fact freshness, and source quality | Implemented | `improvements/client_feedback_improvement_plan.md` |
| P0 | Advanced search filters for ask, facts, and profile queries | Planned | `improvements/client_feedback_improvement_plan.md` |
| P0 | Conversation summary metadata and profile views | Implemented | `improvements/client_feedback_improvement_plan.md` |
| P0 | MCP client smoke coverage for Codex, Gemini, Copilot, Claude, opencode | Implemented | `mcp_client_smoke_plan.md` |
| P0 | Weekly scheduled real-client MCP smoke coverage | Implemented | `real_client_mcp_smoke_plan.md` |
| P0 | Bruno black-box API/MCP integration test layer | Implemented | `bruno_integration_test_plan.md` |
| P1 | CLI foundation and command contract | Implemented | `cli_implementation_plan.md` |
| P1 | CLI `ingest`, `search`, `retrieve`, and `ask` commands | Implemented | `cli_implementation_plan.md` |
| P1 | CLI `serve` command for container/runtime entrypoint | Implemented | `cli_implementation_plan.md`, `release_container_docs_plan.md` |
| P1 | Containerfile maintenance and container CI smoke tests | Implemented | `release_container_docs_plan.md` |
| P1 | GitHub Pages documentation publishing from Markdown | Implemented | `release_container_docs_plan.md` |
| P1 | Platform-specific importers and browser extension capture contract | Planned here | This doc, `roadmap.md`, and `browser_extension_capture_plan.md` |
| P2 | Recurring codebase cleanup and engineering-health review | Planned | `recurring_codebase_cleanup_plan.md` |
| P2 | Admin-only mutation workflows for local/API maintenance, not MCP | Planned | `improvements/client_feedback_improvement_plan.md` |
| P2 | GitHub release and Docker Hub image publishing | Planned | `release_container_docs_plan.md` |
| P2 | First release readiness, repository governance, and contribution policy | Planned | `first_release_readiness_plan.md` |
| P2 | Storage operational hardening: startup policy logs, production fallback warnings, audit events | Partial | `storage_agnostic_byoa_plan.md` |
| P2 | Storage provider expansion config and shared contract tests | Planned | `storage_agnostic_byoa_plan.md` |
| P2 | ChromaDB and Qdrant vector providers | Planned | `storage_agnostic_byoa_plan.md` |
| P2 | MongoDB metadata and MongoDB Atlas Vector Search | Planned | `storage_agnostic_byoa_plan.md` |
| P3 | Elasticsearch/OpenSearch vector providers | Planned | `storage_agnostic_byoa_plan.md` |
| P3 | Milvus/Zilliz and Weaviate vector providers | Planned | `storage_agnostic_byoa_plan.md` |
| P3 | Release notes, image scanning, SBOM, and provenance | Planned | `release_container_docs_plan.md` |
| P3 | Topic/project summaries, digests, consolidation | Planned here | This doc and `roadmap.md` |
| P3 | UI and developer experience | Planned here | This doc and `roadmap.md` |
| P4 | Graph memory, decay, shared memory, plugins | Planned here | This doc and `roadmap.md` |
| P4 | Optional encrypted cloud sync | Planned here | This doc and `roadmap.md` |

## P0: Token-Budgeted Ask And Token Chunking

Use the existing token plans as the source of truth:

- `token_budget_plan.md`
- `improvements/context_building_plan.md`

Implemented:

- [x] Add tokenizer config defaults and lazy tokenizer adapter with deterministic fallback.
- [x] Add optional `max_context_tokens` to API and MCP `memory_ask`.
- [x] Select, skip, or truncate retrieved chunks inside the budget.
- [x] Return non-breaking diagnostics such as selected/dropped chunks.
- [x] Add opt-in `chunking.strategy: token` ingestion windows with overlap.
- [x] Add unit, API, MCP, and regression tests.

## P0: Storage Abstraction Baseline

Use `storage_agnostic_byoa_plan.md` as the source of truth for implemented storage work.

Implemented:

- [x] Add provider capabilities to metadata and vector contracts.
- [x] Add metadata schema-version and vector dimensionality surfaces.
- [x] Add deterministic storage errors for unsupported operations, schema incompatibility, and dimension mismatches.
- [x] Update SQLite metadata and LanceDB vector adapters.
- [x] Add Postgres metadata and PGVector adapters.
- [x] Add in-memory vector provider for local/test/fallback use.
- [x] Add startup metadata schema compatibility checks.
- [x] Add startup and runtime vector dimension checks.
- [x] Add policy-gated vector fallback with degraded health.
- [x] Add dry-run storage wrappers with return-shape parity.
- [x] Add unit/integration coverage for storage safety, Postgres, PGVector, fallback, and redaction behavior.

## P0: Retrieval Precision

Use `improvements/retrieval_precision_plan.md` as the source of truth.

Implementation sequence:

- [x] Add a conservative similarity threshold.
- [x] Add keyword candidate matching alongside vector search.
- [x] Merge vector and keyword scores deterministically.
- [x] Add metadata-aware reranking for tags, source, and topics.
- [x] Verify the combined pipeline against fixed fixtures.
- [x] Validate and tune retrieval quality against real representative data.

## P0: MCP Protocol Compliance

Use `mcp_utility_compliance_plan.md` and `mcp_plan.md` as the source of truth.

MCP is the primary agent integration boundary, so protocol compliance is a P0
quality bar. Treat spec-defined fields, lifecycle behavior, tool schemas,
capabilities, utility methods, and transport behavior as compatibility-critical
surface area, not optional polish.

Current status:

- [x] Return server startup guidance through MCP `InitializeResult.instructions`.
- [x] Keep tool input schemas explicit enough for real clients to construct valid
      `tools/call` arguments.
- [x] Implement MCP pagination for list operations.
- [x] Implement sanitized MCP logging with no payload, query, embedding, secret,
      DSN, or stack-trace leakage.
- [x] Cover initialize, tool listing, pagination, logging, and malformed protocol
      cases with MCP transport tests.
- [ ] Keep checking protocol compliance whenever the MCP spec version changes or
      real clients expose new interpretation gaps.
- [ ] Implement MCP completion only when a concrete client UX needs prompt or
      resource argument suggestions.

## P0: Bearer-Token Auth And Per-User Isolation

Use `bearer_api_key_auth_plan.md` as the source of truth.

This is the top security priority before recommending Raspberry Pi, LAN, or
multi-user deployments. AMH currently has config placeholders but no enforced
auth. A server bound to `0.0.0.0` can expose conversation history to any device
that can reach the port.

Implementation sequence:

- [x] Add `api.auth: bearer_token` alongside existing `none` mode.
- [x] Store users and token hashes in the metadata database, not in config files.
- [x] Add admin CLI commands to create users, issue tokens, list tokens, and
      revoke tokens.
- [x] Accept tokens through `Authorization: Bearer <token>` for HTTP API and MCP.
- [x] Map each token to a server-side user/principal; scopes remain planned.
- [x] Stamp new conversations and facts with server-side `owner_id`.
- [x] Scope search, retrieve, ask, fact search, profile, and future admin
      operations by `owner_id`.
- [x] Prevent cross-user leakage from vector candidates by filtering against
      metadata ownership before returning or answering.
- [ ] Redact bearer tokens from logs, errors, and diagnostics.
- [ ] Keep `auth=none` for CI and loopback-only local development.
- [ ] Leave Google/Apple/Meta OAuth for later `oauth_resource_server` mode or a
      trusted identity-aware reverse proxy.

Acceptance criteria:

- A valid token is required for `/memory/*` and `/mcp/*` when bearer auth is
  enabled.
- User A cannot read, search, ask over, or retrieve User B's memory or facts.
- Raw tokens are displayed only once and only hashes are stored.
- Existing no-auth local tests continue to pass.
- Client request shape is compatible with future MCP OAuth: `Authorization:
  Bearer <token>`.

## P0: Project Workspaces And Shared Collaboration

Use `project_workspace_collaboration_plan.md` as the source of truth.

Bearer auth gives AMH a server-side actor. Project workspaces add the missing
collaboration boundary: users keep a private default memory space, but can also
join shared projects where selected conversations and facts are visible to other
members.

Implementation sequence:

- [x] Keep `owner_id` as the server-stamped actor/audit field.
- [x] Add `project_id` as the memory workspace boundary for conversations,
      facts, and vector metadata.
- [x] Add `projects` and `project_memberships` metadata tables.
- [x] Auto-create one default private project per user.
- [x] Resolve omitted `project_id` values to the authenticated user's default
      private project.
- [x] Validate project membership before every read, write, ask, retrieve,
      profile, and fact supersession operation.
- [x] Add optional `project_id` to HTTP and MCP tool schemas.
- [x] Keep project and membership administration in CLI/API flows first, not
      agent-facing MCP tools.
- [x] Add admin CLI commands to create/list projects and add/list project
      members.

Acceptance criteria:

- Existing single-user behavior remains private by default.
- Two users who share a project can collaborate in that project's memory.
- Users cannot read or write memory in projects where they are not members.
- Duplicate conversation hashes dedupe within a project, not globally.

## P0: MCP Client Feedback Improvements

Use `improvements/client_feedback_improvement_plan.md` as the source of truth.

This plan comes from real opencode and Codex feedback after using ai-memory-hub
through MCP. Treat the P0 items as product-contract work because they affect how
agents interpret successful answers.

Implementation sequence:

- [x] Make `memory_ask` structured fields clear for chunk-backed, fact-backed,
      mixed, conflict, and not-found answers.
- [x] Ensure a fact-layer answer does not look like an empty retrieval miss.
- [x] Separate raw source memory, normalized facts, and polished answer text for
      fact-backed answers.
- [x] Add source-quality or confidence-reason fields for fact answers.
- [x] Expose fact freshness fields such as `created_at`, `updated_at`,
      `last_confirmed_at`, and `superseded_at`.
- [x] Add tests for the Codex-observed response shape and confidence/freshness
      behavior.

## P0: Advanced Search Filters

Use `improvements/client_feedback_improvement_plan.md` as the source of truth.

These filters are high priority because they let agents narrow recall without
client-side post-filtering or overfetching.

Implementation sequence:

- [x] Document current `memory_search` filters in MCP tool descriptions and
      initialize instructions.
- [x] Add filter support to `memory_ask` where it can preserve answer quality.
- [x] Add fact/profile filters for source, predicate, date range, confidence,
      active/superseded status, and freshness.
- [x] Add tests for filtered ask retrieval and fact/profile filter combinations.

## P0: Conversation Summary Metadata And Profile Views

Use `improvements/client_feedback_improvement_plan.md` as the source of truth.

This is not graph memory. It is a lightweight retrieval hint: clients may provide
a short factual `metadata.summary` while still saving the complete conversation.
The summary can improve recall and reranking, but raw messages and normalized
facts remain the source of truth for answers and citations. Built-in generated
conversation, profile, topic, and project summaries are tracked in the same
feedback item because they share provenance and trust constraints.

Implementation sequence:

- [x] Document optional `metadata.summary` in the conversation schema and public docs.
- [x] Update MCP save guidance to ask for a short factual summary while still
      saving the complete conversation.
- [x] Validate `metadata.summary` as a bounded string.
- [x] Include summary text in metadata search/reranking.
- [x] Return summary in search/retrieve metadata, not as standalone answer
      evidence unless raw messages or facts support it.
- [x] Add tests showing summary improves recall without replacing message
      provenance.
- [x] Add a profile summary response that combines active facts, freshness, and
      compact provenance.
- [x] Keep generated summary storage separate from raw chunks and normalized
      facts with provenance-aware generated summary records.
- [x] Add server-generated conversation, topic, and project summaries after
      summary provenance is reliable.

## Closed: Bulk Conversation Insert

Use `improvements/client_feedback_improvement_plan.md` as the source of truth.

Decision: do not add a first-class bulk insert endpoint, CLI command, or MCP tool
now. Clients should store one complete conversation per insert, with the full
`messages` list in a single `conversation_json` object. Importers that need to
save many independent conversations can loop over the existing single insert path
while preserving source thread/session boundaries.

Rationale:

- Batch insert encourages clients to guess boundaries, split one real thread into
  many fake conversations, merge unrelated sessions, or resend overlapping
  messages.
- Single conversation insert keeps validation, dedupe, hashing, trusted append,
  fact extraction, and vector indexing synchronized through one write path.
- If importer throughput becomes a real problem later, optimize importer loops
  without changing the public memory contract first.

## P0: MCP Client Smoke Coverage

Use `mcp_client_smoke_plan.md` as the source of truth.

MCP remains the primary agent integration path. Framework-specific examples should
not be tracked as a separate priority unless they prove a concrete MCP/API gap.

Implementation sequence:

- [x] Keep CI smoke tests focused on MCP protocol compatibility and client-shaped payloads.
- [x] Preserve the existing Codex, Gemini, VS Code Copilot, and opencode profile coverage.
- [x] Add the missing Claude profile.
- [x] Add explicit retrieve round-trip checks and metadata assertions.
- [x] Add malformed payload negative cases with stable `invalid_input` envelopes.
- [x] Prefer a deterministic local OpenAI/Anthropic-compatible test gateway over Ollama, because Ollama is not the system under test.

Real-client CLI smoke coverage is tracked separately as P0 in
`real_client_mcp_smoke_plan.md`.

## P0: Weekly Scheduled Real-Client MCP Smoke Coverage

Use `real_client_mcp_smoke_plan.md` as the source of truth.

Implementation sequence:

- [x] Add a separate real-client smoke harness for agent CLIs.
- [x] Run the real-client smoke lane weekly through scheduled CI.
- [x] Keep manual dispatch available for debugging and release checks.
- [x] Start with Claude Code and Copilot CLI because they expose clear base-URL/provider environment variables.
- [x] Validate Codex, opencode, and Gemini headless command status before wiring them into strict CI.
- [x] Keep real-client smoke tests out of default PR gating until they are stable enough.

## P0: Bruno Black-Box API/MCP Integration Test Layer

Use `bruno_integration_test_plan.md` as the source of truth.

Bruno should provide a local and CI-friendly black-box smoke layer for the live
HTTP API and streamable HTTP MCP endpoint. It should prove the public contract
works against a running server and configured metadata/vector stores, while
pytest remains the source of truth for internal behavior, boundary conditions,
and storage adapter details.

- [x] Add a committed Bruno workspace under `tests/bruno`.
- [x] Cover `/health`, `/ready`, API insert/search/ask, MCP initialize,
      `tools/list`, `memory_validate`, `memory_insert`, `memory_search`, and
      `memory_ask`.
- [x] Run against the reusable Postgres/PGVector local stack.
- [x] Add a GitHub Actions lane that installs `@usebruno/cli`, runs `bru run`,
      uploads HTML/JUnit report artifacts, and publishes JUnit test results.
- [x] Add bearer-token and project-workspace smoke tests after CI token/project
      seeding is scripted.
- [x] Add an OAuth resource-server protected-resource metadata guard.
- [x] Add source/date/tag filter smoke coverage for API and MCP search/ask.
- [x] Verify the unauthenticated smoke collection with the real Bruno CLI locally.
- [ ] Promote to required CI only after repeated stable runs.

## P1: CLI Foundation And Commands

Use `cli_implementation_plan.md` as the source of truth.

Current status:

- [x] A basic `memory.cli` argparse entrypoint exists.
- [x] `python -m memory.cli tokenizer-check` is implemented for tokenizer diagnostics.
- [x] The distributable console script name is finalized as `aim`.
- [x] Global CLI options are implemented across commands.
- [x] `ingest`, `search`, `retrieve`, `ask`, and `serve` are implemented.

Implementation sequence:

- [x] Stabilize the command namespace, console script name, global flags, and JSON/text output contract.
- [x] Keep `tokenizer-check` as the first diagnostics command and align future diagnostics with the same output shape.
- [x] Add shared helpers for config loading, JSON/text formatting, and stable exit-code handling.
- [x] Implement `ingest <file>` using the same normalization, validation, hashing, dedupe, and storage path as API/MCP.
- [x] Implement `search "<query>"` using the existing runtime search function and deterministic result shape.
- [x] Implement `retrieve <id>` and `ask "<question>"` after ingest/search behavior is stable.
- [x] Implement diagnostics: `health`, `config-show`, and `storage-check`.
- [x] Implement `serve --host --port --config` as the preferred runtime/container entrypoint.
- [x] Add `--config`, `--json`, `--top-k`, `--max-context-tokens`, and clear exit-code behavior across commands.
- [x] Add unit tests for parser/output behavior and smoke tests for success, invalid input, and storage round trips.

## P1: Container And Docs Publishing Foundation

Use `release_container_docs_plan.md` as the source of truth.

Current status:

- [x] `Containerfile` exists.
- [x] CI exists in `.github/workflows/pipeline.yml`.
- [x] README and Markdown docs exist.
- [x] Project version is declared in `pyproject.toml`.
- [x] Container build is verified in CI.
- [x] Container smoke test is verified in CI.
- [x] GitHub Pages workflow is implemented.

Implementation sequence:

- [x] Add `.dockerignore`.
- [x] Keep `Containerfile` aligned with `pyproject.toml`, `uv.lock`, default config, and the CLI `serve` command once implemented.
- [x] Add OCI labels to the container image.
- [x] Add a container build job to CI.
- [x] Add a container smoke test that starts the API/MCP app and checks readiness.
- [x] Add non-root and OpenShift arbitrary-UID container hardening.
- [x] Add Containerfile linting with Hadolint.
- [x] Decide default vs optional image variants for Postgres/PGVector and tokenizer extras.
- [x] Document runtime volume mounts and config/secrets environment variables.
- [x] Add MkDocs configuration for `README.md`, top-level `docs/*.md`, and `docs/improvements/*.md`.
- [x] Add a GitHub Pages workflow that builds and deploys docs from `main`.
- [x] Add docs link validation before making docs deploy required.

## P1: Platform-Specific Importers

The importer boundary and manual paste importer are implemented. Export-specific parsers remain planned.

Implementation sequence:

- [x] Define an importer interface that returns unified conversation payloads.
- [x] Add manual paste importer first because it is lowest risk and useful across clients.
- [ ] Add Gemini Takeout parser.
- [ ] Add Claude HTML export parser.
- [ ] Add optional local log importers for VS Code Copilot, Ollama, LM Studio, and Llama Stack.
- [x] Keep storage and ingestion unchanged by feeding all importers through the existing normalized schema path.

## P1: Browser Extension Capture Contract

Use `browser_extension_capture_plan.md` as the source of truth.

- [ ] Keep browser extension implementations in separate repositories.
- [ ] Treat existing `POST /memory/insert` as the normalized web-chat capture contract.
- [ ] Document normalized extension payloads for ChatGPT, Microsoft Copilot,
  Claude, Gemini, and similar web platforms.
- [ ] Keep raw HTML, DOM snapshots, screenshots, and selector evidence out of
  ai-memory-hub storage by default.
- [ ] Add extension-shaped API contract tests before implementing extension repos.
- [ ] Add explicit CORS allowlist config for browser extension origins.
- [ ] Document bearer-token setup and trusted append behavior for repeated
  captures of the same upstream thread.

## P2: GitHub Releases And Docker Hub Publishing

Use `release_container_docs_plan.md` as the source of truth.

Implementation sequence:

- [ ] Define release version policy: GitHub tag must match `pyproject.toml`.
- [ ] Add release tag validation for `vMAJOR.MINOR.PATCH`.
- [ ] Add Docker Hub secrets: `DOCKERHUB_USERNAME`, `DOCKERHUB_TOKEN`, and optional namespace.
- [ ] Add release workflow triggered by `release.published`.
- [ ] Build from `Containerfile` with Docker Buildx.
- [ ] Push versioned tags for every release.
- [ ] Push `latest` only for stable releases.
- [ ] Add image digest to workflow summary and release notes.
- [ ] Add manual `workflow_dispatch` for release publish recovery.

## P2: First Release Readiness

Use `first_release_readiness_plan.md` as the source of truth.

Implementation sequence:

- [ ] Add `CONTRIBUTING.md`, `CODE_OF_CONDUCT.md`, and `SECURITY.md`.
- [ ] Add pull request and issue templates.
- [ ] Stop direct pushes to `main` after the release-readiness PR lands.
- [ ] Configure branch protection and required checks.
- [ ] Add release checklist and version/tag validation.
- [ ] Run a `v0.1.0-rc.1` release-candidate drill before publishing `v0.1.0`.

## P2: Recurring Codebase Cleanup

Use `recurring_codebase_cleanup_plan.md` as the source of truth.

- [ ] Run weekly automated detection checks for dead code, stale docs,
  accidental hardcoding, complex control flow, and unsafe logging patterns.
- [ ] Open one focused monthly cleanup PR for the highest-impact findings.
- [ ] Review public docs and examples before each release.
- [ ] Add regression tests for cleanup work that changes behavior.
- [ ] Keep cleanup PRs narrow enough that review can distinguish behavior
  preservation from intentional behavior changes.

## P2: Admin-Only Mutation Workflows

Use `improvements/client_feedback_improvement_plan.md` as the source of truth.

General delete/update through MCP is intentionally out of scope. Source memory
should remain immutable by default; corrections should use fact supersession.
Any future mutation support must be admin-only through CLI and HTTP API, with
clear auth, audit, and provider-consistency behavior.

Implementation sequence:

- [ ] Define admin-only soft delete, fact hide, metadata edit, append, and
      supersession semantics.
- [ ] Do not add MCP delete/update tools.
- [ ] Add admin-scoped HTTP API endpoints only after auth scope handling is clear.
- [ ] Add admin CLI commands for local maintenance only after API semantics are stable.
- [ ] Add audit fields for who/what performed a mutation.
- [ ] Add provider contract tests for metadata and vector cleanup parity.

## P2: Storage Operational Hardening

Use `storage_agnostic_byoa_plan.md` as the source of truth.

Implemented:

- [x] Redact DSNs, passwords, tokens, and API keys in fallback logs.
- [x] Expose runtime mode: `ok`, `degraded`, or `dry_run`.
- [x] Expose active/requested vector provider and fallback state in runtime health.
- [x] Treat metadata init errors, schema incompatibility, and vector dimension mismatch as hard errors.
- [x] Treat vector init errors as fatal unless fallback is explicitly enabled.

Remaining sequence:

- [ ] Log `allow_fallback` and `dry_run` startup policy consistently, including disabled state.
- [ ] Warn when `allow_fallback=true` is used under a production profile.
- [ ] Emit structured audit events for fallback activation and dry-run skipped writes.
- [ ] Consider changing the default `storage.vector.allow_fallback` to `false` for production-oriented configs.

## P2: Storage Provider Expansion

Use `storage_agnostic_byoa_plan.md` as the source of truth.

Provider expansion sequence:

- [x] Add provider-specific config model for ChromaDB, Qdrant, Milvus, Weaviate, MongoDB Atlas, Elasticsearch, and OpenSearch.
- [x] Extend `providers.vector_db` and `providers.metadata_db` accepted values only as providers land.
- [x] Add shared metadata-store contract tests.
- [x] Add shared vector-store contract tests.
- [x] Add fake SDK/client fixtures for unit tests.
- [ ] Add live integration tests gated by provider-specific environment variables.
- [x] Add fallback and secret-redaction tests for ChromaDB.
- [x] Implement ChromaDB vector adapter first.
- [ ] Implement Qdrant vector adapter second.
- [ ] Implement MongoDB metadata, then MongoDB Atlas Vector Search.
- [x] Update architecture docs and config examples after ChromaDB landed.

## P3: Later Storage Providers

Use `storage_agnostic_byoa_plan.md` as the source of truth.

Implementation sequence:

- [ ] Implement Elasticsearch vector adapter.
- [ ] Implement OpenSearch vector adapter.
- [ ] Keep Elasticsearch/OpenSearch vector-only at first; add hybrid capability only after API semantics are designed.
- [ ] Implement Milvus/Zilliz vector adapter.
- [ ] Implement Weaviate vector adapter.
- [ ] Add Docker Compose examples for local provider smoke testing.
- [ ] Document provider limitations: consistency, index readiness, score interpretation, distance metrics, and local/hosted differences.

## P3: Release Notes And Supply Chain Hardening

Use `release_container_docs_plan.md` as the source of truth.

Implementation sequence:

- [ ] Decide release notes source: GitHub generated notes, `CHANGELOG.md`, or conventional commits.
- [ ] Add release template/checklist.
- [ ] Include Docker image tags, digest, docs URL, and upgrade notes in release notes.
- [ ] Add dependency vulnerability scan plan.
- [ ] Add image vulnerability scan plan.
- [ ] Add SBOM generation plan.
- [ ] Add provenance/signing plan.
- [ ] Document supported image lifecycle and security fix policy.

## P3: Topic/Project Summaries And Consolidation

Implementation sequence:

- [ ] Add topic-level and daily/weekly digest generation.
- [ ] Add duplicate/redundant memory consolidation tooling.
- [ ] Add stable-fact extraction only after summary storage and provenance are reliable.
- [ ] Use `improvements/memory_quality_fact_layer_plan.md` for confidence scoring, compact provenance, result deduplication, profile facts, and correction/supersession behavior.

## P3: UI And Developer Experience

Implementation sequence:

- [x] Expose OpenAPI docs and examples for current HTTP contracts.
- [ ] Add a storage health and diagnostics view.
- [ ] Add search and conversation viewer flows.
- [ ] Add import/upload workflows after importers exist.
- [ ] Add Python SDK first, then TypeScript SDK.
- [ ] Add packaging only after CLI and SDK contracts are stable.

## P4: Advanced Memory

Implementation sequence:

- [ ] Add entity extraction and relationship storage behind an optional module.
- [ ] Add graph-aware retrieval only after entity quality is testable.
- [ ] Add relevance decay and importance scoring.
- [ ] Add shared memory spaces and agent-specific filters.
- [ ] Add plugin extension points once importer/provider boundaries are stable.

## P4: Optional Cloud Sync

Implementation sequence:

- [ ] Define encrypted backup/export format.
- [ ] Add local backup and restore before network sync.
- [ ] Add opt-in encrypted sync for SQLite metadata and vector bundles.
- [ ] Add multi-device conflict handling.

Cloud sync must stay opt-in and must not change the local-first default.
