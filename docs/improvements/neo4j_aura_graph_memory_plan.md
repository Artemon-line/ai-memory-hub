# Neo4j Aura Graph Memory Plan

## Goal

Add Neo4j AuraDB as a first-class cloud graph backend for `ai-memory-hub`,
starting with vector search parity and then using Neo4j graph records to improve
relationship-heavy memory retrieval.

The important product step is not only storing vectors in AuraDB. The
game-changing step is mirroring hub graph records into Neo4j nodes and
relationships, then using graph expansion as a bounded retrieval signal with
provenance and quality gates.

## Scope

- [ ] Add Neo4j AuraDB as `providers.vector_db: neo4j`.
- [ ] Keep SQLite, Postgres, or MongoDB as the source-of-truth metadata provider
      for the first implementation.
- [ ] Store hub-generated memory chunk embeddings in Neo4j vector indexes.
- [ ] Mirror existing advanced-memory entities and relationships into Neo4j
      after vector-provider parity is proven.
- [ ] Use Neo4j graph expansion to improve relationship, dependency, project,
      tool, and contradiction recall.
- [ ] Keep API and MCP response shapes stable across providers.
- [ ] Keep graph-influenced retrieval behind existing quality gates and feature
      flags.

## Non-Goals

- [ ] Do not replace raw conversation storage with Neo4j-generated summaries or
      graph records.
- [ ] Do not make AuraDB a full `providers.metadata_db` replacement in the first
      pass.
- [ ] Do not let Neo4j graph traversal bypass auth, project ownership, shared
      memory policy, or agent filters.
- [ ] Do not expose agent-facing graph mutation tools.
- [ ] Do not log Neo4j URIs with credentials, passwords, bearer tokens, raw
      memory payloads, queries, or embeddings.
- [ ] Do not require AuraDB for local-first operation; Neo4j must remain an
      optional provider.

## Phase 1: Provider Design And Boundaries

- [ ] Use the official Python `neo4j` driver as an optional dependency.
- [ ] Treat the `neo4j/neo4j` repository as the database source, not an embedded
      dependency.
- [ ] Define `Neo4jVectorStore` as a normal implementation of the existing
      vector-store contract.
- [ ] Keep metadata in SQLite, Postgres, or MongoDB while Neo4j is vector-only.
- [ ] Define the initial Neo4j node model for memory chunks:
      `(:MemoryChunk {memory_id, chunk_id, project_id, user_id, source, text,
      embedding, ...})`.
- [ ] Define a validated identifier policy for configurable label, index, and
      property names before interpolating them into Cypher.
- [ ] Decide whether the first implementation targets Neo4j 5.x compatibility
      with `db.index.vector.queryNodes()` or current Cypher `SEARCH`, then
      document the version assumptions.

Acceptance criteria:

- [ ] The integration boundary is clear: vector provider first, graph mirror
      second, metadata provider later.
- [ ] Domain ingestion and API/MCP code stay provider-agnostic.
- [ ] Identifier interpolation is limited to validated schema identifiers.

## Phase 2: Config And Dependency Surface

- [ ] Add a `neo4j` optional extra in `pyproject.toml`.
- [ ] Add `neo4j` to `VectorProviderName` and accepted `providers.vector_db`
      values.
- [ ] Add `Neo4jVectorConfig` with explicit fields:
      - [ ] `uri`
      - [ ] `username`
      - [ ] `password`
      - [ ] `database`
      - [ ] `index`
      - [ ] `label`
      - [ ] `embedding_property`
      - [ ] optional `text_property`
      - [ ] optional `create_index`
- [ ] Add `storage.vector_providers.neo4j` to the main storage config model.
- [ ] Validate `uri` as an absolute Neo4j URI such as `neo4j+s://...` for
      AuraDB.
- [ ] Validate label, property, database, and index names with provider-specific
      helper functions.
- [ ] Ensure config and fallback errors redact URI credentials, username, and
      password.

Example target config:

```yaml
providers:
  metadata_db: sqlite
  vector_db: neo4j
  embeddings: local

storage:
  vector:
    allow_fallback: false
    distance: cosine
  vector_providers:
    neo4j:
      uri: neo4j+s://<aura-id>.databases.neo4j.io
      username: neo4j
      password: ${NEO4J_PASSWORD}
      database: neo4j
      index: memory_vector_index
      label: MemoryChunk
      embedding_property: embedding
      create_index: true
```

Acceptance criteria:

- [ ] Missing `neo4j` package produces an actionable startup error.
- [ ] Invalid provider config fails before any network write.
- [ ] Secrets are redacted in errors, logs, and health/fallback diagnostics.

## Phase 3: Neo4j Vector Store Adapter

- [ ] Add `Neo4jVectorStore` to `memory/backend/vector_store.py` or a dedicated
      provider module if the vector-store file needs splitting.
- [ ] Implement `expected_dimensionality`.
- [ ] Implement `insert(metadata_id, embeddings, replace=False)`.
- [ ] Implement `search(query_vector, top_k=5)`.
- [ ] Implement `delete(memory_id)`.
- [ ] Implement `get_stats()`.
- [ ] Implement `health()`.
- [ ] Implement `capabilities()`.
- [ ] Validate dimensions on every insert and search.
- [ ] Create or validate the Neo4j vector index with explicit dimensions and
      similarity function.
- [ ] Normalize Neo4j similarity scores into the hub's existing `score` field.
- [ ] Preserve the existing chunk result metadata shape used by `memory_search`
      and `memory_ask`.
- [ ] Use parameterized Cypher for all user and memory values.
- [ ] Keep schema identifier interpolation behind validation helpers only.
- [ ] Close Neo4j driver/session resources cleanly.

Acceptance criteria:

- [ ] Insert/search/delete behavior matches shared vector-store contract tests.
- [ ] Existing API and MCP response snapshots do not change.
- [ ] `aim storage-check` and health surfaces report provider, index, distance,
      dimensionality, and row count when available.
- [ ] Startup fails clearly if an existing index has incompatible dimensions or
      similarity function.

## Phase 4: Factory Wiring And Reindex Flow

- [ ] Import and wire `Neo4jVectorStore` in `_build_vector_store()`.
- [ ] Ensure vector fallback behavior works with `providers.vector_db: neo4j`.
- [ ] Ensure runtime health reports requested provider, effective provider, and
      fallback state.
- [ ] Verify `aim reindex --config neo4j-aura-config.yaml --json` can rebuild
      AuraDB vectors from source metadata payloads.
- [ ] Document that changing embedding provider, model, dimension, or vector
      options requires an empty AuraDB vector destination or a reindex.

Acceptance criteria:

- [ ] Existing ingestion/search code does not need Neo4j-specific branches.
- [ ] Reindex can repopulate an empty Neo4j vector index from SQLite/Postgres/
      MongoDB metadata.
- [ ] Vector fallback remains explicit and observable.

## Phase 5: Tests

- [ ] Add fake-driver/fake-session tests for `Neo4jVectorStore`.
- [ ] Add shared vector-provider contract coverage for Neo4j.
- [ ] Test dimension mismatch on insert.
- [ ] Test dimension mismatch on search.
- [ ] Test missing dependency error.
- [ ] Test invalid config values.
- [ ] Test startup failure when index metadata is incompatible.
- [ ] Test `allow_fallback=false` fails startup on Neo4j init errors.
- [ ] Test `allow_fallback=true` activates in-memory fallback and degraded
      health.
- [ ] Test secret redaction for URI, username, and password.
- [ ] Add optional live AuraDB test gated by environment variables:
      - [ ] `AMH_TEST_NEO4J_URI`
      - [ ] `AMH_TEST_NEO4J_USERNAME`
      - [ ] `AMH_TEST_NEO4J_PASSWORD`
      - [ ] `AMH_TEST_NEO4J_DATABASE`
      - [ ] optional `AMH_TEST_NEO4J_INDEX`
- [ ] Add a reindex smoke test using a fake Neo4j client or optional live gate.

Acceptance criteria:

- [ ] Focused Neo4j tests pass without network access by default.
- [ ] Live tests are skipped unless AuraDB credentials are explicitly supplied.
- [ ] Provider contract, fallback, health, and redaction behavior match existing
      providers.

## Phase 6: Documentation And Examples

- [ ] Add a Neo4j AuraDB section to `docs/storage_provider_examples.md`.
- [ ] Add Neo4j to the storage provider matrix only after implementation lands.
- [ ] Add known limitations:
      - [ ] AuraDB network latency and cloud availability affect retrieval.
      - [ ] Vector indexes are approximate nearest-neighbor search.
      - [ ] Same-transaction index visibility may differ from local stores.
      - [ ] Existing metadata remains outside Neo4j in the first phase.
- [ ] Add a minimal AuraDB config sample.
- [ ] Add a local Docker Neo4j smoke example only if it stays lightweight and
      does not distract from AuraDB support.
- [ ] Document reindex instructions for moving existing vectors into AuraDB.

Acceptance criteria:

- [ ] Users can configure AuraDB without reading implementation code.
- [ ] Docs make clear that `storage.vector_providers.neo4j` is inactive unless
      `providers.vector_db: neo4j` is selected.
- [ ] Docs recommend `storage.vector.allow_fallback: false` for production.

## Phase 7: Graph Record Mirror

- [ ] Add a Neo4j graph mirror service separate from `Neo4jVectorStore`.
- [ ] Mirror existing `graph_entities` into Neo4j nodes.
- [ ] Mirror existing `graph_relationships` into Neo4j relationships or
      relationship-record nodes.
- [ ] Link mirrored graph records to source evidence:
      - [ ] conversation ID
      - [ ] message index
      - [ ] chunk ID
      - [ ] fact ID where applicable
      - [ ] extractor metadata
- [ ] Preserve supersession, conflict, confidence, review status, and active/
      inactive state.
- [ ] Make graph mirroring idempotent.
- [ ] Add a dry-run mode for graph mirror operations.
- [ ] Add repair/rebuild command for Neo4j graph mirror from source metadata.

Acceptance criteria:

- [ ] Neo4j graph records are derived from source-of-truth metadata and can be
      rebuilt.
- [ ] Mirroring does not change API/MCP answer behavior by itself.
- [ ] Provenance is sufficient to explain any future graph-influenced retrieval
      result.

## Phase 8: Graph-Aware Retrieval With Neo4j

- [x] Existing advanced-memory plan defines graph-aware retrieval gates and
      diagnostics.
- [x] Existing graph quality gate requires measurable entity, relationship,
      provenance, and graph-retrieval quality.
- [ ] Add Neo4j-backed graph expansion as an optional retrieval candidate source.
- [ ] Keep graph expansion behind `retrieval.graph_enabled`.
- [ ] Keep graph expansion behind `retrieval.graph_quality_gate_passed`.
- [ ] Use graph matches to expand or rerank candidate memories, not replace
      vector, keyword, fact, or summary retrieval.
- [ ] Add result diagnostics showing when Neo4j graph expansion influenced
      ranking.
- [ ] Preserve compact provenance and confidence reasons in `memory_ask`.
- [ ] Add relationship-heavy evaluation cases:
      - [ ] "Which tools are tied to this project?"
      - [ ] "What changed about this preference?"
      - [ ] "Which clients were involved in this MCP failure?"
      - [ ] "What facts conflict about this configuration?"

Acceptance criteria:

- [ ] Graph expansion improves targeted relationship questions.
- [ ] Ordinary semantic retrieval does not regress below baseline.
- [ ] Graph features can be disabled without changing non-graph result shapes.
- [ ] Graph-derived context is never used without source evidence.

## Phase 9: Future Full Neo4j Metadata Provider

- [ ] Revisit only after vector provider and graph mirror are stable.
- [ ] Define whether Neo4j can satisfy the full metadata-store contract:
      - [ ] conversation insert and deterministic IDs
      - [ ] conversation hash deduplication
      - [ ] append-only updates
      - [ ] facts and profile records
      - [ ] graph records
      - [ ] summaries
      - [ ] review and forget audit records
      - [ ] users, projects, memberships, auth identities, web sessions, and
            token revocation state
      - [ ] schema versioning and migrations
- [ ] Add migration/export/import strategy before advertising Neo4j as
      `providers.metadata_db: neo4j`.
- [ ] Decide whether auth/session state should remain in SQL even if memory
      metadata moves to Neo4j.

Acceptance criteria:

- [ ] Neo4j is not advertised as a metadata provider until it passes the same
      contract and security expectations as SQLite/Postgres/MongoDB.
- [ ] Migration and rollback paths are explicit.

## Done When

- [ ] AuraDB can be selected with `providers.vector_db: neo4j`.
- [ ] Neo4j vector search passes the shared vector-provider contract.
- [ ] AuraDB live smoke tests are available but opt-in.
- [ ] Existing memory API and MCP shapes are unchanged.
- [ ] Existing metadata providers remain source of truth.
- [ ] Existing conversations can be reindexed into AuraDB.
- [ ] Graph records can be mirrored into Neo4j with provenance.
- [ ] Neo4j-backed graph expansion improves graph-memory benchmarks without
      regressing baseline retrieval.
- [ ] Docs explain setup, limitations, fallback policy, and reindex behavior.
