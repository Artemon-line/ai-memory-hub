# ai-memory-hub

[![ci](https://github.com/Artemon-line/ai-memory-hub/actions/workflows/pipeline.yml/badge.svg?branch=main)](https://github.com/Artemon-line/ai-memory-hub/actions/workflows/pipeline.yml)

`ai-memory-hub` is a local-first memory backend for AI conversation storage and retrieval.

## What It Exposes

- FastAPI endpoints:
  - `POST /memory/insert`
  - `POST /memory/search`
  - `POST /memory/retrieve`
- FastMCP HTTP bridge (mounted at `/mcp` when enabled)
- FastMCP tools:
  - `memory.insert`
  - `memory.search`
  - `memory.retrieve`

## Ingestion Flow

```python
def ingest_messages(conversation_json):
    validate_json(conversation_json)
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

## Run Locally

```bash
uv sync --dev
uv run pytest -q
uv run uvicorn memory.api.server:app --host 127.0.0.1 --port 8000
```

## API Examples

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

## MCP Bridge Example

When `interfaces.mcp` is enabled, the FastMCP HTTP bridge is mounted at `/mcp`.
Use a FastMCP client to invoke tool names such as `memory.insert`, `memory.search`, and `memory.retrieve` through that endpoint.

```bash
curl -X POST http://127.0.0.1:8000/mcp \
  -H "Content-Type: application/json" \
  -d '{
    "tool": "memory.insert",
    "arguments": {
      "conversation_json": {
        "id": "d9fd4c95-9cb3-4fd5-b967-3027f8863210",
        "source": "chatgpt",
        "timestamp": "2026-01-01T00:00:00Z",
        "messages": [{"role":"user","text":"hello"}],
        "metadata": {"imported_at": "2026-01-01T00:00:00Z"}
      }
    }
  }'
```

## Tests

- `tests/test_mvp_ingestion.py`
- `tests/test_mvp_ingestion_agent.py`
- `tests/test_api_endpoints.py`
- `tests/test_mcp_tools.py`

## License

MIT. See `LICENSE`.
