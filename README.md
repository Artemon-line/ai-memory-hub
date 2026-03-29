# ai-memory-hub

A **local-first**, extensible memory engine that unifies AI conversations (ChatGPT, Gemini, Copilot, Claude, local LLMs) into one **searchable**, **RAG-ready** knowledge hub.

It ingests chats, normalizes them, embeds them, stores them, and lets you query that history with **semantic search** or **retrieval-augmented generation** — a personal memory layer you control.

**Goal:** keep ideas, plans, and insights findable across tools and sessions, without sending your history to a third party by default.

---

## Features

### Multi-source ingestion

Import or capture conversations from:

- ChatGPT (ZIP export)
- Gemini (Google Takeout)
- Claude
- Microsoft Copilot
- Local LLMs (Ollama, Llama Stack, LM Studio)
- Browser copy/paste or extensions

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
                |  (ChatGPT, Gemini…)   |
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

For how **AI agents** should call the hub (search / retrieve / ask), see [docs/agents.md](docs/agents.md).

---

## Roadmap

### MVP

- [ ] ChatGPT ingestion
- [ ] Unified schema
- [ ] Vector store (Chroma or LanceDB)
- [ ] Semantic search endpoint
- [ ] RAG endpoint

### Phase 2

- [ ] Gemini ingestion
- [ ] Copilot ingestion
- [ ] Topic clustering
- [ ] Timeline reconstruction
- [ ] UI dashboard

### Phase 3

- [ ] MCP provider
- [ ] Agent memory integration
- [ ] Plugin system
- [ ] Local embeddings

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
