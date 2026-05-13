# ai-memory-hub — roadmap

Structured plan for a **local-first** memory engine that unifies AI conversations into one searchable, RAG-ready knowledge hub.

**How to use this doc:** work **phases in order** or parallelize **within** a phase when tasks are independent. Each phase ends with a **milestone** you can demo or ship.

**Related docs:** [Architecture](architecture.md) · [Agent integration](agents.md) · [Project overview](../README.md)

---

## At a glance (humans)

| Phase | Focus | Milestone (one line) |
|-------|--------|----------------------|
| 1 | MVP: ChatGPT-only ingestion, normalize, store, search, RAG | ChatGPT export → search + `/ask` over your memory |
| 2 | Multi-source ingestion | One hub across platforms, including those without official exports (e.g. Copilot) |
| 3 | Intelligence: summaries, topics, timelines, consolidation | Knowledge engine, not just storage |
| 4 | UI & DX | Dashboard + SDKs + packaging |
| 5 | Agent integration | LangGraph, Llama Stack, Assistants, MCP as memory provider |
| 6 | Advanced memory | Graphs, decay, shared memory, plugins |
| 7 | Optional cloud | Encrypted sync, multi-device, backup (opt-in only) |

---

## At a glance (agents)

When planning or estimating work, treat phases as **ordered capability layers**:

- **Phase 1** defines the minimum **pipeline**: **ChatGPT-only** ingestion (official ZIP export) → **unified schema** → **vector + SQLite** → **HTTP** `/search`, `/conversation/{id}`, `/ask`.
- **Phase 2** adds **platform-specific ingestion** (Gemini Takeout, Copilot workarounds, Claude HTML, local LLM logs); **schema and storage stay stable** if normalization boundaries are respected.
- **Phase 3** is **offline/batch intelligence** on top of stored chunks (summaries, clustering, timelines).
- **Phase 4** is **presentation and distribution** (UI, SDKs, OpenAPI, pip).
- **Phase 5** aligns with [agents.md](agents.md): **tools** wrapping the same API; **MCP** maps to `memory.*` operations.
- **Phases 6–7** are **extensions**; **local-first** remains default until Phase 7 is explicitly enabled.

---

## Phase 1 — Core MVP (Minimum Viable Product)

**Goal:** A working memory engine with ingestion, storage, search, and RAG.

### 1.1 Ingestion (ChatGPT only for MVP)

- [ ] ChatGPT export ingestion (ZIP → JSON → normalized schema)

    ChatGPT provides an official, structured export format — the easiest ingestion target and the one to implement first for MVP.

- [ ] Basic metadata extraction (timestamps, roles, source)
- [ ] Chunking long conversations for embedding

### 1.2 Normalization

- [ ] Unified conversation schema
- [ ] Message role normalization
- [ ] Basic topic tagging (LLM or regex-based)

### 1.3 Storage

- [ ] Vector store (Chroma or LanceDB)
- [ ] Metadata DB (SQLite)
- [ ] Embedding pipeline (OpenAI or Llama Stack)

### 1.4 Query API

- [ ] `POST /search` — semantic search
- [ ] `GET /conversation/{id}` — retrieve conversation
- [ ] `POST /ask` — RAG endpoint

### 1.5 CLI (optional)

- [ ] `aimh ingest <file>`
- [ ] `aimh search "<query>"`

**Milestone:** Ingest ChatGPT exports, search them semantically, and ask questions over your own memory.

---

## Phase 2 — Multi-source ingestion

**Goal:** Support multiple AI platforms with different ingestion requirements.

### 2.1 Gemini

- [ ] Google Takeout parser
- [ ] Thread reconstruction

### 2.2 Copilot (no official export — custom ingestion)

Copilot does not provide a structured export comparable to ChatGPT. Ingestion must use one or more of the following strategies:

- [ ] **Manual paste importer** — user pastes a Copilot chat; parser extracts roles and messages.

- [ ] **VS Code Copilot logs (optional)** — parse local `.jsonl` logs when available.

- [ ] **Future: Copilot API ingestion** — if Microsoft exposes conversation-history APIs, add a dedicated ingestion module.

### 2.3 Claude

- [ ] HTML export parser
- [ ] Conversation grouping

### 2.4 Local LLMs

- [ ] Ollama conversation logs
- [ ] LM Studio logs
- [ ] Llama Stack logs

**Milestone:** ai-memory-hub becomes a unified memory layer for all your AI tools, even those without official export formats.

---

## Phase 3 — Intelligence layer

**Goal:** Higher-level reasoning and memory organization.

### 3.1 Summaries

- [ ] Per-conversation summaries
- [ ] Per-topic summaries
- [ ] Daily/weekly digest generation

### 3.2 Topic extraction

- [ ] LLM-based topic labeling
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

---

## Summary

Evolution path:

1. **MVP** — ingest, store, search, RAG
2. **Multi-source ingestion**
3. **Intelligence layer**
4. **UI and developer experience**
5. **Agent integration**
6. **Advanced memory features**
7. **Optional sync**

Phases stack modularly so scope stays focused and components remain swappable (see [architecture.md](architecture.md)).
