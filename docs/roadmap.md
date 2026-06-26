# ai-memory-hub — roadmap

Structured plan for a **local-first**, **deterministic**, **MCP-native** memory engine that unifies AI conversations into one searchable, 
RAG-ready knowledge hub with **pluggable storage backends**.

**How to use this doc:** work **phases in order** or parallelize **within** a phase when tasks are independent. 
Each phase ends with a **milestone** you can demo or ship.

**Related docs:** 
[Architecture](architecture.md) 
[Agent integration](agents.md) 
[Deterministic ingestion plan](deterministic_ingestion_plan.md) 
[Storage-agnostic BYOA plan](storage_agnostic_byoa_plan.md) 
[Browser extension capture plan](browser_extension_capture_plan.md)
[Project overview](../README.md)

---

## At a glance (humans)

| Phase | Focus | Milestone (one line) |
|-------|--------|----------------------|
| 1 | Core MVP: MCP-native ingestion, normalize, store, search, retrieve | MCP tools/resources + HTTP `/memory/*` over SQLite + LanceDB |
| 1.5 | Storage abstraction | Unified metadata/vector provider interfaces with local, hosted, and existing-stack backends |
| 2 | Multi-source ingestion | One hub across platforms, including those without official exports (e.g. Copilot) |
| 3 | Intelligence: summaries, topics, timelines, consolidation | Knowledge engine, not just storage |
| 4 | UI & DX | Dashboard + SDKs + packaging |
| 5 | Agent integration | LangGraph, Llama Stack, Assistants using MCP/API memory backend |
| 6 | Advanced memory | Graphs, decay, shared memory, plugins |
| 7 | Optional cloud | Encrypted sync, multi-device, backup (opt-in only) |

---

## At a glance (agents)

When planning or estimating work, treat phases as **ordered capability layers**:

- **Phase 1** defines the minimum **pipeline**: **schema-first ingestion** (MCP/API payloads) → **unified schema** → **embeddings** → **vector search** → **HTTP** `/memory/search`, 
`/memory/retrieve` + MCP `memory_*` tools/resources.
- **Phase 1.5** promotes storage to a **first-class subsystem**: `VectorStore` interface → LanceDB/ChromaDB/Qdrant/Milvus/Weaviate/PGVector/MongoDB Atlas/Elasticsearch/OpenSearch/Redis/Pinecone/Turbopuffer/Vespa/Typesense/in-memory providers → deterministic startup checks → fallback behavior.
- **Phase 2** adds **platform-specific ingestion** (Gemini Takeout, Copilot workarounds, Claude HTML, local LLM logs);
**schema and storage stay stable** if normalization boundaries are respected.
- Browser extension capture is a planned Phase 2-compatible ingestion option:
extension repos parse ChatGPT, Microsoft Copilot, Claude, Gemini, and similar
web UIs, then post normalized payloads to `POST /memory/insert`.
- **Phase 3** is **offline/batch intelligence** on top of stored chunks (summaries, clustering, timelines).
- **Phase 4** is **presentation and distribution** (UI, SDKs, OpenAPI, pip).
- **Phase 5** aligns with [agents.md](agents.md): external agent frameworks consume the same memory API/MCP backend with their choice of supported metadata and vector providers.
- **Phases 6–7** are **extensions**; **local-first** remains default until Phase 7 is explicitly enabled.

---

## Phase 1 — Core MVP (Minimum Viable Product)

**Goal:** A working memory engine with MCP/API ingestion, storage, search, and retrieval.

### 1.1 Ingestion (MCP/API-first for MVP)

- [x] Schema-validated ingestion payload (`conversation_json` -> normalized runtime object)
- [x] MCP tool ingestion (`memory_insert`)
- [x] HTTP ingestion (`POST /memory/insert`)

- [x] Basic metadata capture (timestamps, roles, source)
- [x] Chunking messages for embedding

### 1.2 Normalization

- [x] Unified conversation schema
- [ ] Message role normalization from non-conforming upstream sources
- [ ] Basic topic tagging

### 1.3 Storage

- [x] Vector store (LanceDB with in-memory fallback)
- [x] Metadata DB (SQLite)
- [x] Embedding pipeline (OpenAI or local deterministic provider)

### 1.4 Query API

- [x] `POST /memory/search` — semantic search
- [x] `POST /memory/retrieve` — retrieve conversation by ID
- [x] MCP `memory_search` and `memory_retrieve`

### 1.6 CLI (optional)

- [ ] `aim ingest <file>`
- [ ] `aim search "<query>"`

**Milestone:** Ingest via MCP/API, validate against unified schema, store in SQLite+vector DB, and retrieve via HTTP or MCP.

---

## Phase 1.5 — Storage abstraction and provider expansion

**Goal:** Make storage pluggable and keep every storage backend behind stable metadata and vector interfaces.

### 1.5.1 Storage abstraction layer

- [x] Define `VectorStore` interface:
  - `insert(chunks)`
  - `search(query_embedding, top_k)`
  - `delete(ids)`
  - `get_stats()`
- [x] Move LanceDB implementation behind the interface
- [x] Formalize in-memory implementation as a supported provider
- [x] Keep metadata and vector storage boundaries explicit

### 1.5.2 PGVector provider

- [x] Configure PGVector through `storage.vector_providers.pgvector.url`
- [x] Create vector table (for example, `memory_vectors`)
- [x] Support cosine, L2, and inner product distance modes
- [x] Add HNSW index creation
- [x] Add deterministic startup checks:
  - vector extension installed or installable (`CREATE EXTENSION vector`)
  - embedding dimension matches configured provider
  - schema version is compatible
  - selected distance mode is supported
- [x] Add fallback logic:
  - if PGVector init fails, fallback to in-memory vectors when enabled
  - if LanceDB init fails, fallback to in-memory vectors when enabled
  - emit clear diagnostics when fallback changes runtime behavior

### 1.5.3 Config changes

- [x] `providers.vector_db: lancedb | chromadb | qdrant | milvus | weaviate | pgvector | mongodb_atlas | elasticsearch | opensearch | redis | pinecone | turbopuffer | vespa | typesense | memory`
- [x] `providers.metadata_db: sqlite | postgres | mongodb`
- [x] `storage.vector.allow_fallback: true`
- [x] `storage.vector.distance: cosine | l2 | inner_product`

### 1.5.4 Tests

- [x] PGVector integration tests
- [x] LanceDB parity tests
- [x] In-memory parity tests
- [x] Fallback tests
- [x] Dry-run/startup validation tests

**Milestone:** ai-memory-hub supports **LanceDB**, **PGVector**, and **in-memory** vector storage with identical search behavior at the API/MCP boundary.

---

## Phase 2 — Multi-source ingestion

**Goal:** Support multiple AI platforms with different ingestion requirements.

PGVector makes large ingestion sets more practical, but ingestion code should stay storage-agnostic. Each parser emits normalized records; the configured storage backend handles persistence.

### 2.1 Gemini

- [ ] Google Takeout parser
- [ ] Thread reconstruction

### 2.2 Copilot (no official export — custom ingestion)

Copilot does not provide a structured export comparable to ChatGPT. Ingestion must use one or more of the following strategies:

- [x] **Manual paste importer** — user pastes a speaker-labelled chat; parser extracts roles and messages.

- [ ] **VS Code Copilot logs (optional)** — parse local `.jsonl` logs when available.

- [ ] **Future: Copilot API ingestion** — if Microsoft exposes conversation-history APIs, add a dedicated ingestion module.

### 2.3 Claude

- [ ] HTML export parser
- [ ] Conversation grouping

### 2.4 Local LLMs

- [ ] Ollama conversation logs
- [ ] LM Studio logs
- [ ] Llama Stack logs

### 2.5 Browser extension capture

- [ ] Keep browser extension implementations in separate repos.
- [ ] Have extensions parse platform web UIs and send normalized conversation
  JSON to the existing `POST /memory/insert` API.
- [ ] Do not parse raw HTML or DOM snapshots in the ai-memory-hub API layer.
- [ ] Add API contract tests, CORS allowlist config, bearer-token guidance, and
  trusted append guidance before recommending this path for daily use.

**Milestone:** ai-memory-hub becomes a unified memory layer for all your AI tools, even those without official export formats.

---

## Phase 3 — Intelligence layer

**Goal:** Higher-level reasoning and memory organization.

PGVector unlocks larger local memory sets, faster clustering workflows, and SQL-friendly analysis when Postgres is selected.

### 3.1 Summaries

- [ ] Per-conversation summaries
- [ ] Per-topic summaries
- [ ] Daily/weekly digest generation

### 3.2 Topic extraction

- [ ] LLM-based topic labeling
- [ ] Hosted LLM fact extractor implementation with prompt/schema tuning
- [ ] HDBSCAN clustering
- [ ] Topic graph

### 3.3 Timeline reconstruction

- [ ] Chronological view of discussions
- [ ] “What did I work on last month?”
- [ ] “Show all GPU upgrade conversations”

### 3.4 Memory consolidation

- [ ] Merge redundant conversations
- [ ] Extract stable facts
- [ ] Long-term memory store

**Milestone:** The hub behaves as a knowledge engine, not only a storage system.

---

## Phase 4 — UI and developer experience

**Goal:** Pleasant day-to-day use and integration.

### 4.1 Web UI

- [ ] MaterializeCSS dashboard
- [ ] Upload exports
- [ ] Search interface
- [ ] Conversation viewer
- [ ] RAG query console
- [ ] Fact review, edit, supersession, and audit workflows
- [ ] Vector backend diagnostics
- [ ] Storage health panel
- [ ] Embedding dimension inspector

### 4.2 Developer tools

- [ ] Python SDK
- [ ] TypeScript SDK
- [ ] API documentation (OpenAPI)

### 4.3 Packaging

- [ ] `pip install ai-memory-hub`
- [ ] Dockerfile (optional)

**Milestone:** Polished interface and clear developer path to adopt the project.

---

## Phase 5 — Agent integration

**Goal:** Make ai-memory-hub the **memory provider** for agents.

Agents should be able to choose local LanceDB, Postgres+PGVector, or ephemeral in-memory storage without changing their MCP/API integration.

### 5.1 LangGraph

- [ ] Tools for search, retrieve, ask
- [ ] Memory-aware agent examples

### 5.2 Llama Stack

- [ ] External memory provider
- [ ] Configurable memory routing

### 5.3 OpenAI Assistants

- [ ] Function calling integration
- [ ] Memory-aware assistant template

### 5.4 MCP (Model Context Protocol)

- [ ] `memory_search`
- [ ] `memory_retrieve`
- [ ] `memory.summarize`
- [ ] `memory.timeline`

**Milestone:** Any compatible agent can retrieve relevant context from your memory.

---

## Phase 6 — Advanced features

**Goal:** Long-term, richer memory behavior.

PGVector and Postgres enable future hybrid search, SQL-based memory analytics, and graph-adjacent workflows without requiring a separate hosted vector service.

### 6.1 Memory graph

- [ ] Entity extraction
- [ ] Relationship mapping
- [ ] Graph-based retrieval

### 6.2 Relevance decay

- [ ] Time-based weighting
- [ ] Importance scoring
- [ ] “Forget” low-value conversations

### 6.3 Multi-agent shared memory

- [ ] Shared memory spaces
- [ ] Agent-specific memory filters

### 6.4 Plugins

- [ ] Custom ingestion modules
- [ ] Custom embedding providers
- [ ] Custom vector stores

**Milestone:** General-purpose memory platform beyond a single-user notebook flow.

---

## Phase 7 — Optional cloud and sync

**Only if you choose to — the project stays local-first by default.**

- [ ] Encrypted cloud sync
- [ ] Multi-device support
- [ ] Backup/restore
- [ ] SQLite metadata sync
- [ ] PGVector table sync
- [ ] LanceDB bundle sync

---

## Summary

Evolution path:

1. **MVP** — ingest, store, search, RAG
2. **Storage abstraction** — supported metadata and vector providers behind stable interfaces
3. **Multi-source ingestion**
4. **Intelligence layer**
5. **UI and developer experience**
6. **Agent integration**
7. **Advanced memory features**
8. **Optional sync**

Phases stack modularly so scope stays focused and components remain swappable (see [architecture.md](architecture.md)).

Positioning:

> ai-memory-hub is the SQLite of agent memory: deterministic, local-first, pluggable, and MCP-native.
