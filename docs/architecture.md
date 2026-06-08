# Architecture

ai-memory-hub is a local-first memory engine built around a unified JSON schema, deterministic ingestion, configurable providers, and MCP/API interfaces.

Related docs:
- Project overview: `../README.md`
- Agent integration: `agents.md`
- Roadmap and priority order: `roadmap.md`
- Prioritized implementation plan: `prioritized_feature_plan.md`
- MCP client smoke testing: `mcp_client_smoke_plan.md`
- Deterministic ingestion details: `deterministic_ingestion_plan.md`
- Storage/provider details: `storage_agnostic_byoa_plan.md`
- MCP contract details: `mcp_plan.md`

## Current Status

Implemented and verified in the codebase:

- Schema-first ingestion through HTTP `POST /memory/insert` and MCP `memory_insert`.
- MCP validation/search/retrieve/ask tools, plus conversation, search, timeline, health resources and prompts.
- MCP client smoke profiles for Codex, Gemini, VS Code Copilot, and opencode over the streamable HTTP transport.
- Omitted-ID insertion: normalization assigns a UUID when clients omit `id`.
- Deterministic ingestion with message hashes, conversation hashes, duplicate detection, same-thread append handling, append-only chunking, and indexing state updates.
- Message-level chunking by default, plus opt-in token-window chunking for long messages.
- Embedding providers: OpenAI and local deterministic embeddings.
- Metadata stores: SQLite and Postgres.
- Vector stores: LanceDB, PGVector, and in-memory.
- Provider capabilities, schema-version checks, vector dimensionality checks, fallback policy, degraded health state, and dry-run wrappers.
- Search result grouping by conversation score.
- `memory_ask` returns structured `results`, human-readable `answer`, and `citations`.
- Token-budgeted `memory_ask` is available through config or per-request `max_context_tokens`, with optional diagnostics.
- Basic deterministic topic enrichment from message text.

Planned or partial:

- Retrieval precision improvements, including similarity thresholds, hybrid keyword/vector search, and metadata-aware reranking, are partially implemented. Representative-data tuning remains planned in `improvements/retrieval_precision_plan.md`.
- Claude MCP smoke profile, negative client payload cases, explicit E2E retrieve checks, platform-specific importers, CLI commands, summaries, UI, SDKs, framework-specific agent examples, graph memory, shared memory, plugins, and optional cloud sync remain roadmap work.

## High-Level System Diagram

```text
Source Messages or Client Payloads
  -> Normalize into Unified Conversation Schema
  -> Schema Validation
  -> Deterministic Hashing + Duplicate/Append Detection
  -> Metadata Store Write
  -> Message-Level or Token-Window Chunking
  -> Embed Chunks
  -> Vector Store Write
  -> Search / Retrieve / Ask via MCP or HTTP API
```

## Ingestion Pipeline

The implemented ingestion path is schema-first and deterministic. It accepts already-structured conversation payloads and also normalizes common client payload variants, such as `conversation` arrays and message `content` fields.

Steps:

1. Coerce payload shape and normalize defaults.
2. Assign a UUID if no `id` is provided.
3. Normalize roles/text fields and metadata.
4. Generate message and conversation hashes.
5. Validate against `memory/schema/conversation.schema.json`.
6. Enrich metadata topics with deterministic keyword rules.
7. Detect exact duplicates and trusted same-thread appends.
8. Store metadata and child message/chunk records.
9. Embed only the chunks that need indexing.
10. Store vectors and mark chunk indexing state.
11. Return deterministic outcome fields, including `deduplicated`, `appended_messages`, and `embedded_chunks`.

## Normalization Layer

The normalization layer enforces one runtime shape across clients and storage backends.

Responsibilities:

- Validate schema.
- Normalize message `role`, `text`, and common `content` aliases.
- Normalize timestamps and metadata defaults.
- Attach stable message hashes, conversation hashes, import timestamps, and topic metadata.
- Produce deterministic message-level or token-window chunks with stable chunk IDs.

Role normalization from arbitrary upstream exports is not yet a full platform-importer layer. Platform-specific parsers remain planned work.

## Storage Layer

Local-first remains the default. No cloud sync is implemented or enabled by default.

Default providers:

- Metadata store: SQLite
- Vector store: LanceDB with in-memory fallback when allowed
- Embeddings: OpenAI or local deterministic provider

Supported providers:

- Metadata: SQLite, Postgres
- Vectors: LanceDB, PGVector, in-memory

Implemented storage safety:

- Provider capability reporting.
- Metadata schema-version compatibility checks.
- Vector dimensionality checks at startup and runtime.
- Policy-gated vector fallback to in-memory storage.
- Dry-run wrappers that skip writes while preserving response shape.
- Secret-safe log redaction for storage and fallback diagnostics.
- Runtime health state for normal, degraded, and dry-run modes.

## Interfaces

HTTP API:

- `POST /memory/insert`
- `POST /memory/search`
- `POST /memory/retrieve`
- `POST /memory/ask`

MCP tools:

- `memory_validate`
- `memory_insert`
- `memory_search`
- `memory_retrieve`
- `memory_ask`

MCP resources:

- `memory://conversation/example`
- `memory://conversation/{id}`
- `memory://search/{query}`
- `memory://timeline/{day}`
- `memory://health`

MCP prompts:

- `save_conversation`
- `search_memory`
- `ask_memory`
- `summarize_conversation`

MCP tool responses use a stable envelope with `status`, `id`, `results`, `cursor`, `error_code`, and `error_message`, plus tool-specific fields.

## Retrieval And Ask

Implemented retrieval flow:

1. Embed the query.
2. Search the active vector store.
3. Fetch matching conversations from metadata storage.
4. Enrich rows with conversation-level score and match count.
5. Return grouped, deterministic top-k results.

`memory_ask` builds a simple answer from the selected search rows and returns:

- `results`: structured matches.
- `answer`: human-readable summary text.
- `citations`: provenance for included chunks.

When budgeting is enabled, `memory_ask` selects, truncates, or drops context chunks within the configured budget and may return `context_tokens_used`, `chunks_selected`, `chunks_dropped`, and `tokenizer_used`.

Hybrid keyword/vector scoring, similarity thresholds, and metadata-aware reranking are not yet implemented.

## Bring Your Own Stack Model

Bring Your Own Stack means embedding and storage providers are selected through config while the ingestion, API, and MCP contracts stay stable.

```text
[Clients]
  MCP / HTTP API
       |
       v
[Hub Runtime]
  Normalization
  Validation
  Dedupe / Append Logic
  Chunking
       |
       v
[Configured Providers]
  Embeddings: openai | local
  Metadata: sqlite | postgres
  Vectors: lancedb | pgvector | memory
       |
       v
[Consumers]
  Search / Retrieve / Ask / RAG
```

## Config-Driven Provider System

All providers are wired from config at startup.

Example:

```yaml
providers:
  embeddings: local
  metadata_db: sqlite
  vector_db: lancedb

storage:
  dry_run: false
  metadata_schema_versions: [1]
  vector:
    allow_fallback: true
    distance: cosine

interfaces:
  mcp: true
  api: true
```

See `prioritized_feature_plan.md` for the current implementation priority order for unimplemented features.
