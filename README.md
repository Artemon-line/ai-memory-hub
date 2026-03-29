# ai-memory-hub

A **local-first**, extensible memory engine that unifies AI conversations (ChatGPT first for MVP, then Gemini, Copilot, Claude, local LLMs, and more) into one **searchable**, **RAG-ready** knowledge hub.

It ingests chats, normalizes them, embeds them, stores them, and lets you query that history with **semantic search** or **retrieval-augmented generation** — a personal memory layer you control. **Delivery is phased:** see [docs/roadmap.md](docs/roadmap.md).

**Goal:** keep ideas, plans, and insights findable across tools and sessions, without sending your history to a third party by default.

---

## Features

### Multi-source ingestion

**MVP (Phase 1):** [ChatGPT ZIP export](docs/roadmap.md) — official structured format, implemented first.

**Phase 2 and beyond** (same unified schema; see [roadmap](docs/roadmap.md)):

- Gemini (Google Takeout)
- Claude (HTML export and grouping)
- Microsoft Copilot — **no ChatGPT-like export**; capture via paste importer, optional browser extension, optional VS Code `.jsonl` logs, or future APIs
- Local LLMs (Ollama, LM Studio, Llama Stack logs)

### Unified memory schema

Conversations are normalized to a consistent shape, for example:

`source` → `timestamp` → user messages → assistant messages → `metadata`

### Semantic search

Query your full history using:

- Embeddings and vector similarity
- Metadata filters
- Time filters

### RAG over your own chats

Example questions:

- “What did I say about upgrading my GPU?”
- “Summarize all my RAG experiments.”
- “Find every conversation where I discussed Llama Stack.”

### Modular design

Planned extension points for:

- Ingestion adapters
- Vector stores
- Embedding providers
- Agents and tools
- UI

### Local-first

Data stays on your machine: no required cloud, no tracking, and no mandatory external services beyond what you opt into.

---

## Architecture (overview)

```
                +-----------------------+
                |   Ingestion modules   |
                | (ChatGPT MVP → more)  |
                +-----------+-----------+
                            |
                            v
                +-----------------------+
                |   Normalization       |
                |   Unified schema      |
                +-----------+-----------+
                            |
                            v
        +-------------------+-------------------+
        |                                       |
        v                                       v
+---------------+                      +----------------+
|  Vector store | <--- embeddings ---- | Embedding API  |
| (Chroma etc.) |                      | (OpenAI/Llama) |
+-------+-------+                      +--------+-------+
        |                                       |
        +-------------------+-------------------+
                            |
                            v
                +-----------------------+
                |   Query engine        |
                | (search + RAG + API)  |
                +-----------+-----------+
                            |
                            v
                +-----------------------+
                |   UI / agents / MCP   |
                +-----------------------+
```

---

## Planned module layout

The following paths describe the **intended** repository layout as the project grows (not everything may exist yet in a given checkout).

| Area | Paths (planned) |
|------|-------------------|
| Ingestion | `ingestion/chatgpt_parser.py`, `ingestion/gemini_parser.py`, `ingestion/copilot_parser.py` |
| Storage | `storage/vector_store.py`, `storage/metadata_db.py` |
| Inference | `inference/rag.py`, `inference/summarizer.py`, `inference/topic_extractor.py` |
| API | `backend/main.py` |

For how **AI agents** should call the hub (search / retrieve / ask), see [docs/agents.md](docs/agents.md). For system layers and data flow, see [docs/architecture.md](docs/architecture.md).

---

## Roadmap

The single source of truth for milestones and checklists is **[docs/roadmap.md](docs/roadmap.md)**. In short:

- **Phase 1 (MVP):** ChatGPT-only ingestion → unified schema → vector store + SQLite → `/search`, `/conversation/{id}`, `/ask`.
- **Phase 2:** Multi-source ingestion (Gemini, Copilot workarounds, Claude, local LLMs).
- **Later phases:** Intelligence layer, UI and SDKs, agent/MCP integration, advanced memory features, optional cloud sync.

---

## Contributing

Contributions are welcome — including ideas and feedback. Open an issue or pull request anytime.

---

## License

MIT — see [LICENSE](LICENSE).

---

## Why this exists

LLMs do not remember your past chats by default. Your conversations still contain decisions, context, and knowledge worth keeping.

**ai-memory-hub** is a personal, private memory layer that can grow with you and stay under your control.
