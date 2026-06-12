# Prioritized Feature Plan

This plan captures unimplemented or partial features found while reconciling `docs/` with the current codebase.

## Priority Order

| Priority | Feature | Status | Existing plan |
|----------|---------|--------|---------------|
| P0 | Token-budgeted `memory_ask` and token-aware ingestion chunking | Implemented | `token_budget_plan.md`, `improvements/context_building_plan.md` |
| P0 | Storage abstraction baseline: capabilities, schema checks, dimensions, fallback, dry-run, Postgres/PGVector | Implemented | `storage_agnostic_byoa_plan.md` |
| P0 | Retrieval precision: threshold, hybrid search, metadata rerank | Implemented | `improvements/retrieval_precision_plan.md` |
| P0 | MCP client smoke coverage for Codex, Gemini, Copilot, Claude, opencode | Implemented | `mcp_client_smoke_plan.md` |
| P0 | Weekly scheduled real-client MCP smoke coverage | Planned | `real_client_mcp_smoke_plan.md` |
| P1 | CLI foundation and command contract | Partial | `cli_implementation_plan.md` |
| P1 | CLI `ingest`, `search`, `retrieve`, and `ask` commands | Planned | `cli_implementation_plan.md` |
| P1 | CLI `serve` command for container/runtime entrypoint | Planned | `cli_implementation_plan.md`, `release_container_docs_plan.md` |
| P1 | Containerfile maintenance and container CI smoke tests | Planned | `release_container_docs_plan.md` |
| P1 | GitHub Pages documentation publishing from Markdown | Planned | `release_container_docs_plan.md` |
| P1 | Platform-specific importers | Planned here | This doc and `roadmap.md` |
| P2 | GitHub release and Docker Hub image publishing | Planned | `release_container_docs_plan.md` |
| P2 | Storage operational hardening: startup policy logs, production fallback warnings, audit events | Partial | `storage_agnostic_byoa_plan.md` |
| P2 | Storage provider expansion config and shared contract tests | Planned | `storage_agnostic_byoa_plan.md` |
| P2 | ChromaDB and Qdrant vector providers | Planned | `storage_agnostic_byoa_plan.md` |
| P2 | MongoDB metadata and MongoDB Atlas Vector Search | Planned | `storage_agnostic_byoa_plan.md` |
| P3 | Elasticsearch/OpenSearch vector providers | Planned | `storage_agnostic_byoa_plan.md` |
| P3 | Milvus/Zilliz and Weaviate vector providers | Planned | `storage_agnostic_byoa_plan.md` |
| P3 | Release notes, image scanning, SBOM, and provenance | Planned | `release_container_docs_plan.md` |
| P3 | Summaries, digests, consolidation | Planned here | This doc and `roadmap.md` |
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

- [ ] Add a separate real-client smoke harness for agent CLIs.
- [ ] Run the real-client smoke lane weekly through scheduled CI.
- [ ] Keep manual dispatch available for debugging and release checks.
- [ ] Start with Claude Code and Copilot CLI because they expose clear base-URL/provider environment variables.
- [ ] Validate Codex, opencode, and Gemini headless commands before wiring them into CI.
- [ ] Keep real-client smoke tests out of default PR gating until they are stable enough.

## P1: CLI Foundation And Commands

Use `cli_implementation_plan.md` as the source of truth.

Current status:

- [x] A basic `memory.cli` argparse entrypoint exists.
- [x] `python -m memory.cli tokenizer-check` is implemented for tokenizer diagnostics.
- [ ] The distributable console script name is not finalized.
- [ ] Global CLI options are not implemented across commands.
- [ ] `ingest`, `search`, `retrieve`, `ask`, and `serve` are not implemented.

Implementation sequence:

- [ ] Stabilize the command namespace, console script name, global flags, and JSON/text output contract.
- [x] Keep `tokenizer-check` as the first diagnostics command and align future diagnostics with the same output shape.
- [ ] Add shared helpers for config loading, JSON/text formatting, and stable exit-code handling.
- [ ] Implement `ingest <file>` using the same normalization, validation, hashing, dedupe, and storage path as API/MCP.
- [ ] Implement `search "<query>"` using the existing runtime search function and deterministic result shape.
- [ ] Implement `retrieve <id>` and `ask "<question>"` after ingest/search behavior is stable.
- [ ] Implement diagnostics: `health`, `config-show`, and `storage-check`.
- [ ] Implement `serve --host --port --config` as the preferred runtime/container entrypoint.
- [ ] Add `--config`, `--json`, `--top-k`, `--max-context-tokens`, and clear exit-code behavior across commands.
- [ ] Add unit tests for parser/output behavior and smoke tests for success, invalid input, and storage round trips.

## P1: Container And Docs Publishing Foundation

Use `release_container_docs_plan.md` as the source of truth.

Current status:

- [x] `Containerfile` exists.
- [x] CI exists in `.github/workflows/pipeline.yml`.
- [x] README and Markdown docs exist.
- [x] Project version is declared in `pyproject.toml`.
- [ ] Container build is not verified in CI.
- [ ] Container smoke test is not verified in CI.
- [ ] GitHub Pages workflow is not implemented.

Implementation sequence:

- [ ] Add `.dockerignore`.
- [ ] Keep `Containerfile` aligned with `pyproject.toml`, `uv.lock`, default config, and the CLI `serve` command once implemented.
- [ ] Add OCI labels to the container image.
- [ ] Add a container build job to CI.
- [ ] Add a container smoke test that starts the API/MCP app and checks readiness.
- [ ] Decide default vs optional image variants for Postgres/PGVector and tokenizer extras.
- [ ] Document runtime volume mounts and config/secrets environment variables.
- [ ] Add MkDocs configuration for `README.md`, top-level `docs/*.md`, and `docs/improvements/*.md`.
- [ ] Add a GitHub Pages workflow that builds and deploys docs from `main`.
- [ ] Add docs link validation before making docs deploy required.

## P1: Platform-Specific Importers

Roadmap importers are not implemented. The current code normalizes common payload variants, but it does not parse external exports.

Implementation sequence:

- [ ] Define an importer interface that returns unified conversation payloads.
- [ ] Add manual paste importer first because it is lowest risk and useful across clients.
- [ ] Add Gemini Takeout parser.
- [ ] Add Claude HTML export parser.
- [ ] Add optional local log importers for VS Code Copilot, Ollama, LM Studio, and Llama Stack.
- [x] Keep storage and ingestion unchanged by feeding all importers through the existing normalized schema path.

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

- [ ] Add provider-specific config model for ChromaDB, Qdrant, Milvus, Weaviate, MongoDB Atlas, Elasticsearch, and OpenSearch.
- [ ] Extend `providers.vector_db` and `providers.metadata_db` accepted values only as providers land.
- [ ] Add shared metadata-store contract tests.
- [ ] Add shared vector-store contract tests.
- [ ] Add fake SDK/client fixtures for unit tests.
- [ ] Add live integration tests gated by provider-specific environment variables.
- [ ] Add fallback and secret-redaction tests for each vector provider.
- [ ] Implement ChromaDB vector adapter first.
- [ ] Implement Qdrant vector adapter second.
- [ ] Implement MongoDB metadata, then MongoDB Atlas Vector Search.
- [ ] Update architecture docs and config examples after each provider lands.

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

## P3: Summaries And Consolidation

Implementation sequence:

- [ ] Add per-conversation summaries as metadata or a separate summary table.
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
