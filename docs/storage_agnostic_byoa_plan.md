# Storage-Agnostic BYOA Plan (Adjusted)

Plan for evolving `ai-memory-hub` from SQLite-first persistence to a storage-agnostic
architecture with explicit provider capabilities, schema compatibility checks, vector dimensionality guards,
controlled fallback behavior, and dry-run support.

## Scope and Constraints

- Modify only storage contracts, provider adapters, and factory wiring.
- Do not modify domain logic semantics.
- Do not change API/MCP interface or response shapes.

## Goals

- Keep storage providers pluggable through stable interfaces.
- Make optional provider features explicit and discoverable.
- Fail fast on metadata schema incompatibility.
- Prevent vector dimension mismatch errors at runtime.
- Keep hub available when vector provider fails to initialize only when explicitly allowed.
- Support safe dry-run execution for write paths.

## Non-Goals For Implemented Baseline

- Redesigning domain workflows.
- Changing public API/MCP contracts.
- Broad provider expansion beyond SQLite, Postgres, LanceDB, pgvector, and in-memory fallback.

Provider expansion is now tracked as follow-up work in Phase 6. Those adapters must preserve
the same storage contracts, API/MCP response shapes, fallback policy, health semantics, and
dry-run behavior.

## Target Architecture

Layered storage design:

1. domain layer (unchanged behavior)
2. storage contracts (`MetadataStore`, `VectorStore`)
3. provider adapters (SQLite, Postgres, LanceDB, pgvector, in-memory fallback, others)
4. factory/wiring (startup selection, validation, fallback)

Core rule:

- Domain code depends on storage interfaces only.
- Optional capabilities are exposed but never required by domain logic.

## 1) Provider Capabilities Contract

Extend both `MetadataStore` and `VectorStore` with:

- `capabilities() -> ProviderCapabilities`

`ProviderCapabilities` fields:

- `supports_batch_insert: bool = False`
- `supports_transactions: bool = False`
- `supports_ttl: bool = False`
- `supports_tags: bool = False`
- `supports_metadata_indexing: bool = False`

Rules:

- Defaults are `False` in the base contract.
- Each adapter overrides only fields it actually supports.
- Domain layer must not rely on optional capabilities for correctness.
- Unsupported optional operations must raise deterministic `NotSupportedError`
(or equivalent storage-layer error type), never silent no-op.

Implementation notes:

- Add a typed capabilities object in contracts (dataclass or TypedDict).
- Include capabilities in adapter health/debug internals if useful, but do not alter API/MCP payloads.

## 2) Metadata Schema Versioning

Add schema version tracking for metadata providers.

Contract additions:

- `schema_version: int` property or equivalent accessor on metadata store.
- `health()` must expose effective schema version.

Adapter behavior:

- SQLite adapter returns fixed schema version (initial value `1`).
- Postgres adapter reads schema version from `schema_version` table.
- On startup/initialization, fail fast if version is missing or incompatible.

Compatibility policy:

- Define supported version set/range in factory or adapter constants.
- Raise deterministic configuration/runtime error with actionable message.

Postgres version table policy:

- Enforce single-row schema version invariant.
- Read version in a transactionally consistent way.
- Define behavior for rolling deploys with mixed app versions (document supported overlap window or fail-fast policy).

## 3) Vector Dimensionality Validation

Extend vector contract with expected dimensionality and strict validation.

Contract additions:

- `expected_dimensionality: int`
- Validation in `insert()` and `search()` against incoming vectors.

Requirements:

- Raise deterministic error when embedding size mismatches expected dimension.
- Error message must include expected and actual dimensions.
- Add startup check in factory: embedding provider dimension vs vector store `expected_dimensionality`.
- Dimension mismatch is a hard error and must not trigger vector fallback.

Runtime drift policy:

- Re-validate dimensions on every insert/search call.
- If embedding provider configuration changes at runtime, fail fast on first mismatched request with deterministic error.

## 4) Provider Fallback Strategy

Implement explicit vector-store fallback only.

Behavior:

- If configured vector store initialization fails, fallback to in-memory vector store only when explicitly enabled.
- Log warning with sanitized failure reason and fallback provider.
- Hub continues startup only under allowed fallback policy.

Rules:

- No silent degradation: fallback event must be clearly logged.
- Metadata store must not fallback; metadata initialization failure is fatal.
- Fallback scope is factory/wiring only.
- Fallback activation must be reflected in health() as degraded state.
- Fallback vector store must adopt the same `expected_dimensionality` as the failed provider.

Configuration:

```yaml
storage:
  vector:
    allow_fallback: true | false
```

Policy:

- Recommended default for production: `allow_fallback: false`.
- `allow_fallback: true` may be used for local/dev or explicitly tolerated degraded environments.

## 5) Dry-Run Mode

Add storage config flag:

```yaml
storage:
  dry_run: true | false
```

Behavior when `dry_run = true`:

- All storage writes are no-ops.
- Reads continue to operate normally.
- Vector search continues to operate normally.
- Insert operations still run validation (including dimensionality checks) but skip persistence.
- Log `DRY-RUN: write skipped` for write operations.

Rules:

- Must not break ingestion pipeline execution flow.
- Must not alter API/MCP response shapes.
- Implement via storage-layer wrappers/decorators to avoid domain changes.

Return contract in dry-run:

- Write methods must return the same shape/type as non-dry-run paths.
- For ID-returning inserts, return deterministic IDs using
  the existing application ID strategy (no storage-generated IDs required).
- Never return `None` where normal flow returns an ID/object.

## Operational Safety

- Secret-safe logging:
  - redact DSNs, credentials, tokens, and connection secrets from all storage/fallback errors.
  - include provider name, error class, and safe diagnostic context only.
- Explicit degradation signaling:
  - `health()` exposes machine-readable mode/state: `ok`, `degraded`, or `dry_run`.
  - include active metadata/vector providers and whether vector fallback is active.
- Startup policy visibility:
  - log whether `allow_fallback` and `dry_run` are enabled at startup.
  - warn on risky combinations (for example `allow_fallback=true` in production profile).
- Failure semantics:
  - metadata initialization errors are fatal.
  - vector initialization errors are fatal unless explicit fallback policy allows continuation.
  - dimension mismatch and schema incompatibility are always hard errors.
- Auditability:
  - emit one structured event for fallback activation and one for dry-run skipped writes.

## 6) Implementation Plan

### Phase 1: Contract Extensions

Status: `IMPLEMENTED`

- [x] Add `ProviderCapabilities` type and `capabilities()` to both contracts.
- [x] Add metadata schema version surface and vector expected dimensionality surface.
- [x] Add deterministic storage error types/messages for unsupported operations, schema incompatibility, and dimension mismatches.

### Phase 2: Adapter Updates (SQLite + LanceDB)

Status: `IMPLEMENTED`

- [x] Update SQLite metadata adapter:
  - [x] implement `capabilities()`
  - [x] expose fixed `schema_version = 1`
  - [x] include version in `health()`
- [x] Update LanceDB vector adapter:
  - [x] implement `capabilities()`
  - [x] expose `expected_dimensionality`
  - [x] validate dimensions in `insert()` and `search()`

### Phase 2b: Adapter Updates (Postgres + pgvector)

Status: `IMPLEMENTED`

- [x] Add Postgres metadata adapter:
  - [x] implement config selection via `providers.metadata_db: postgres`
  - [x] implement `capabilities()`
  - [x] read `schema_version` from `schema_version` table
  - [x] enforce single-row schema version invariant
  - [x] include version in `health()`
  - [x] support insert, insert-new/deduplication, append, get, get-many, conversation-hash lookup, and upstream-thread lookup
- [x] Add pgvector vector adapter:
  - [x] implement config selection via `providers.vector_db: pgvector`
  - [x] implement `capabilities()`
  - [x] expose `expected_dimensionality`
  - [x] validate dimensions in `insert()` and `search()`
  - [x] support configurable distance metric: `cosine`, `l2`, `inner_product`
  - [x] include version/dimension/provider details in `health()`
- [x] Add in-memory vector adapter as explicit local/test/fallback provider.

### Phase 3: Factory Wiring

Status: `IMPLEMENTED`

- [x] Add startup schema compatibility check for metadata store.
- [x] Add startup dimension compatibility check (embedding provider vs vector store).
- [x] Add policy-gated vector init fallback to in-memory store with warning log and preserved dimensionality.
- [x] Keep metadata init fail-fast behavior.
- [x] Surface fallback/degraded state via health.

### Phase 4: Dry-Run Wrappers

Status: `IMPLEMENTED`

- [x] Add storage-level wrappers for metadata/vector writes.
- [x] Preserve read/search behavior.
- [x] Emit `DRY-RUN: write skipped` logs consistently.
- [x] Enforce dry-run return shape parity.

### Phase 5: Tests

Unit tests for all five feature groups:

- [x] capabilities defaults and per-adapter overrides
- [x] unsupported operation deterministic errors
- [x] schema version exposure and incompatible-version fail-fast
- [x] dimension validation in vector `insert()` and `search()`
- [x] dry-run write no-op with validation + unchanged read/search semantics and return-shape parity

Integration tests (required):

Status: `IMPLEMENTED`

- [x] vector provider init failure with `allow_fallback=false` fails startup
- [x] vector provider init failure with `allow_fallback=true` activates in-memory fallback and degraded health
- [x] metadata init failure always fails startup
- [x] Postgres `schema_version` table invariants and incompatibility behavior
- [x] startup and runtime dimensionality mismatch behavior
- [x] log redaction checks for DSN/credential leakage
- [x] pgvector startup, insert/search, health, fallback, and optional live integration coverage
- [x] Postgres metadata startup, schema-version invariant, incompatibility, and optional live integration coverage

### Phase 6: Provider Coverage Backlog

Status: `PARTIAL`

Current implemented provider matrix:

- [x] Metadata: SQLite
- [x] Metadata: Postgres
- [x] Metadata: MongoDB
- [x] Vectors: LanceDB
- [x] Vectors: pgvector
- [x] Vectors: in-memory
- [x] Vectors: ChromaDB
- [x] Vectors: Qdrant
- [x] Vectors: MongoDB Atlas Vector Search

Expansion principles:

- Add one provider at a time behind existing contracts.
- Prefer provider-native SDKs only inside adapter modules.
- Keep domain logic unchanged.
- Treat local/dev providers before hosted/cloud-only providers.
- Every vector provider must support `insert()`, `search()`, `delete()`, `get_stats()`, `health()`,
  `capabilities()`, and `expected_dimensionality`.
- Every metadata provider must support current deduplication semantics before being advertised as
  a replacement for SQLite/Postgres.
- Provider-specific config must be explicit, validated, and secret-safe.

Recommended implementation order:

1. ChromaDB: simplest local-first vector expansion after LanceDB.
2. Qdrant: strong local Docker and cloud story with explicit collection dimensions.
3. MongoDB Atlas: useful metadata option plus Atlas Vector Search for users already on Mongo.
4. Elasticsearch/OpenSearch: useful for hybrid search and existing ops stacks.
5. Milvus/Zilliz: useful for larger vector deployments, more operationally complex.
6. Weaviate: useful for schema-rich vector deployments, cloud/auth behavior needs careful testing.

### Phase 6a: Provider Config Model

Status: `PARTIAL`

- [x] Extend `providers.vector_db` accepted values:
  - [x] `chromadb`
  - [x] `qdrant`
  - [ ] `milvus`
  - [ ] `weaviate`
  - [x] `mongodb_atlas`
  - [ ] `elasticsearch`
  - [ ] `opensearch`
- [x] Extend `providers.metadata_db` accepted values:
  - [x] `mongodb`
  - [ ] optional future `elasticsearch`/`opensearch` only if metadata semantics are fully mapped
- [x] Add provider-specific config sections without changing API/MCP payloads:

```yaml
storage:
  vector:
    allow_fallback: false
    distance: cosine
  vector_providers:
    chromadb:
      path: ./data/chromadb
      collection: memory_vectors
      mode: persistent # persistent | http
      host: 127.0.0.1
      port: 8000
    qdrant:
      url: http://127.0.0.1:6333
      api_key: ""
      collection: memory_vectors
    milvus:
      uri: http://127.0.0.1:19530
      token: ""
      collection: memory_vectors
    weaviate:
      url: http://127.0.0.1:8080
      api_key: ""
      class_name: MemoryVector
    mongodb_atlas:
      uri: ""
      database: ai_memory_hub
      metadata_collection: conversations
      vector_collection: memory_vectors
      vector_index: memory_vector_index
    elasticsearch:
      url: http://127.0.0.1:9200
      api_key: ""
      index: memory_vectors
    opensearch:
      url: http://127.0.0.1:9200
      username: ""
      password: ""
      index: memory_vectors
  metadata_providers:
    mongodb:
      uri: mongodb://127.0.0.1:27017
      database: ai_memory_hub
      conversations_collection: conversations
```

- [x] Validate provider-specific config at startup.
- [x] Redact provider URLs, API keys, tokens, usernames, passwords, and connection strings.
- [ ] Document environment variable names for live tests:
  - [x] `AMH_TEST_CHROMADB_URL`
  - [x] `AMH_TEST_QDRANT_URL`
  - [x] `AMH_TEST_QDRANT_API_KEY`
  - [ ] `AMH_TEST_MILVUS_URI`
  - [ ] `AMH_TEST_MILVUS_TOKEN`
  - [ ] `AMH_TEST_WEAVIATE_URL`
  - [ ] `AMH_TEST_WEAVIATE_API_KEY`
  - [x] `AMH_TEST_MONGODB_URI`
  - [x] `AMH_TEST_MONGODB_ATLAS_URI`
  - [x] `AMH_TEST_MONGODB_ATLAS_DATABASE`
  - [x] `AMH_TEST_MONGODB_ATLAS_COLLECTION`
  - [x] `AMH_TEST_MONGODB_ATLAS_INDEX`
  - [ ] `AMH_TEST_ELASTICSEARCH_URL`
  - [ ] `AMH_TEST_ELASTICSEARCH_API_KEY`
  - [ ] `AMH_TEST_OPENSEARCH_URL`
  - [ ] `AMH_TEST_OPENSEARCH_USERNAME`
  - [ ] `AMH_TEST_OPENSEARCH_PASSWORD`

### Phase 6b: Shared Provider Contract Tests

Status: `PARTIAL`

- [x] Create reusable metadata-store contract tests:
  - [x] insert returns deterministic ID
  - [x] insert-new detects duplicate `conversation_hash`
  - [x] get/get-many preserve payload shape
  - [x] upstream-thread lookup works
  - [x] schema version appears in `health()`
  - [ ] incompatible schema version fails startup
  - [ ] unsupported optional operations raise `NotSupportedError`
- [x] Create reusable vector-store contract tests:
  - [x] insert/search/delete behavior parity
  - [x] replace removes previous chunks for the same memory ID
  - [x] search output fields match current contract
  - [x] `expected_dimensionality` is exposed
  - [x] insert/search dimension mismatches raise `VectorDimensionError`
  - [x] health exposes provider and dimensions
  - [ ] unsupported optional operations raise `NotSupportedError`
- [x] Add provider fixtures with fake SDK/client objects for unit-level tests.
- [ ] Add live integration tests gated by environment variables.
- [ ] Add fallback tests for each vector provider:
  - [x] ChromaDB unavailable provider fails startup when `allow_fallback=false`
  - [x] ChromaDB unavailable provider activates in-memory fallback when `allow_fallback=true`
  - [x] ChromaDB fallback health reports `mode=degraded`
  - [x] ChromaDB fallback log redacts provider secrets

### Phase 6c: ChromaDB Vector Adapter

Status: `PARTIAL`

Target: local-first vector provider with optional HTTP client mode.

- [x] Add dependency strategy:
  - [x] optional extra: `chromadb`
  - [x] deterministic import error when package is missing
- [x] Add `ChromaDBVectorStore` adapter.
- [x] Support persistent local mode using configured path.
- [x] Support HTTP client mode using configured host/port or URL.
- [x] Use configured collection name.
- [x] Store chunk payload fields:
  - [x] `memory_id`
  - [x] `chunk_id`
  - [x] `chunk_index`
  - [x] `message_hash`
  - [x] `role`
  - [x] `text`
  - [x] `vector`
- [ ] Persist collection metadata:
  - [ ] schema version
  - [ ] expected dimensionality
  - [ ] distance metric, if supported by selected Chroma configuration
- [ ] Validate existing collection metadata at startup.
- [x] Validate dimensions on every insert/search.
- [x] Implement replace by deleting rows for `memory_id` before adding new chunks.
- [x] Normalize Chroma search distances into current `score` field.
- [x] Implement delete by `memory_id`.
- [x] Implement `get_stats()` and `health()`.
- [ ] Tests:
  - [x] fake-client contract tests
  - [ ] persistent local integration test
  - [ ] HTTP-mode smoke test when `AMH_TEST_CHROMADB_URL` is set
  - [ ] collection dimension mismatch startup failure
  - [x] fallback and redaction tests

### Phase 6d: Qdrant Vector Adapter

Status: `IMPLEMENTED`

Target: local Docker or Qdrant Cloud vector provider.

- [x] Add dependency strategy:
  - [x] optional extra: `qdrant-client`
  - [x] deterministic import error when package is missing
- [x] Add `QdrantVectorStore` adapter.
- [x] Support URL and API key configuration.
- [x] Map hub distance config to Qdrant distance:
  - [x] `cosine`
  - [x] `l2`
  - [x] `inner_product`
- [x] Create collection when missing with configured vector size/distance.
- [ ] Validate existing collection vector size and distance at startup.
- [x] Store chunk payload fields in Qdrant payload.
- [x] Use stable point IDs derived from `chunk_id` or deterministic UUID namespace.
- [x] Implement upsert for insert.
- [x] Implement replace by deleting points filtered by `memory_id`.
- [x] Implement search result normalization.
- [x] Implement delete by `memory_id` filter.
- [x] Implement `get_stats()` and `health()`.
- [ ] Tests:
  - [x] fake-client contract tests
  - [x] local/cloud live smoke when `AMH_TEST_QDRANT_URL` is set
  - [ ] collection distance/dimension mismatch failures
  - [x] API key redaction checks
  - [x] fallback behavior tests

### Phase 6e: MongoDB Metadata And Atlas Vector Search

Status: `IMPLEMENTED`

Target: MongoDB as a metadata provider, with optional Atlas Vector Search as a vector provider.

Decision points:

- [x] Decide whether first implementation is:
  - [x] metadata-only MongoDB provider
  - [ ] Atlas Vector Search only
  - [x] combined metadata + vector provider
- [x] Recommended sequence:
  - [x] implement MongoDB metadata first
  - [x] add Atlas Vector Search after metadata semantics are stable

MongoDB metadata adapter:

- [x] Add dependency strategy:
  - [x] optional extra: `pymongo`
  - [x] deterministic import error when package is missing
- [x] Add `MongoDBMetadataStore`.
- [x] Store conversations in configured database/collection.
- [ ] Add schema version document with single active version invariant.
- [ ] Create unique indexes:
  - [ ] `id`
  - [ ] `metadata.conversation_hash`
  - [ ] `(source, metadata.upstream_thread_id)` where upstream thread exists
- [x] Preserve insert, insert-new, append, get, get-many, conversation-hash lookup, and upstream-thread lookup semantics.
- [x] Ensure duplicate-key errors map to deterministic behavior.
- [x] Implement `capabilities()` and `health()`.
- [ ] Tests:
  - [x] fake-client metadata contract tests
  - [x] optional live smoke when `AMH_TEST_MONGODB_URI` is set
  - [ ] schema version invariant tests
  - [x] duplicate/deduplication parity tests
  - [ ] URI credential redaction tests

Atlas Vector Search adapter:

- [x] Add `MongoDBAtlasVectorStore`.
- [ ] Validate configured vector search index exists and is queryable.
- [ ] Validate index dimensions and similarity mode where Atlas exposes them.
- [x] Store vector documents with chunk payload fields.
- [x] Implement insert/upsert, replace, search, delete, stats, health.
- [ ] Define readiness behavior while Atlas search indexes are still building.
- [ ] Tests:
  - [x] fake-client vector contract tests
  - [x] optional Atlas live smoke when `AMH_TEST_MONGODB_ATLAS_URI` is set
  - [ ] index missing/not-ready startup failures
  - [ ] dimension mismatch tests
  - [x] fallback behavior tests

### Phase 6f: Elasticsearch And OpenSearch Vector Adapters

Status: `NOT IMPLEMENTED`

Target: vector search for users already running Elastic/OpenSearch, with a path toward hybrid retrieval.

Shared adapter requirements:

- [ ] Add dependency strategy:
  - [ ] optional extra: `elasticsearch`
  - [ ] optional extra: `opensearch-py`
  - [ ] deterministic import error when package is missing
- [ ] Add `ElasticsearchVectorStore`.
- [ ] Add `OpenSearchVectorStore`.
- [ ] Support URL plus API key or username/password auth.
- [ ] Create index when missing.
- [ ] Store mapping metadata:
  - [ ] schema version
  - [ ] dense-vector field dimensions
  - [ ] similarity/distance mode
- [ ] Validate existing mapping at startup.
- [ ] Store chunk payload fields as document fields.
- [ ] Use stable document IDs derived from `chunk_id`.
- [ ] Implement replace by deleting documents filtered by `memory_id`.
- [ ] Implement vector search:
  - [ ] Elasticsearch `knn` or script-score path based on supported version
  - [ ] OpenSearch k-NN query path based on installed plugin/version
- [ ] Normalize provider scores/distances into current `score` field.
- [ ] Explicitly document refresh/read-after-write behavior.
- [ ] Implement `get_stats()` and `health()`.
- [ ] Tests:
  - [ ] fake-client contract tests for both providers
  - [ ] optional live smoke for Elasticsearch
  - [ ] optional live smoke for OpenSearch
  - [ ] mapping mismatch startup failures
  - [ ] auth redaction checks
  - [ ] fallback behavior tests

Hybrid-search follow-up:

- [ ] Add provider capability for keyword/hybrid search only after API semantics are designed.
- [ ] Keep initial adapter vector-only to preserve existing API/MCP behavior.

### Phase 6g: Milvus/Zilliz Vector Adapter

Status: `NOT IMPLEMENTED`

Target: larger vector deployments via self-hosted Milvus or managed Zilliz.

- [ ] Add dependency strategy:
  - [ ] optional extra: `pymilvus`
  - [ ] deterministic import error when package is missing
- [ ] Add `MilvusVectorStore`.
- [ ] Support URI and token configuration.
- [ ] Map hub distance config to Milvus metric type.
- [ ] Create collection when missing.
- [ ] Define collection schema:
  - [ ] primary key
  - [ ] `memory_id`
  - [ ] `chunk_id`
  - [ ] `chunk_index`
  - [ ] `message_hash`
  - [ ] `role`
  - [ ] `text`
  - [ ] vector field with configured dimension
- [ ] Validate existing collection schema/dimension/metric at startup.
- [ ] Create or validate vector index.
- [ ] Ensure collection load/readiness before search.
- [ ] Implement insert/upsert behavior.
- [ ] Implement replace by deleting rows filtered by `memory_id`.
- [ ] Implement search result normalization.
- [ ] Implement delete by `memory_id`.
- [ ] Implement `get_stats()` and `health()`.
- [ ] Tests:
  - [ ] fake-client contract tests
  - [ ] optional Milvus/Zilliz live smoke when `AMH_TEST_MILVUS_URI` is set
  - [ ] collection not-loaded readiness tests
  - [ ] metric/dimension mismatch startup failures
  - [ ] token redaction checks
  - [ ] fallback behavior tests

### Phase 6h: Weaviate Vector Adapter

Status: `NOT IMPLEMENTED`

Target: schema-rich vector deployments with self-hosted or cloud Weaviate.

- [ ] Add dependency strategy:
  - [ ] optional extra: `weaviate-client`
  - [ ] deterministic import error when package is missing
- [ ] Add `WeaviateVectorStore`.
- [ ] Support URL and API key configuration.
- [ ] Use externally supplied vectors from the hub embedding provider.
- [ ] Disable or avoid provider-side vectorization for stored chunks unless explicitly configured later.
- [ ] Create class/collection when missing.
- [ ] Validate existing class schema:
  - [ ] vector dimensions where available
  - [ ] distance metric
  - [ ] required properties
  - [ ] schema version marker
- [ ] Store chunk payload properties.
- [ ] Use stable object UUIDs derived from `chunk_id`.
- [ ] Implement replace by deleting objects filtered by `memory_id`.
- [ ] Implement near-vector search.
- [ ] Normalize Weaviate distances/scores into current `score` field.
- [ ] Implement delete, stats, and health.
- [ ] Tests:
  - [ ] fake-client contract tests
  - [ ] optional live smoke when `AMH_TEST_WEAVIATE_URL` is set
  - [ ] schema mismatch startup failures
  - [ ] API key/header redaction checks
  - [ ] fallback behavior tests

### Phase 6i: Documentation And Examples

Status: `PARTIAL`

- [x] Update architecture supported-provider matrix after each provider lands.
- [x] Add config examples for ChromaDB.
- [x] Add config examples for Qdrant.
- [x] Add config examples for MongoDB metadata and MongoDB Atlas Vector Search.
- [ ] Add local Docker Compose snippets for:
  - [ ] Qdrant
  - [ ] ChromaDB HTTP server
  - [ ] Milvus
  - [ ] Weaviate
  - [ ] Elasticsearch
  - [ ] OpenSearch
- [ ] Add cloud setup notes for:
  - [x] Qdrant Cloud
  - [ ] Zilliz
  - [x] MongoDB Atlas Vector Search
  - [ ] Weaviate Cloud
  - [ ] Elastic Cloud
- [ ] Document provider-specific limitations:
  - [ ] consistency/read-after-write
  - [ ] index build readiness
  - [ ] score interpretation
  - [ ] supported distance metrics
  - [ ] local vs hosted feature differences

### Phase 6 Acceptance Criteria

- [x] `providers.vector_db` can select every implemented vector provider from config.
- [x] `providers.metadata_db` can select every implemented metadata provider from config.
- [ ] API and MCP response shapes are unchanged for all providers.
- [ ] Domain ingestion/search code remains provider-agnostic.
- [x] Each provider passes shared contract tests.
- [ ] Each provider has fallback, health, dimensionality, and redaction tests.
- [x] Live tests are optional and skipped unless provider-specific environment variables are set.
- [x] Missing optional provider dependencies produce actionable startup errors.
- [ ] Provider docs include a minimal config example and known limitations.

### Phase 7: Operational Hardening Backlog

Status: `PARTIAL`

- [x] Redact DSNs, passwords, tokens, and API keys in fallback logs.
- [x] Expose machine-readable runtime mode: `ok`, `degraded`, or `dry_run`.
- [x] Expose active/requested vector provider and fallback state in runtime health.
- [x] Treat metadata initialization errors as fatal.
- [x] Treat vector initialization errors as fatal unless `storage.vector.allow_fallback=true`.
- [x] Treat schema incompatibility and vector dimension mismatch as hard errors.
- [ ] Log `allow_fallback` and `dry_run` startup policy consistently even when disabled.
- [ ] Warn when `allow_fallback=true` is used under a production profile.
- [ ] Emit structured audit events for fallback activation and dry-run skipped writes, not only warning strings.
- [ ] Consider changing the default `storage.vector.allow_fallback` to `false` for production-oriented configs.

## Deliverables

- Updated storage contracts (`MetadataStore`, `VectorStore`) with capabilities/version/dimensionality surfaces.
- Updated SQLite, Postgres, LanceDB, pgvector, and in-memory adapters.
- Factory fallback + startup validation wiring.
- Dry-run wrappers for write paths.
- Unit + integration tests covering all required adjustments and safety guarantees.

## Acceptance Criteria

- Domain layer remains provider-agnostic and does not depend on optional capabilities.
- API/MCP interfaces and response shapes remain unchanged.
- Metadata store fails fast on schema incompatibility.
- Vector dimension mismatches fail deterministically with clear errors.
- Vector provider init failures fallback only when explicitly allowed and are clearly logged.
- Fallback/degraded mode is machine-visible in health output.
- Dry-run mode preserves pipeline flow while skipping persistence writes with shape-parity responses.
- Logs and errors are secret-safe by default.
