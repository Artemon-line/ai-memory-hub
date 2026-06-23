# Technical Overview

This page keeps the technical detail that used to live in the README. The README
is intentionally short; use this page when you need implementation, operations,
or integration details.

## Installation

Clone the repository and install the development environment with `uv`:

```bash
git clone https://github.com/Artemon-line/ai-memory-hub.git
cd ai-memory-hub
uv sync --dev
```

The project requires Python 3.14 or newer.

Optional extras:

```bash
uv sync --dev --extra postgres
uv sync --dev --extra tokenizer
uv sync --dev --all-extras
```

Use provider extras when selecting optional storage SDKs such as ChromaDB,
Qdrant, Milvus, Weaviate, MongoDB, Elasticsearch, OpenSearch, Redis, Postgres,
or PGVector. Use the tokenizer extra for exact OpenAI-compatible token counting
through `tiktoken`. Use `--all-extras` for container builds or provider
compatibility checks.

## How It Works

```text
Conversation JSON
  -> Schema Validation
  -> Chunk Messages or Token Windows
  -> Embed Chunks
  -> Store Metadata + Vectors
  -> Search / Retrieve / Ask
```

The hub expects structured conversation JSON. Importers convert supported
external formats into that shared schema before using the same ingestion path.
Manual speaker-labelled transcripts are supported; export-specific parsers are
tracked in the roadmap. Browser extension capture is planned as a separate
extension-repo workflow that posts normalized web chat payloads to the existing
insert API; see the
[browser extension capture plan](browser_extension_capture_plan.md).

## API Endpoints

| Method | Path | Purpose |
| --- | --- | --- |
| `POST` | `/memory/insert` | Validate and store a conversation |
| `POST` | `/memory/search` | Return ranked semantic matches |
| `POST` | `/memory/retrieve` | Retrieve a stored conversation by ID |
| `POST` | `/memory/ask` | Build an answer from retrieved memory or facts |
| `POST` | `/memory/facts/search` | Search normalized facts |
| `POST` | `/memory/profile/get` | Return profile facts and a compact fact-based summary for a subject |
| `POST` | `/memory/facts/supersede` | Mark a fact as superseded |
| `GET` | `/health` | Liveness endpoint with redacted runtime health |
| `GET` | `/ready` | Readiness endpoint for container orchestration |

## Insert Payload

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
    "imported_at": "2026-05-17T00:00:00Z",
    "summary": "User asked the assistant to remember a preference.",
    "tags": ["preferences"]
  }
}
```

Messages may use `text` or `content`; `content` is normalized to `text`.
`metadata.summary` is optional. Use it as a short factual retrieval hint only;
raw `messages` and normalized facts remain the source of truth for answers and
citations. The server also writes deterministic generated summaries for each
conversation, its topics, and its project. Generated summaries are persisted
separately from raw messages and facts; conversation summaries are exposed back
under `metadata.generated_summary` for search, retrieve, and CLI display.
The server also writes deterministic `metadata.auto_tags` and
`metadata.tag_sources` from source, topics, entities, and fact predicates.
Manual `metadata.tags` remain authoritative and are not overwritten.
For continuing source threads, clients may send `metadata.upstream_thread_id`
or `metadata.thread_id`. The server preserves upstream IDs, derives
`metadata.thread_id` when needed, and accepts optional
`metadata.parent_conversation_id` plus `metadata.related_conversation_ids` as
conversation UUID references.

Store one complete conversation per insert. Do not split one thread into
multiple batch items. If an importer has many independent source conversations,
it should call the existing single insert path once per source conversation while
preserving each original thread/session boundary.

## Search Result Shape

`/memory/search` returns ranked chunk matches in `results`. Each result includes:

- `id`: canonical conversation ID
- `score`: vector distance, where lower is better
- `chunk_index`, `role`, and `text`: the matched chunk
- `conversation`: the stored conversation payload, including
  `metadata.generated_summary` when a server-generated conversation summary is
  available, and `metadata.auto_tags` when deterministic tags were generated
- `conversation_score`: best score for that conversation among retrieved chunks
- `conversation_match_count`: number of retrieved chunks from that conversation

Search applies conservative conversation grouping before trimming to `top_k`, so
closely matched chunks from the same conversation can surface together without
hiding strong unrelated matches.
Use `result_mode=threads` to group matching conversations by `metadata.thread_id`.

## Ask Result Shape

`/memory/ask` returns:

- `answer`: human-readable answer text. For fact-backed answers this uses
  normalized display values while preserving raw source text in stored memory and
  fact evidence.
- `results`: chunk-shaped retrieval hits. Fact-only answers can legitimately
  return an empty `results` list because the answer came from normalized facts,
  not retrieved chunks.
- `citations`: compact provenance for chunks or facts used in the answer.
- `provenance`: grouped source-conversation provenance.
- `confidence` and `confidence_reason`: confidence label plus the reason, such
  as a direct user statement, assistant statement, inferred recurring topic, or
  conflict.
- `answer_basis`: one of `direct_memory`, `fact_layer`, `mixed`, `conflict`, or
  `not_found`.
- `evidence`: stable evidence entries used by the answer. Chunk-backed answers
  return `type: "chunk"` entries; fact-backed answers return `type: "fact"`
  entries.
- `structured_evidence`: split evidence for clients that do not want to inspect
  mixed lists. `structured_evidence.facts` contains normalized fact evidence and
  `structured_evidence.results` contains chunk-shaped retrieval hits.
- `facts`: active normalized facts used by fact-backed answers, including
  `source_quality`, `confidence_reason`, `created_at`, `updated_at`,
  `last_confirmed_at`, `superseded_at`, `object_raw`, and
  `object_normalized`.

Pass `max_context_tokens` to `/memory/ask` to build the answer from only the
chunks that fit the requested context budget. When budgeting is active, the
response can include `context_tokens_used`, `chunks_selected`, `chunks_dropped`,
and `tokenizer_used`.

## CLI

Use `python -m memory.cli` during development, or the packaged `aim` console
script after installation.

```bash
python -m memory.cli ingest conversation.json --json
python -m memory.cli import manual copilot-chat.txt --source vscode-copilot --json
python -m memory.cli search "local-first tools" --top-k 5 --json
python -m memory.cli retrieve <MEMORY_ID> --json
python -m memory.cli ask "What did I store about local-first tools?" --top-k 5 --json
python -m memory.cli serve --host 127.0.0.1 --port 8000
```

Manual imports accept multiline messages labelled with common speaker names such
as `User:`, `You:`, `Human:`, `Assistant:`, `Copilot:`, `Claude:`, or `Gemini:`.
Use `-` as the file name to read the transcript from stdin.

Shared options include `--config <path>`, `--json`, `--quiet`, and `--verbose`.
`search` also supports `--source`, `--date-from`, `--date-to`, repeated `--tags`,
`--thread-id`, and `--result-mode chunks|compact|conversations|threads`.

Diagnostics:

```bash
python -m memory.cli tokenizer-check --json
python -m memory.cli health --json
python -m memory.cli config-show --json
python -m memory.cli storage-check --json
```

Local bearer-token and project administration is CLI-first. Token creation prints
the raw bearer token once; token list and revoke commands only expose stable
token ids and non-secret prefixes.

```bash
python -m memory.cli admin user create jane --display-name "Jane" --json
python -m memory.cli admin user list --json
python -m memory.cli admin token create --user jane --display-name laptop --json
python -m memory.cli admin token list --user jane --json
python -m memory.cli admin token revoke <TOKEN_ID_OR_PREFIX> --json

python -m memory.cli admin project create shared-321 --owner jane --name "Shared 321" --json
python -m memory.cli admin project list --user jane --json
python -m memory.cli admin project member add shared-321 --user carl --role writer --json
python -m memory.cli admin project member list shared-321 --json
```

Fact review helpers:

```bash
python -m memory.cli fact-search --subject user --json
python -m memory.cli fact-search --source codex --status superseded --json
python -m memory.cli profile-get --subject user --predicate owns_guitar --source-quality corrected_by_user --json
python -m memory.cli fact-supersede <OLD_FACT_ID> <NEW_FACT_ID> --json
```

## MCP Interface

When `interfaces.mcp` is enabled, the streamable HTTP MCP endpoint is mounted at:

```text
http://127.0.0.1:8000/mcp/
```

Core tools:

- `memory_validate(conversation_json)`
- `memory_insert(conversation_json)`
- `memory_search(query, top_k=5, limit, cursor, source, date_from, date_to, tags, thread_id)`
- `memory_retrieve(id)`
- `memory_ask(question, top_k=5, max_context_tokens=None, result_mode="chunks", source, date_from, date_to, tags, thread_id, project_id)`
- `memory_fact_search(subject=None, predicate=None, include_superseded=False, source, date_from, date_to, confidence, status, source_quality, freshness_from, freshness_to, project_id)`
- `memory_profile_get(subject="user", predicate, source, date_from, date_to, confidence, status, source_quality, freshness_from, freshness_to, project_id)`
- `memory_fact_supersede(fact_id, superseded_by)`

`memory_profile_get` returns `facts` plus a `summary` object. The summary is
generated from active normalized facts and includes freshness, source-quality
counts, filters, and compact fact provenance. Insert also generates
conversation, topic, and project summaries from stored message text. Generated
summaries are stored separately from raw chunks and normalized facts; the
conversation summary is returned as `metadata.generated_summary` on search and
retrieve responses.

`memory_insert` accepts one complete conversation object. There is intentionally
no bulk MCP insert tool. Clients may include a short `metadata.summary`, but
must still send the complete `messages` list.

Resources:

- `memory://conversation/example`

Templates:

- `memory://conversation/{id}`
- `memory://search/{query}`
- `memory://timeline/{day}`
- `memory://health`

Prompts:

- `save_conversation`
- `search_memory`
- `ask_memory`
- `summarize_conversation`

## MCP Session Flow

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

## Native Clients

Register `ai-memory-hub` as an MCP server in clients such as Codex, opencode,
Claude, Copilot, VS Code, or Cursor, then point the client at:

```text
http://127.0.0.1:8000/mcp/
```

The same memory operations remain available through HTTP API endpoints, so agent
clients and direct service clients can share one backend.

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

### SQLite + ChromaDB

Use ChromaDB as an optional local-first vector backend:

```yaml
providers:
  metadata_db: sqlite
  vector_db: chromadb

storage:
  vector:
    allow_fallback: false
  vector_providers:
    chromadb:
      path: ./data/chromadb
      collection: memory_vectors
```

Install the optional dependency with `uv sync --extra chromadb`.

### SQLite + Qdrant

Use Qdrant as a local Docker or Qdrant Cloud vector backend:

```yaml
providers:
  metadata_db: sqlite
  vector_db: qdrant

storage:
  vector:
    allow_fallback: false
    distance: cosine
  vector_providers:
    qdrant:
      url: http://127.0.0.1:6333
      api_key: ""
      collection: memory_vectors
```

Install the optional dependency with `uv sync --extra qdrant`.

### SQLite + Milvus

Use Milvus or Zilliz for larger vector deployments:

```yaml
providers:
  metadata_db: sqlite
  vector_db: milvus

storage:
  vector:
    allow_fallback: false
    distance: cosine
  vector_providers:
    milvus:
      uri: http://127.0.0.1:19530
      token: ""
      collection: memory_vectors
```

Install the optional dependency with `uv sync --extra milvus`.

### SQLite + Weaviate

Use Weaviate for schema-rich vector deployments:

```yaml
providers:
  metadata_db: sqlite
  vector_db: weaviate

storage:
  vector:
    allow_fallback: false
  vector_providers:
    weaviate:
      url: http://127.0.0.1:8080
      api_key: ""
      collection: MemoryVector
```

Install the optional dependency with `uv sync --extra weaviate`.

### SQLite + Elasticsearch

Use Elasticsearch when vector storage should live in an existing Elastic cluster:

```yaml
providers:
  metadata_db: sqlite
  vector_db: elasticsearch

storage:
  vector:
    allow_fallback: false
    distance: cosine
  vector_providers:
    elasticsearch:
      url: http://127.0.0.1:9200
      username: ""
      password: ""
      index: memory_vectors
```

Install the optional dependency with `uv sync --extra elasticsearch`.

### SQLite + OpenSearch

Use OpenSearch when vector storage should live in an existing OpenSearch cluster:

```yaml
providers:
  metadata_db: sqlite
  vector_db: opensearch

storage:
  vector:
    allow_fallback: false
    distance: cosine
  vector_providers:
    opensearch:
      url: http://127.0.0.1:9200
      username: ""
      password: ""
      index: memory_vectors
```

Install the optional dependency with `uv sync --extra opensearch`.

### SQLite + Redis/RediSearch

Use Redis when Redis Stack already owns operational infrastructure and
RediSearch should host vectors:

```yaml
providers:
  metadata_db: sqlite
  vector_db: redis

storage:
  vector:
    allow_fallback: false
    distance: cosine
  vector_providers:
    redis:
      url: redis://127.0.0.1:6379/0
      index: memory_vectors
      key_prefix: "memory_vectors:"
```

Install the optional dependency with `uv sync --extra redis`.

### MongoDB Metadata And Atlas Vectors

Use MongoDB for metadata when Mongo already owns application persistence, and
use MongoDB Atlas Vector Search when Atlas should also own vectors:

```yaml
providers:
  metadata_db: mongodb
  vector_db: mongodb_atlas

storage:
  metadata_providers:
    mongodb:
      uri: mongodb://127.0.0.1:27017
      database: ai_memory_hub
      conversations_collection: conversations
  vector_providers:
    mongodb_atlas:
      uri: mongodb+srv://user:password@example.mongodb.net/app
      database: ai_memory_hub
      collection: memory_vectors
      index: memory_vector_index
```

Install the optional dependency with `uv sync --extra mongodb`.

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
  vector_providers:
    chromadb:
      path: ./data/chromadb
      collection: memory_vectors
    qdrant:
      url: http://127.0.0.1:6333
      api_key: ""
      collection: memory_vectors
    milvus:
      uri: http://127.0.0.1:19530
      token: ""
      collection: memory_vectors
    weaviate:
      url: http://127.0.0.1:8080
      api_key: ""
      collection: MemoryVector
    mongodb_atlas:
      uri: ""
      database: ai_memory_hub
      collection: memory_vectors
      index: memory_vector_index
    elasticsearch:
      url: http://127.0.0.1:9200
      username: ""
      password: ""
      index: memory_vectors
    opensearch:
      url: http://127.0.0.1:9200
      username: ""
      password: ""
      index: memory_vectors
  metadata_providers:
    mongodb:
      uri: ""
      database: ai_memory_hub
      conversations_collection: conversations

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
  strategy: message
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
uv run aim serve --host 0.0.0.0 --port 8000
```

The built-in container configuration uses deterministic local embeddings, SQLite
metadata, and in-memory vectors so the image starts without external model or
database services.

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

For persistent local container data, mount `/app/data` and optionally `/app/logs`.
For custom configuration, mount a config file and start with:

```bash
uv run aim serve --config <path>
```

Keep secrets in a mounted config file or injected environment variables, not in
committed images or repository files.

For a reusable Docker/Podman Compose setup that runs ai-memory-hub with Postgres
metadata and PGVector vectors:

```bash
cd examples/postgres/pgvector
docker compose up --build
```

The example binds ai-memory-hub on host port `8000` for LAN clients. Use
`http://<HOST_LAN_IP>:8000/mcp/` from another PC on the same network. It also
enables tokenizer budgeting with the optional `tiktoken` extra. For remote
Ollama embeddings, see `examples/postgres/pgvector/config.ollama.yaml`.

Additional checked-in provider examples are under `examples/storage-providers`.
They cover local LanceDB, in-memory vectors, ChromaDB, Qdrant, MongoDB metadata,
MongoDB Atlas Vector Search, Milvus, Weaviate, Elasticsearch, OpenSearch, and
Redis/RediSearch.

## Testing

Run the full local suite:

```bash
uv run pytest
```

Run storage tests only:

```bash
uv run pytest tests/integration/test_storage_features.py
```

Default local runs do not require external storage services. Live provider tests
are skipped unless their `AMH_TEST_*` environment variables are set. GitHub
Actions runs fake-client provider contracts in the normal CI suite and service-
backed live provider checks in `.github/workflows/storage-providers.yml`.

Run live Postgres and PGVector tests locally:

```bash
docker run --name aim-pgvector-test \
  -e POSTGRES_USER=test \
  -e POSTGRES_PASSWORD=test \
  -e POSTGRES_DB=memory \
  -p 5432:5432 \
  -d pgvector/pgvector:pg16

export AMH_TEST_POSTGRES_DSN="postgresql://test:test@127.0.0.1:5432/memory"
uv run pytest -q tests/integration/test_storage_features.py -k "postgres_live_integration_when_dsn_provided or postgres_schema_version or pgvector_live_integration_when_dsn_provided or runtime_postgres_pgvector_live_integration_when_dsn_provided"

docker rm -f aim-pgvector-test
```

Benchmarks and evaluation helpers:

```bash
uv run python -m memory.benchmarks.token_budget --iterations 200
uv run python -m memory.benchmarks.retrieval_quality
uv run python -m memory.benchmarks.cache_candidates --iterations 1000
```

Container CI verifies Hadolint, image build, packaged `aim serve` startup,
`/ready`, MCP initialize at `/mcp/`, arbitrary non-root UID behavior, and
writable runtime paths.

A Bruno integration layer provides black-box local and CI smoke tests for the
live API/MCP surface against a running server and configured metadata/vector
stores. The Bruno lane uploads HTML and JUnit artifacts and publishes JUnit
results through GitHub Actions test summaries. Pytest CI jobs also emit JUnit
XML artifacts for unit/integration, E2E, and storage lanes. See the
[Bruno integration test plan](bruno_integration_test_plan.md).

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

## More Documentation

- [Architecture](architecture.md)
- [Agent integration](agents.md)
- [Roadmap](roadmap.md)
- [Release, container, and docs publishing plan](release_container_docs_plan.md)
- [First release readiness plan](first_release_readiness_plan.md)
- [Project promotion plan](project_promotion_plan.md)
- [Bruno integration test plan](bruno_integration_test_plan.md)
- [Browser extension capture plan](browser_extension_capture_plan.md)
- [Observability, logging, and telemetry plan](observability_logging_telemetry_plan.md)
- [Recurring codebase cleanup plan](recurring_codebase_cleanup_plan.md)
- [Bearer/API-key auth plan](bearer_api_key_auth_plan.md)
- [Project workspace collaboration plan](project_workspace_collaboration_plan.md)
- [Improvement plans](improvements.md)
