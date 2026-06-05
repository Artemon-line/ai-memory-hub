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

The Hub supports main paths:
**Structured (JSON)**: `ingest_messages(conversation_json)`

## Storage

- Metadata: SQLite or Postgres (`providers.metadata_db`)
- Vectors: LanceDB, PGVector, or in-memory (`providers.vector_db`)
- Startup checks:
  - metadata schema version compatibility (`storage.metadata_schema_versions`)
  - embedding/vector dimensionality compatibility
- Validation schema:
  - loaded from `schema.file` at startup
- Optional vector fallback:
  - if LanceDB or PGVector init fails and `storage.vector.allow_fallback: true`, runtime falls back to in-memory vectors
- Dry-run mode:
  - `storage.dry_run: true` skips writes while preserving read/search and response shapes

## Config

Relevant config keys:

```yaml
providers:
  embeddings: local   # openai | local
  metadata_db: sqlite # sqlite | postgres
  metadata_dsn: ""    # required for metadata_db=postgres or vector_db=pgvector
  vector_db: lancedb  # lancedb | pgvector | memory

storage:
  dry_run: false
  metadata_schema_versions: [1]
  vector:
    allow_fallback: true
    distance: cosine  # cosine | l2 | inner_product

schema:
  file: ./memory/schema/conversation.schema.json

interfaces:
  mcp: true
  api: true

paths:
  data_dir: ./data
```

## Local Storage Tests

Default local runs do not require Postgres or PGVector. Live Postgres/PGVector tests are skipped unless `AMH_TEST_POSTGRES_DSN` is set.

Run the full local suite:

```bash
uv sync --dev
uv run pytest
```

Run storage tests only:

```bash
uv run pytest tests/integration/test_storage_features.py
```

To run live Postgres + PGVector tests locally, start a PGVector-enabled Postgres container:

```bash
docker run --name aimh-pgvector-test \
  -e POSTGRES_USER=test \
  -e POSTGRES_PASSWORD=test \
  -e POSTGRES_DB=memory \
  -p 5432:5432 \
  -d pgvector/pgvector:pg16
```

Then run the same checks CI runs:

```bash
export AMH_TEST_POSTGRES_DSN="postgresql://test:test@127.0.0.1:5432/memory"
uv run pytest -q tests/integration/test_storage_features.py -k "postgres_live_integration_when_dsn_provided or postgres_schema_version or pgvector_live_integration_when_dsn_provided or runtime_postgres_pgvector_live_integration_when_dsn_provided"
```

Clean up the local container when finished:

```bash
docker rm -f aimh-pgvector-test
```

Run server:

```bash
uv sync --dev
uv run uvicorn memory.api.server:app --host 127.0.0.1 --port 8000
```

## API Usage

Insert (Structured):

```bash
curl -X POST http://127.0.0.1:8000/memory/insert \
  -H "Content-Type: application/json" \
  -d '{
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

### Search and Ask Result Shape

`/memory/search` returns ranked chunk matches in `results`.

Each result includes:

- `id`: canonical conversation ID
- `score`: vector distance, where lower is better
- `chunk_index`, `role`, and `text`: the matched chunk
- `conversation`: the stored conversation payload
- `conversation_score`: best score for the conversation among retrieved candidates
- `conversation_match_count`: number of retrieved candidate chunks from that conversation

Search applies conservative conversation grouping before trimming to `top_k`, so closely
matched chunks from the same conversation are surfaced together without suppressing
strong unrelated matches.

`/memory/ask` returns the same structured matches in `results`, plus:

- `answer`: human-readable answer built from the selected matches
- `citations`: compact provenance for the chunks used by the answer

This means clients can consume `memory_ask` either as a direct answer (`answer`) or as
structured retrieval output (`results`) with provenance (`citations`).

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

`memory_search` and `memory_ask` expose conversation-aware ranking metadata in each
result: `conversation_score` and `conversation_match_count`. `memory_ask` also returns
the useful matches in `results`, not only in `citations`, so MCP clients can reliably
inspect structured hits while still using `answer` for human-facing output.

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

Minimal valid insert payload when the backend requires the caller to supply the ID:

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

If backend-generated IDs are enabled, omit `id` entirely and let the server assign the canonical UUID.

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
