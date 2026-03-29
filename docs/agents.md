# AI agents and ai-memory-hub

This hub is a **memory layer** for AI agents: it stores conversations, supports semantic retrieval, and can answer questions with RAG over your personal history.

**Audience:** Builders wiring agents (LangGraph, Llama Stack, OpenAI Assistants, MCP, custom tools) to a single memory backend.

**Related docs:** [Roadmap](roadmap.md) · [Architecture](architecture.md)

---

## Roadmap alignment

Delivery is **phased** (see [roadmap.md](roadmap.md)):

- **Phase 1 (MVP):** End-users ingest **ChatGPT exports** first (official ZIP → normalized data). Agents still interact **only** with the hub’s **HTTP API** — they do not parse ZIP exports themselves.
- **Phase 2+:** Additional **sources** (Gemini Takeout, Claude HTML, local LLM logs, **Copilot** via paste / extension / logs / future APIs) feed the same schema once parsers exist.
- **Phase 5 (roadmap):** First-class **agent integration** (LangGraph toolkits, MCP server, templates) sits on top of the same `search` / `retrieve` / `ask` surface.

Optional MCP tools such as `memory.summarize` or `memory.timeline` assume **later** intelligence/timeline work in the roadmap; until then, agents rely on `/search` and `/ask` (and `/conversation/{id}`) as implemented.

---

## What agents can do

- Retrieve past conversations
- Search for relevant context
- Summarize historical discussions
- Extract facts, decisions, or plans
- Build timelines
- Maintain continuity across sessions

**Example prompts users might run through an agent:**

- “What did I say about upgrading my GPU last month?”
- “Find all conversations where I discussed FastAPI RAG.”
- “Summarize everything I learned about Llama Stack.”

---

## Interaction model

Agents talk to the hub **only through its HTTP API**. They do **not** read raw export files or database files directly.

| Operation | Method | Path | Role |
|-----------|--------|------|------|
| Search | `POST` | `/search` | Semantic search across stored conversations |
| Retrieve | `GET` | `/conversation/{id}` | Fetch one conversation (or message scope the API defines) |
| Ask (RAG) | `POST` | `/ask` | Retrieval-augmented answer over memory |

### Search

Semantic search over stored conversations.

```http
POST /search
Content-Type: application/json
```

```json
{
  "query": "gpu upgrade"
}
```

### Retrieve

Load a specific conversation by identifier (exact shape of `{id}` and response follows your deployed API contract).

```http
GET /conversation/{id}
```

### Ask (RAG run query + optional retrieval limits)

```http
POST /ask
Content-Type: application/json
```

```json
{
  "query": "What were my plans for the PC upgrade?",
  "top_k": 5
}
```

`top_k` controls how many chunks or hits to pull into context before generation (if your server supports it).

---

## Architecture (conceptual)

```mermaid
flowchart TB
  Agent["AI agent\n(LangGraph, MCP client, etc.)"]
  API["ai-memory-hub API\nsearch · retrieve · ask"]
  Store["Vector store + metadata"]

  Agent -->|"Tools / HTTP"| API
  API --> Store
```

Plain-text equivalent:

```
+---------------------+
|     AI agent        |
| (LangGraph, MCP…)   |
+----------+----------+
           |
           | Tools / HTTP
           v
+-----------------------------+
|     ai-memory-hub API       |
|  search / retrieve / ask    |
+----------+------------------+
           |
           v
+-----------------------------+
|   Vector store + metadata   |
+-----------------------------+
```

---

## Integration notes

- **Tools:** Expose `search`, `retrieve`, and `ask` as function/tool calls your agent can choose based on the user task.
- **MCP:** If you add an MCP server in front of this API, map each tool to the corresponding method and path above.
- **Safety:** Treat the hub like any other backend: validate inputs, handle errors and timeouts, and avoid logging full message bodies if logs are shared.

For product overview, ingestion scope by phase, and local-first setup, see the [README](../README.md) and [roadmap](roadmap.md).
