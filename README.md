# ai-memory-hub

[![ci](https://github.com/Artemon-line/ai-memory-hub/actions/workflows/pipeline.yml/badge.svg?branch=main)](https://github.com/Artemon-line/ai-memory-hub/actions/workflows/pipeline.yml)
[![Python 3.14+](https://img.shields.io/badge/python-3.14%2B-blue.svg)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

Give AI agents a local-first, MCP-native memory backend for conversation ingestion,
semantic search, retrieval, and ask-over-memory.

`ai-memory-hub` stores structured conversations behind a unified JSON schema, embeds
message chunks, and exposes the same memory operations through both FastAPI and
Model Context Protocol (MCP). It is built for agents and developer tools that need
persistent memory without depending on one hosted memory platform.

## Features

- Local-first memory storage with SQLite and LanceDB defaults
- MCP-native interface for agent clients, including tools, resources, templates,
  and prompts
- FastAPI endpoints for direct HTTP ingestion, search, retrieval, and ask flows
- Semantic retrieval over embedded conversation chunks
- Conversation-aware result grouping with provenance metadata
- Optional token-budgeted ask responses and token-aware ingestion chunking
- Configurable embeddings provider: local OpenAI-compatible endpoint or OpenAI
- Pluggable storage choices: SQLite or Postgres metadata, LanceDB, PGVector, or
  in-memory vectors
- JSON schema validation before storage
- Dry-run mode for integration testing without writes
- Optional in-memory vector fallback when LanceDB or PGVector startup fails
- Content-hash redaction in API and MCP responses

## Installation

Clone the repository and install the development environment with `uv`:

```bash
git clone https://github.com/Artemon-line/ai-memory-hub.git
cd ai-memory-hub
uv sync --dev
```

The project requires Python 3.14 or newer.

For Postgres metadata support, install the optional extra:

```bash
uv sync --dev --extra postgres
```

For exact OpenAI-compatible token counting with `tiktoken`, install the tokenizer
extra:

```bash
uv sync --dev --extra tokenizer
```

## Quick Start

Start the API and MCP server:

```bash
uv run aim serve --host 127.0.0.1 --port 8000
```

Insert a conversation:

```bash
curl -X POST http://127.0.0.1:8000/memory/insert \
  -H "Content-Type: application/json" \
  -d '{
    "source": "codex",
    "timestamp": "2026-05-17T00:00:00Z",
    "messages": [
      {"role": "user", "text": "Remember that I prefer local-first tools."},
      {"role": "assistant", "text": "Stored."}
    ],
    "metadata": {
      "imported_at": "2026-05-17T00:00:00Z",
      "tags": ["preferences"]
    }
  }'
```

Search memory:

```bash
curl -X POST http://127.0.0.1:8000/memory/search \
  -H "Content-Type: application/json" \
  -d '{"query": "local-first tools", "top_k": 5}'
```

Ask over memory:

```bash
curl -X POST http://127.0.0.1:8000/memory/ask \
  -H "Content-Type: application/json" \
  -d '{"question": "What tool preference did the user mention?", "top_k": 5}'
```

## How It Works

```text
Conversation JSON
  -> Schema Validation
  -> Chunk Messages or Token Windows
  -> Embed Chunks
  -> Store Metadata + Vectors
  -> Search / Retrieve / Ask
```

The hub expects structured conversation JSON. It does not scrape exports or parse
HTML directly. A caller, importer, or agent formats source messages into the shared
schema, then sends them through the API or MCP interface.

## API Endpoints

| Method | Path | Purpose |
| --- | --- | --- |
| `POST` | `/memory/insert` | Validate and store a conversation |
| `POST` | `/memory/search` | Return ranked semantic matches |
| `POST` | `/memory/retrieve` | Retrieve a stored conversation by ID |
| `POST` | `/memory/ask` | Build an answer from retrieved memory |
| `GET` | `/health` | Liveness endpoint with redacted runtime health |
| `GET` | `/ready` | Readiness endpoint for container orchestration |

## CLI

Use `python -m memory.cli` during development, or the packaged `aim` console
script after installation.

```bash
python -m memory.cli ingest conversation.json --json
python -m memory.cli search "local-first tools" --top-k 5 --json
python -m memory.cli retrieve <MEMORY_ID> --json
python -m memory.cli ask "What did I store about local-first tools?" --top-k 5 --json
python -m memory.cli serve --host 127.0.0.1 --port 8000
```

Shared options include `--config <path>`, `--json`, `--quiet`, and `--verbose`.
`search` also supports `--source`, `--date-from`, `--date-to`, repeated
`--tags`, and `--result-mode chunks|compact|conversations`.

Diagnostics:

```bash
python -m memory.cli tokenizer-check --json
python -m memory.cli health --json
python -m memory.cli config-show --json
python -m memory.cli storage-check --json
```

Fact review helpers:

```bash
python -m memory.cli fact-search --subject user --json
python -m memory.cli profile-get --subject user --json
python -m memory.cli fact-supersede <OLD_FACT_ID> <NEW_FACT_ID> --json
```

The same commands are available through `aim` when the project is installed in
an environment that exposes console scripts.

### Insert Payload

If backend-generated IDs are enabled, omit `id` and let the server assign the
canonical UUID. When the backend requires caller-supplied IDs, include an `id`:

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

### Search Result Shape

`/memory/search` returns ranked chunk matches in `results`. Each result includes:

- `id`: canonical conversation ID
- `score`: vector distance, where lower is better
- `chunk_index`, `role`, and `text`: the matched chunk
- `conversation`: the stored conversation payload
- `conversation_score`: best score for that conversation among retrieved chunks
- `conversation_match_count`: number of retrieved chunks from that conversation

Search applies conservative conversation grouping before trimming to `top_k`, so
closely matched chunks from the same conversation can surface together without
hiding strong unrelated matches.

### Ask Result Shape

`/memory/ask` returns:

- `answer`: human-readable answer built from selected matches
- `results`: the same structured retrieval hits returned by search
- `citations`: compact provenance for chunks used in the answer

Clients can consume `memory_ask` either as a direct answer or as structured
retrieval output with provenance.

Pass `max_context_tokens` to `/memory/ask` to build the answer from only the
chunks that fit the requested context budget. When budgeting is active, the
response can include diagnostics such as `context_tokens_used`,
`chunks_selected`, `chunks_dropped`, and `tokenizer_used`.

## MCP Interface

When `interfaces.mcp` is enabled, the streamable HTTP MCP endpoint is mounted at:

```text
http://127.0.0.1:8000/mcp/
```

### MCP Tools

- `memory_insert(conversation_json)`
- `memory_search(query, top_k=5, limit, cursor, source, date_from, date_to, tags)`
- `memory_retrieve(id)`
- `memory_ask(question, top_k=5, max_context_tokens=None)`

Example `tools/call` after MCP initialization:

```bash
curl -X POST http://127.0.0.1:8000/mcp/ \
  -H "Content-Type: application/json" \
  -H "Accept: application/json, text/event-stream" \
  -H "Mcp-Session-Id: <SESSION_ID>" \
  -d '{
    "jsonrpc": "2.0",
    "id": 3,
    "method": "tools/call",
    "params": {
      "name": "memory_search",
      "arguments": {
        "query": "local-first tools",
        "limit": 2,
        "cursor": "0",
        "source": "codex",
        "date_from": "2026-01-01T00:00:00Z",
        "date_to": "2026-12-31T23:59:59Z",
        "tags": ["preferences"]
      }
    }
  }'
```

### MCP Session Flow

Initialize:

```bash
curl -i -X POST http://127.0.0.1:8000/mcp/ \
  -H "Content-Type: application/json" \
  -H "Accept: application/json, text/event-stream" \
  -d '{
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
      "protocolVersion": "2025-06-18",
      "capabilities": {},
      "clientInfo": {"name": "example-client", "version": "0.1.0"}
    }
  }'
```

Use the returned `Mcp-Session-Id` for later MCP calls:

```bash
curl -X POST http://127.0.0.1:8000/mcp/ \
  -H "Content-Type: application/json" \
  -H "Accept: application/json, text/event-stream" \
  -H "Mcp-Session-Id: <SESSION_ID>" \
  -d '{"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}'
```

### MCP Resources and Templates

Resources:

- `memory://conversation/example`

Templates:

- `memory://conversation/{id}`
- `memory://search/{query}`
- `memory://timeline/{day}`

Prompts:

- `save_conversation`
- `search_memory`
- `ask_memory`
- `summarize_conversation`

## Storage Backends

### SQLite + LanceDB

The default local setup stores metadata in SQLite and vectors in LanceDB:

```yaml
providers:
  metadata_db: sqlite
  vector_db: lancedb

paths:
  data_dir: ./data
```

### Postgres Metadata

Use Postgres for conversation metadata:

```yaml
providers:
  metadata_db: postgres
  metadata_dsn: postgresql://user:password@127.0.0.1:5432/memory
  vector_db: lancedb
```

### PGVector

Use PGVector for vector storage:

```yaml
providers:
  metadata_db: postgres
  metadata_dsn: postgresql://user:password@127.0.0.1:5432/memory
  vector_db: pgvector
```

### In-Memory Vectors

Use the in-memory vector backend for tests and short-lived local experiments:

```yaml
providers:
  vector_db: memory
```

## Configuration

Configuration is loaded from `config.yaml` by default. A fuller example is
available in `example.config.yaml`.

```yaml
providers:
  embeddings: local
  embedding_model: nomic-embed-text
  embedding_dimension: 768
  metadata_db: sqlite
  metadata_dsn: ""
  vector_db: lancedb

storage:
  dry_run: false
  allow_trusted_appends: false
  metadata_schema_versions: [1]
  vector:
    allow_fallback: true
    distance: cosine

tokenizer:
  enabled: false
  encoding: cl100k_base

ask:
  max_context_tokens: 2000

retrieval:
  vector_score_threshold: 7.5
  keyword_enabled: true
  keyword_candidate_limit: 50
  keyword_weight: 0.25
  metadata_weight: 0.15
  candidate_multiplier: 3

chunking:
  strategy: message  # message | token
  max_tokens: 800
  overlap_tokens: 80

schema:
  file: ./memory/schema/conversation.schema.json

interfaces:
  mcp: true
  api: true

paths:
  data_dir: ./data
  logs_dir: ./logs

openai:
  base_url: http://localhost:11434/v1
  api_key: dummy_key
  model: gpt-4.1
```

The default `chunking.strategy: message` keeps one chunk per normalized message.
Set `chunking.strategy: token` to split long messages into token windows with
`chunking.max_tokens` and `chunking.overlap_tokens`. Token chunking is opt-in and
uses `tiktoken` when available, with a deterministic local heuristic fallback.

Retrieval first gathers vector candidates, filters low-confidence vector matches
with `retrieval.vector_score_threshold`, then applies deterministic keyword and
metadata reranking. `retrieval.keyword_enabled` also allows exact keyword matches
from metadata storage to supplement vector candidates when the active metadata
store supports text lookup.

`tokenizer.encoding` is an encoding name such as `cl100k_base`, not a model file
path. ai-memory-hub does not download tokenizer files itself. When the optional
`tiktoken` extra is installed, `tiktoken` resolves and caches the encoding data.
For persistent or offline deployments, set `TIKTOKEN_CACHE_DIR` to a writable
directory and prewarm the cache during setup:

```bash
TIKTOKEN_CACHE_DIR=./data/tiktoken-cache uv run python -c "import tiktoken; tiktoken.get_encoding('cl100k_base')"
```

Check which tokenizer path will be used:

```bash
uv run python -m memory.cli tokenizer-check --json
```

The diagnostic reports `tokenizer_used` as either `tiktoken:<encoding>` or
`heuristic`, along with the active cache environment when one is configured.

## Native Clients

Register `ai-memory-hub` as an MCP server in clients such as Codex, VS Code, or
Cursor, then point the client at:

```text
http://127.0.0.1:8000/mcp/
```

The same memory operations remain available through HTTP API endpoints, so agent
clients and direct service clients can share one backend.

## Testing

Run the full local suite:

```bash
uv run pytest
```

Run storage tests only:

```bash
uv run pytest tests/integration/test_storage_features.py
```

Run the lightweight token-budget benchmark:

```bash
uv run python -m memory.benchmarks.token_budget --iterations 200
```

The benchmark uses a fixed in-memory corpus and reports JSON metrics for default
ask latency, request-budgeted ask latency, config-enabled ask latency, selected
context size, and token chunking output. Add `--max-budgeted-ratio` or
`--max-config-enabled-ratio` when you want the command to fail on a local
slowdown threshold.

Validate retrieval quality against representative local memory queries:

```bash
uv run python -m memory.benchmarks.retrieval_quality
```

The retrieval quality evaluator compares tuned retrieval defaults against a
vector-only baseline on representative project-memory conversations. It reports
`precision_at_1`, `recall_at_k`, and MRR, and exits non-zero if thresholds fail.

Measure repeated operations before adding or resizing caches:

```bash
uv run python -m memory.benchmarks.cache_candidates --iterations 1000
```

The cache-candidate benchmark reports JSON timings for schema loading, schema
compatibility checks, config parsing, and token counting, plus guidance for when
each operation is worth caching. Add `--profile-out cache-candidates.prof` to
write cProfile stats for deeper inspection.

Default local runs do not require Postgres or PGVector. Live Postgres/PGVector
tests are skipped unless `AMH_TEST_POSTGRES_DSN` is set.

Container CI also verifies:

- `Containerfile` linting with Hadolint
- image build from the checked-in `Containerfile`
- startup through the packaged `aim serve` command
- `/ready` readiness
- MCP initialize at `/mcp/`
- non-root runtime behavior with an arbitrary UID in root group
- writable `/app/data`, `/app/logs`, and tokenizer cache paths

To run live Postgres and PGVector tests locally, start a PGVector-enabled
Postgres container:

```bash
docker run --name aim-pgvector-test \
  -e POSTGRES_USER=test \
  -e POSTGRES_PASSWORD=test \
  -e POSTGRES_DB=memory \
  -p 5432:5432 \
  -d pgvector/pgvector:pg16
```

Then run the live checks:

```bash
export AMH_TEST_POSTGRES_DSN="postgresql://test:test@127.0.0.1:5432/memory"
uv run pytest -q tests/integration/test_storage_features.py -k "postgres_live_integration_when_dsn_provided or postgres_schema_version or pgvector_live_integration_when_dsn_provided or runtime_postgres_pgvector_live_integration_when_dsn_provided"
```

Clean up the container:

```bash
docker rm -f aim-pgvector-test
```

## Containers

Build the local image:

```bash
docker build -t ai-memory-hub:local -f Containerfile .
```

Run the default image locally:

```bash
docker run --rm -p 8000:8000 ai-memory-hub:local
```

The image exposes the API and MCP service on port `8000` and starts with:

```bash
/app/.venv/bin/aim serve --host 0.0.0.0 --port 8000
```

Container images run as a non-root user by default and are built so OpenShift can
override the runtime UID while keeping root-group write access to `/app/data`,
`/app/logs`, and `TIKTOKEN_CACHE_DIR`. Use `/ready` for readiness probes and
`/health` for liveness probes.

Kubernetes probe example:

```yaml
readinessProbe:
  httpGet:
    path: /ready
    port: 8000
livenessProbe:
  httpGet:
    path: /health
    port: 8000
```

For persistent local container data, mount `/app/data` and optionally
`/app/logs`. For custom configuration, mount a config file and start with
`aim serve --config <path>`.

For a reusable Docker/Podman Compose setup that runs ai-memory-hub with Postgres
metadata and PGVector vectors, see:

```bash
cd examples/postgres/pgvector
docker compose up --build
```

The example binds ai-memory-hub on host port `8000` for LAN clients. Use
`http://<HOST_LAN_IP>:8000/mcp/` from another PC on the same network.
It also enables tokenizer budgeting with the optional `tiktoken` extra.
For remote Ollama embeddings, see
`examples/postgres/pgvector/config.ollama.yaml`.
When Ollama runs on the same host as Docker, the example reaches it from the
container through `host.docker.internal`.

The current CI builds and smokes container images but does not publish them.
Future image publishing is intended for GitHub Releases, with no required repo
secrets for the current build-only container CI path. Docker Hub publishing will
require `DOCKERHUB_USERNAME`, `DOCKERHUB_TOKEN`, and optionally
`DOCKERHUB_NAMESPACE`.

## Project Structure

```text
memory/
  api/          FastAPI application and HTTP routes
  backend/      metadata stores, vector stores, redaction, dry-run wrappers
  ingestion/    schema validation and ingestion agents
  interfaces/   MCP server implementation
  schema/       conversation JSON schema
docs/           architecture notes, plans, and improvement documents
tests/          unit, integration, and end-to-end tests
```

## Security Considerations

- Store real API keys outside committed configuration files.
- Use secure database connection strings and network access controls.
- Treat stored conversations as sensitive application data.
- Review retention, deletion, and redaction requirements before production use.
- Add authentication before exposing the API or MCP endpoint beyond localhost.

## Documentation

- [Architecture](docs/architecture.md)
- [Agent integration](docs/agents.md)
- [MCP plan](docs/mcp_plan.md)
- [Release, container, and docs publishing plan](docs/release_container_docs_plan.md)
- [Observability, logging, and telemetry plan](docs/observability_logging_telemetry_plan.md)
- [Bearer/API-key auth plan](docs/bearer_api_key_auth_plan.md)
- [Roadmap](docs/roadmap.md)
- [Improvements](docs/improvements.md)

## Contributing

Contributions are welcome.

1. Fork the repository.
2. Create a feature branch.
3. Run `uv run pytest`.
4. Commit your changes.
5. Open a pull request.

## License

This project is licensed under the MIT License. See [LICENSE](LICENSE) for
details.
