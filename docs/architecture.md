# Architecture

ai-memory-hub is a local-first memory engine built around a unified JSON schema, deterministic ingestion, configurable providers, and MCP/API interfaces.

Related docs:
- Project overview: `../README.md`
- Agent integration: `agents.md`
- Current status and remaining programs: `project_status.md`
- GitHub issue-backed planning: `plans.md`
- MCP client smoke testing: `mcp_client_smoke_plan.md`
- Storage/provider details: `storage_agnostic_byoa_plan.md`
- MCP and agent contract: `agents.md`
- MCP utility compliance: `mcp_utility_compliance_plan.md`

## Current Status

Implemented and verified in the codebase:

- Schema-first ingestion through HTTP `POST /memory/insert`, registered-parser
  uploads through `POST /memory/import`, and MCP `memory_insert`.
- MCP validation/search/retrieve/ask tools, fact/profile/review/project helper
  tools, plus conversation, search, timeline, health resources and prompts.
- MCP client smoke profiles for Codex, Gemini, VS Code Copilot, and opencode over the streamable HTTP transport.
- Omitted-ID insertion: normalization assigns a UUID when clients omit `id`.
- Deterministic ingestion with message hashes, conversation hashes, duplicate detection, same-thread append handling, append-only chunking, and indexing state updates.
- Sensitive-content quarantine for likely secrets and high-confidence sensitive
  PII, stored as `memory_status: quarantined` and excluded from default recall
  until explicit review approval.
- Message-level chunking by default, plus opt-in token-window chunking for long messages.
- Embedding providers: HTTP endpoint and local deterministic embeddings.
- Multilingual retrieval is supported when the configured embedding model
  supports the languages involved; ai-memory-hub itself is not English-only.
- Metadata stores: SQLite, Postgres, and MongoDB.
- Vector stores: LanceDB, Qdrant, Milvus/Zilliz, Weaviate,
  PGVector, MongoDB Atlas Vector Search, Elasticsearch, OpenSearch, Redis,
  Pinecone, Turbopuffer, Vespa, Typesense, and in-memory.
- Provider capabilities, schema-version checks, vector dimensionality checks, fallback policy, degraded health state, and dry-run wrappers.
- Search result grouping by conversation score.
- `memory_ask` returns structured `results`, human-readable `answer`, and `citations`.
- Token-budgeted `memory_ask` is available through config or per-request `max_context_tokens`, with optional diagnostics.
- Basic deterministic topic enrichment from message text.

Memory history is append-only for `v1.0.0-beta`. General destructive memory
update/delete endpoints and MCP tools do not ship in the beta. Corrections are
represented as new memories, fact supersession, or governance/review events.
Archive/restore remains a near-term retention and storage-optimization feature;
when it ships, it should be implemented as a visibility state over preserved
history rather than as history deletion.

Remaining programs are tracked through the [roadmap](roadmap.md) and linked
GitHub epics. They include release hardening, exact agent/model provenance,
remaining import compatibility, plugin packaging, the memory workspace and SDKs,
advanced memory lifecycle controls, application encryption, and optional graph
provider expansion.

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
7. Quarantine likely secrets and high-confidence sensitive PII before chunks,
   vectors, facts, or graph records are written.
8. Compare ordered message hashes for a matched conversation: equal snapshots
   deduplicate, strict supersets append, strict prefixes are stale no-ops, and
   every other divergence is rejected without mutation.
9. Store metadata and child message/chunk records. Appends use an optimistic
   conversation-hash compare-and-swap so a concurrent writer cannot be lost or
   reordered; compatible races are reclassified against the new stored state.
10. Embed only the chunks that need indexing, splitting remote inputs into
    configured count- and byte-bounded batches while preserving chunk order.
11. Store vectors and mark chunk indexing state.
12. Derive facts and graph records, limiting trusted-append extraction to newly
    appended messages while retaining their absolute source indexes.
13. Return deterministic outcome fields, including `deduplicated`, `appended_messages`, and `embedded_chunks`.

Timestamp freshness never overrides the ordered hash comparison. A stale
snapshot cannot truncate newer history, and a first-message or middle-message
edit cannot be silently combined into a transcript that never existed.

### Fact validation and confidence

Fact extraction is a two-stage path: deterministic rules or the optional
external extractor produce candidates, then a shared validator checks evidence
before any candidate is stored. New facts must cite a non-empty character span
inside a declared source message. The validator classifies the surrounding
statement as asserted, negated, hypothetical, quoted, uncertain, or temporary;
negated and hypothetical candidates are discarded. Quoted, uncertain,
temporary, inferred, auto-saved, and assistant-derived facts receive calibrated
confidence. Assistant-derived facts remain non-authoritative until supported by
user evidence.

The confidence rubric is deterministic: quoted `0.30` (low), uncertain `0.40` (low),
assistant-derived `0.50` (medium), inferred topic `0.55` (medium), temporary
`0.60` (medium), client auto-save `0.65` (medium), direct user assertion `0.90`
(high), and direct user correction `0.98` (high). A matching direct user fact is
the authoritative corroboration for an assistant-authored claim; the original
assistant-derived row remains medium-confidence provenance.

Validated fact responses expose `evidence_span`, `source_role`,
`assertion_type`, `temporal_scope`, `confidence_score`, `confidence_reason`,
`extractor_name`, `extractor_version`, `rule_identifier`, and
`validation_status`. Existing stored facts are returned with
`validation_status: legacy` and do not receive invented evidence spans.

External extractor output is schema-bound and capped at 100 candidates per
ingestion call. Each candidate must include bounded subject, predicate, object,
source message indexes, evidence span, and extractor/rule identifiers. Message
indexes are relative to the message batch passed to the extractor and are
translated to conversation indexes after validation. Unknown fields, invalid
indexes or spans, unsupported claims, oversized values, and extractor failures
are ignored without blocking deterministic ingestion.

## Normalization Layer

The normalization layer enforces one runtime shape across clients and storage backends.

Responsibilities:

- Validate schema.
- Normalize message `role`, `text`, and common `content` aliases.
- Normalize timestamps and metadata defaults.
- Attach stable message hashes, conversation hashes, import timestamps, topic
  metadata, and a project/source/parser-scoped digest when a registered importer
  provides a reliable upstream session ID.
- Produce deterministic message-level or token-window chunks with stable chunk IDs.

Role normalization from arbitrary upstream exports is not yet a full platform-importer layer. Platform-specific parsers remain planned work.

## Storage Layer

Local-first remains the default. No cloud sync is implemented or enabled by default.

Default providers:

- Metadata store: SQLite
- Vector store: LanceDB with in-memory fallback when allowed
- Embeddings: HTTP endpoint or local deterministic provider

Supported providers:

- Metadata: SQLite, Postgres, MongoDB
- Vectors: LanceDB, Qdrant, Milvus/Zilliz, Weaviate, PGVector,
  MongoDB Atlas Vector Search, Elasticsearch, OpenSearch, Redis, Vespa,
  Typesense, Pinecone, Turbopuffer,
  in-memory

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
- `POST /memory/import`
- `POST /memory/search`
- `POST /memory/retrieve`
- `POST /memory/ask`
- `POST /memory/facts/search`
- `POST /memory/profile/get`
- `POST /memory/facts/supersede`
- `POST /memory/pending/approve`
- `POST /memory/pending/reject`
- `GET /memory/projects`
- `GET /memory/projects/default`
- `GET /memory/projects/{project_id}`
- `GET /health`
- `GET /ready`
- `GET /observability`
- `GET /.well-known/oauth-protected-resource`
- `GET /.well-known/oauth-protected-resource/mcp`
- `GET /.well-known/oauth-authorization-server`
- Connect UI and local OAuth routes under `/connect`, `/auth/*`, and `/oauth/*`

MCP tools:

- `memory_validate`
- `memory_insert`
- `memory_search`
- `memory_retrieve`
- `memory_ask`
- `memory_fact_search`
- `memory_profile_get`
- `memory_lookup`
- `memory_fact_supersede`
- `memory_pending_approve`
- `memory_pending_reject`
- `memory_project_list`
- `memory_project_default_get`
- `memory_project_get`

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
2. Build typed vector filters from trusted authorization scope and request filters.
3. Push owner/project constraints into vector providers that advertise support,
   so authorization scope is applied before vector `top_k` selection.
4. Use bounded adaptive oversampling for unsupported residual constraints, then
   validate every candidate against authoritative metadata.
5. Gather independent dense, lexical, fact, and optionally graph candidate lists.
6. Fuse list positions with reciprocal rank fusion; provider-specific raw score
   scales are not mixed into the fused rank.
7. Prioritize exact identifiers, quoted phrases, and rare exact terms, then run
   an optional bounded semantic reranker over redacted chunk projections.
8. Apply deterministic recency, importance, pin, access, and stable tie ordering.
9. Enrich rows with conversation-level rank and match count, then return grouped
   deterministic top-k results.

Provider capabilities report the fields supported for vector-side filtering.
Runtime health reports those fields and the bounded residual-fallback policy;
filtered search diagnostics report pushed and residual fields, attempt count,
and the final candidate limit. Self-query output never supplies owner or project
scope.

`memory_ask` builds a simple answer from the selected search rows and returns:

- `results`: structured matches.
- `answer`: human-readable summary text.
- `citations`: provenance for included chunks.

Before choosing an answer basis, `memory_ask` now places normalized facts and
ranked conversation chunks into a common evidence-candidate stage. Candidates
retain their type-specific payload while being ordered by direct support,
correction status, source quality, confidence, freshness, and relevance. A
single high-confidence direct-user or corrected fact can use the existing fast
path. Conflicting facts, weak or assistant-derived facts, and questions that ask
for context retrieve chunks before the basis is selected. Matching direct-user
chunks corroborate weak facts; disagreements return `answer_basis: conflict`
with both fact and chunk evidence. Corrected current facts remain authoritative
over stale historical chunks. Context budgeting is applied to the selected
chunk evidence after arbitration.

When budgeting is enabled, `memory_ask` selects, truncates, or drops context chunks within the configured budget and may return `context_tokens_used`, `chunks_selected`, `chunks_dropped`, and `tokenizer_used`. Exact suffix/prefix overlap between adjacent token windows from the same source message is represented once in the selected context while each retained span keeps its original citation.

Semantic reranking is optional. With no reranker, on timeout, or on invalid
reranker output, retrieval uses the same deterministic fused ordering. Inputs
to a configured reranker contain only a bounded query and bounded projections
of the chunk, source, timestamp, and tags; secrets are redacted first.

## Bring Your Own Stack Model

Bring Your Own Stack means embedding and storage providers are selected through config while the ingestion, API, and MCP contracts stay stable.

The embedding provider is part of the storage contract. Stored vectors and query
vectors must come from the same embedding provider, model, dimension, and
configuration. Multilingual memory depends on choosing a multilingual-capable
embedding model. Changing the embedding model or provider for an existing vector
index requires an explicit reindex or a separate vector namespace/index, even
when the new model has the same dimensionality.

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
  Embeddings: http | local
  Metadata: sqlite | postgres | mongodb
  Vectors: lancedb | qdrant | milvus | weaviate | pgvector |
           mongodb_atlas | elasticsearch | opensearch | redis | vespa |
           typesense | pinecone | turbopuffer | memory
       |
       v
[Consumers]
  Search / Retrieve / Ask / RAG
```

## Config-Driven Provider System

All providers are configured from one config file at startup, but the runtime
uses only one metadata provider and one vector provider. The active providers
are selected by `providers.metadata_db` and `providers.vector_db`. The
`storage.metadata_providers` and `storage.vector_providers` sections hold
provider-specific settings for possible providers; entries in those sections are
not active unless selected.

Multiple active metadata or vector stores are not supported in the normal write
and query path. Migration, dual-write validation, backups, or federated search
should be implemented as explicit tools or modes rather than inferred from
multiple configured provider blocks.

Provider dependencies are installed by package extras, not by config. For local
development install only the extra for the provider you select, such as
`uv sync --extra postgres` or `uv sync --extra qdrant`. The root container image
is intentionally limited to the default SQLite/LanceDB quickstart path; provider
examples use provider-local Containerfiles when extra SDKs are required.

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
  vector_providers:
    qdrant:
      url: http://127.0.0.1:6333
      collection: memory_vectors
    milvus:
      uri: http://127.0.0.1:19530
      token: ""
      collection: memory_vectors
    weaviate:
      url: http://127.0.0.1:8080
      api_key: ""
      collection: MemoryVector
    mongodb_atlas:
      uri: ""
      database: ai_memory_hub
      collection: memory_vectors
      index: memory_vector_index
    elasticsearch:
      url: http://127.0.0.1:9200
      username: ""
      password: ""
      index: memory_vectors
    opensearch:
      url: http://127.0.0.1:9200
      username: ""
      password: ""
      index: memory_vectors
    redis:
      url: redis://127.0.0.1:6379/0
      index: memory_vectors
      key_prefix: "memory_vectors:"
    pinecone:
      api_key: ""
      index: memory-vectors
      namespace: default
      cloud: aws
      region: us-east-1
      create_index: false
    turbopuffer:
      api_key: ""
      namespace: memory-vectors
      region: gcp-us-central1
    vespa:
      url: http://127.0.0.1:8080
      token: ""
      namespace: memory
      schema: memory
      rank_profile: vector_similarity
    typesense:
      url: http://127.0.0.1:8108
      api_key: ""
      collection: memory_vectors
  metadata_providers:
    mongodb:
      uri: ""
      database: ai_memory_hub
      conversations_collection: conversations

interfaces:
  mcp: true
  api: true
```

See `project_status.md` for the current implementation classification and
`plans.md` for the issue-backed planning policy.
