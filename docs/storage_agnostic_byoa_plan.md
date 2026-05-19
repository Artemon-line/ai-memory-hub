# Storage-Agnostic BYOA Plan

Plan for evolving `ai-memory-hub` from SQLite-first persistence to a storage-agnostic architecture that supports BYOA backends (Postgres, MongoDB, and other modern databases).

## Goals

- Decouple core memory logic from concrete storage engines.
- Support multiple metadata and vector backends via adapters.
- Enable BYOA deployment where users bring their own DB infra.
- Preserve API/MCP contracts across storage providers.

## Non-Goals

- Rewriting API/MCP surface area.
- Forcing one universal schema across relational and document stores.
- Immediate support for every backend in one release.

## Target Architecture

Use a layered storage contract:

1. domain layer (ingestion/search/ask logic)
2. storage interface layer (protocols/ABCs)
3. provider adapters (sqlite/postgres/mongo/etc.)
4. factory/wiring (config-driven provider selection)

Core rule:

- domain code imports only storage interfaces, never provider-specific clients.

## Storage Contracts

Define explicit interfaces in `memory/backend/contracts.py`:

- `MetadataStore`
  - `insert(conversation_json) -> id`
  - `get(id) -> conversation | None`
  - `get_many(ids) -> dict[id, conversation]`
  - optional: `update`, `delete`, `health`, `close`
- `VectorStore`
  - `insert(memory_id, embeddings) -> None`
  - `search(query_vector, top_k=5) -> list[match]`
  - optional: `delete(memory_id)`, `health`, `close`

Design requirements:

- deterministic return shapes
- provider-independent error model
- clear type contracts for `id`, timestamps, metadata, vectors

## Provider Model

## 1) Metadata providers

Phase order:

1. SQLite (existing, baseline)
2. Postgres adapter
3. MongoDB adapter

Notes:

- Postgres is recommended default for production BYOA.
- MongoDB supports document-native conversation payloads.
- Keep conversation object canonical in app layer; transform only at adapter boundary.

## 2) Vector providers

Phase order:

1. LanceDB (existing)
2. pgvector adapter (real Postgres vector path)
3. optional external vector stores (Qdrant, Weaviate, Milvus)

## Configuration Design

Replace/extend provider config into explicit storage config:

```yaml
storage:
  metadata:
    provider: sqlite   # sqlite | postgres | mongodb
    dsn: ""            # postgres://... or mongodb://...
    database: ""       # required for mongodb
    collection: conversations
    table: conversations
  vector:
    provider: lancedb  # lancedb | pgvector | memory
    dsn: ""            # used by pgvector
    table: memory_vectors
```

Rules:

- provider-specific required fields validated at startup.
- fail fast with actionable config errors.

## Data Model Strategy

Canonical app model:

- keep current unified conversation JSON as source of truth.

Adapter mapping:

- relational stores:
  - core columns (`id`, `source`, `timestamp`, `metadata_json`, `messages_json`)
  - optional indexed/derived columns (`tags`, `topics`, `updated_at`)
- document stores:
  - single canonical document with indexed fields (`id`, `source`, `timestamp`, tags/topics)

ID strategy:

- application-generated UUID remains canonical.

## Migration Plan

## Phase 1: Contract extraction

- extract `MetadataStore` and `VectorStore` interfaces.
- refactor current SQLite/LanceDB implementations to conform strictly.
- add adapter factory (`memory/backend/factory.py`).

## Phase 2: Postgres metadata adapter

- implement CRUD + batch retrieval.
- add schema migration SQL and indexes.
- add integration tests via ephemeral Postgres service.

## Phase 3: MongoDB metadata adapter

- implement equivalent metadata contract.
- add required indexes.
- add integration tests via ephemeral Mongo service.

## Phase 4: pgvector adapter

- implement vector contract using pgvector.
- validate score/order behavior parity with current search semantics.

## Phase 5: BYOA hardening

- startup health checks for configured stores.
- connection pooling/timeouts/retry policy.
- structured error mapping and observability.

## Compatibility Requirements

- API and MCP output shapes must stay stable.
- No provider-specific fields should leak into client-facing responses.
- Search behavior differences across providers must be bounded and documented.

## Testing Guidelines

## A. Contract tests (required for every provider)

Reusable suite against each adapter:

- insert/get/get_many parity
- missing id returns `None`
- duplicate id behavior is consistent
- timestamp serialization roundtrip
- metadata/topics/tags preservation

## B. Vector behavior tests

- insert/search returns deterministic shape
- top_k boundaries enforced
- score ordering consistent
- chunk metadata (`chunk_index`, `role`, `text`) preserved

## C. Integration matrix (CI)

Run matrix jobs:

- `metadata=sqlite, vector=lancedb`
- `metadata=postgres, vector=lancedb`
- `metadata=mongodb, vector=lancedb`
- `metadata=postgres, vector=pgvector` (when available)

## D. End-to-end parity tests

Run same ingestion -> search -> ask flow across matrix and assert:

- status codes/envelopes match
- citations structure matches
- topic enrichment persists
- retrieval by id parity

## E. Failure-mode tests

- invalid DSN/config fails fast
- backend unavailable errors are actionable
- timeout and reconnect behavior is bounded

## Operational BYOA Considerations

- secret management via env vars (never commit DSNs).
- TLS options for managed DBs.
- migration/version tracking for relational backends.
- index management for Mongo/Postgres.
- backup/restore responsibility documented for BYOA operators.

## Observability

Add provider-aware telemetry fields:

- `metadata_provider`, `vector_provider`
- query latency buckets
- connection errors by provider
- retries/timeouts

Expose health endpoint details (non-sensitive) for active providers.

## Acceptance Criteria

- New storage contracts adopted in domain code (no direct provider imports).
- SQLite path remains fully compatible.
- Postgres and Mongo metadata adapters pass contract + integration suites.
- BYOA config docs and examples available.
- CI matrix is green for supported providers.

## Suggested Delivery Sequence

1. Contracts + factory extraction (no behavior change).
2. Postgres metadata adapter + tests.
3. Mongo metadata adapter + tests.
4. pgvector adapter + tests.
5. Docs/examples + production hardening.
