# ai-memory-hub

[![ci](https://github.com/Artemon-line/ai-memory-hub/actions/workflows/pipeline.yml/badge.svg?branch=main)](https://github.com/Artemon-line/ai-memory-hub/actions/workflows/pipeline.yml)

`ai-memory-hub` is a local-first, MCP-native memory backend for AI conversation
ingestion, storage, search, retrieval, and ask-over-memory.

## What It Exposes

- FastAPI endpoints:
  - `POST /memory/insert`
  - `POST /memory/search`
  - `POST /memory/retrieve`
  - `POST /memory/ask`
- FastMCP HTTP bridge (mounted at `/mcp` when enabled)
- FastMCP tools:
  - `memory_insert`
  - `memory_search`
  - `memory_retrieve`
  - `memory_ask`
- MCP implementation: `memory/interfaces/mcp_server.py`

## Ingestion Flow

```python
def ingest_messages(conversation_json):
    validate_json(conversation_json)
    enrich_topics(conversation_json)
    chunks = chunk_messages(conversation_json)
    embeddings = embed_chunks(chunks)
    metadata_id = metadata_store.insert(conversation_json)
    vector_store.insert(metadata_id, embeddings)
    return {"status": "ok", "id": metadata_id, "chunks": len(chunks)}
```

## Storage

- Metadata: SQLite
- Vectors: LanceDB (or in-memory fallback path for `pgvector` provider mode in MVP)

## Config

Relevant config keys:

```yaml
providers:
  embeddings: local   # openai | local
  vector_db: lancedb  # lancedb | pgvector

interfaces:
  mcp: true
  api: true

paths:
  data_dir: ./data
```

Run server:

```bash
uv sync --dev
uv run uvicorn memory.api.server:app --host 127.0.0.1 --port 8000
```

## API Usage

Insert:

```bash
curl -X POST http://127.0.0.1:8000/memory/insert \
  -H "Content-Type: application/json" \
  -d '{
    "id": "d9fd4c95-9cb3-4fd5-b967-3027f8863210",
    "source": "chatgpt",
    "timestamp": "2026-01-01T00:00:00Z",
    "messages": [{"role":"user","text":"hello"}],
    "metadata": {"imported_at": "2026-01-01T00:00:00Z"}
  }'
```

Search:

```bash
curl -X POST http://127.0.0.1:8000/memory/search \
  -H "Content-Type: application/json" \
  -d '{"query":"hello","top_k":5}'
```

Retrieve:

```bash
curl -X POST http://127.0.0.1:8000/memory/retrieve \
  -H "Content-Type: application/json" \
  -d '{"id":"d9fd4c95-9cb3-4fd5-b967-3027f8863210"}'
```

Ask:

```bash
curl -X POST http://127.0.0.1:8000/memory/ask \
  -H "Content-Type: application/json" \
  -d '{"question":"what did we store about MCP?","top_k":5}'
```

MCP streamable HTTP endpoint:

`http://127.0.0.1:8000/mcp/`

### JSON-RPC Session Flow

Initialize:

```bash
curl -i -X POST http://127.0.0.1:8000/mcp/ \
  -H "Content-Type: application/json" \
  -H "Accept: application/json, text/event-stream" \
  -d '{
    "jsonrpc":"2.0",
    "id":1,
    "method":"initialize",
    "params":{
      "protocolVersion":"2025-06-18",
      "capabilities":{},
      "clientInfo":{"name":"example-client","version":"0.1.0"}
    }
  }'
```

Use returned `Mcp-Session-Id` for all subsequent calls:

```bash
curl -X POST http://127.0.0.1:8000/mcp/ \
  -H "Content-Type: application/json" \
  -H "Accept: application/json, text/event-stream" \
  -H "Mcp-Session-Id: <SESSION_ID>" \
  -d '{"jsonrpc":"2.0","id":2,"method":"tools/list","params":{}}'
```

## MCP Tools

- `memory_insert(conversation_json)`
- `memory_search(query, top_k=5, limit, cursor, source, date_from, date_to, tags)`
- `memory_retrieve(id)`
- `memory_ask(question, top_k=5)`

Example `tools/call`:

```bash
curl -X POST http://127.0.0.1:8000/mcp/ \
  -H "Content-Type: application/json" \
  -H "Accept: application/json, text/event-stream" \
  -H "Mcp-Session-Id: <SESSION_ID>" \
  -d '{
    "jsonrpc":"2.0",
    "id":3,
    "method":"tools/call",
    "params":{
      "name":"memory_search",
      "arguments":{
        "query":"hello",
        "limit":2,
        "cursor":"0",
        "source":"chatgpt",
        "date_from":"2026-01-01T00:00:00Z",
        "date_to":"2026-12-31T23:59:59Z",
        "tags":["project-x"]
      }
    }
  }'
```

Minimal valid insert payload:

```json
{
  "id": "11111111-2222-4333-8444-555555555555",
  "source": "codex",
  "timestamp": "2026-05-17T00:00:00Z",
  "messages": [
    {"role": "user", "text": "remember this"},
    {"role": "assistant", "text": "stored"}
  ],
  "metadata": {
    "imported_at": "2026-05-17T00:00:00Z"
  }
}
```

## MCP Resources and Templates

- Resources:
  - `memory://conversation/example`
- Templates:
  - `memory://conversation/{id}`
  - `memory://search/{query}`
  - `memory://timeline/{day}`

List templates:

```bash
curl -X POST http://127.0.0.1:8000/mcp/ \
  -H "Content-Type: application/json" \
  -H "Accept: application/json, text/event-stream" \
  -H "Mcp-Session-Id: <SESSION_ID>" \
  -d '{"jsonrpc":"2.0","id":4,"method":"resources/templates/list","params":{}}'
```

Read resource:

```bash
curl -X POST http://127.0.0.1:8000/mcp/ \
  -H "Content-Type: application/json" \
  -H "Accept: application/json, text/event-stream" \
  -H "Mcp-Session-Id: <SESSION_ID>" \
  -d '{
    "jsonrpc":"2.0",
    "id":5,
    "method":"resources/read",
    "params":{"uri":"memory://conversation/example"}
  }'
```

## MCP Prompts

- `save_conversation`
- `search_memory`
- `ask_memory`
- `summarize_conversation`

List prompts:

```bash
curl -X POST http://127.0.0.1:8000/mcp/ \
  -H "Content-Type: application/json" \
  -H "Accept: application/json, text/event-stream" \
  -H "Mcp-Session-Id: <SESSION_ID>" \
  -d '{"jsonrpc":"2.0","id":6,"method":"prompts/list","params":{}}'
```

Get prompt:

```bash
curl -X POST http://127.0.0.1:8000/mcp/ \
  -H "Content-Type: application/json" \
  -H "Accept: application/json, text/event-stream" \
  -H "Mcp-Session-Id: <SESSION_ID>" \
  -d '{
    "jsonrpc":"2.0",
    "id":7,
    "method":"prompts/get",
    "params":{"name":"search_memory","arguments":{"query":"hello"}}
  }'
```

Get ask prompt:

```bash
curl -X POST http://127.0.0.1:8000/mcp/ \
  -H "Content-Type: application/json" \
  -H "Accept: application/json, text/event-stream" \
  -H "Mcp-Session-Id: <SESSION_ID>" \
  -d '{
    "jsonrpc":"2.0",
    "id":8,
    "method":"prompts/get",
    "params":{"name":"ask_memory","arguments":{"question":"what changed?","top_k":"5"}}
  }'
```

## Native Clients (Codex / VS Code / Cursor)

Register `ai-memory-hub` as an MCP server in your client, then point it at:

- HTTP: `http://127.0.0.1:8000/mcp/`
- or stdio command launch for this project environment

## License

MIT. See `LICENSE`.
