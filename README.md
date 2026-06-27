# ai-memory-hub

[![ci](https://github.com/Artemon-line/ai-memory-hub/actions/workflows/pipeline.yml/badge.svg?branch=main)](https://github.com/Artemon-line/ai-memory-hub/actions/workflows/pipeline.yml)
[![Python 3.14+](https://img.shields.io/badge/python-3.14%2B-blue.svg)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

Provider live checks:
[![ChromaDB](https://img.shields.io/github/actions/workflow/status/Artemon-line/ai-memory-hub/storage-providers.yml?branch=main&event=push&label=ChromaDB&logo=chroma)](https://github.com/Artemon-line/ai-memory-hub/actions/workflows/storage-providers.yml)
[![Qdrant](https://img.shields.io/github/actions/workflow/status/Artemon-line/ai-memory-hub/storage-providers.yml?branch=main&event=push&label=Qdrant&logo=qdrant)](https://github.com/Artemon-line/ai-memory-hub/actions/workflows/storage-providers.yml)
[![MongoDB](https://img.shields.io/github/actions/workflow/status/Artemon-line/ai-memory-hub/storage-providers.yml?branch=main&event=push&label=MongoDB&logo=mongodb)](https://github.com/Artemon-line/ai-memory-hub/actions/workflows/storage-providers.yml)
[![Weaviate](https://img.shields.io/github/actions/workflow/status/Artemon-line/ai-memory-hub/storage-providers.yml?branch=main&event=push&label=Weaviate&logo=weaviate)](https://github.com/Artemon-line/ai-memory-hub/actions/workflows/storage-providers.yml)
[![Elasticsearch](https://img.shields.io/github/actions/workflow/status/Artemon-line/ai-memory-hub/storage-providers.yml?branch=main&event=push&label=Elasticsearch&logo=elasticsearch)](https://github.com/Artemon-line/ai-memory-hub/actions/workflows/storage-providers.yml)
[![OpenSearch](https://img.shields.io/github/actions/workflow/status/Artemon-line/ai-memory-hub/storage-providers.yml?branch=main&event=push&label=OpenSearch&logo=opensearch)](https://github.com/Artemon-line/ai-memory-hub/actions/workflows/storage-providers.yml)
[![Milvus](https://img.shields.io/github/actions/workflow/status/Artemon-line/ai-memory-hub/storage-providers.yml?branch=main&event=push&label=Milvus&logo=milvus)](https://github.com/Artemon-line/ai-memory-hub/actions/workflows/storage-providers.yml)
[![Redis](https://img.shields.io/github/actions/workflow/status/Artemon-line/ai-memory-hub/storage-providers.yml?branch=main&event=push&label=Redis&logo=redis)](https://github.com/Artemon-line/ai-memory-hub/actions/workflows/storage-providers.yml)
[![Typesense](https://img.shields.io/github/actions/workflow/status/Artemon-line/ai-memory-hub/storage-providers.yml?branch=main&event=push&label=Typesense)](https://github.com/Artemon-line/ai-memory-hub/actions/workflows/storage-providers.yml)

Local-first memory for AI agents.

`ai-memory-hub` gives Codex, opencode, Claude, Copilot, and other agent clients a
shared memory backend through MCP and HTTP. Store conversations once, then search,
retrieve, and ask over them from any compatible client.

## Why It Exists

Agent conversations contain useful project context, preferences, decisions, and
facts, but that memory is usually trapped inside one tool. ai-memory-hub keeps it
in your own local storage and exposes it through stable interfaces.

It is built for:

- Cross-client handoff between agent tools
- Local-first project and profile memory
- MCP-native workflows
- Deterministic ingestion and retrieval
- SQLite/LanceDB by default, with optional Postgres, MongoDB, and vector-store providers
- Bring-your-own embedding model, local or hosted

## Provider Selection Rule

At runtime, ai-memory-hub uses one metadata provider and one vector provider.
The active providers are selected by `providers.metadata_db` and
`providers.vector_db`. Provider blocks under `storage.metadata_providers` and
`storage.vector_providers` are only configuration candidates; listing a block
there does not activate that provider.

```yaml
providers:
  metadata_db: postgres
  vector_db: pgvector

storage:
  metadata_providers:
    postgres:
      url: postgresql://memory:memory@postgres:5432/memory
  vector_providers:
    pgvector:
      url: postgresql://memory:memory@postgres:5432/memory
      table_name: memory_vectors
```

Optional provider SDKs are installed through extras, not by the presence of a
config block. For example, use `uv sync --extra postgres` for Postgres/PGVector
or `uv sync --extra qdrant` for Qdrant. The checked-in `Containerfile` installs
all provider extras intentionally so one image can run any supported provider,
but a local/dev install only downloads the extras you request. The free local
Compose examples for PostgreSQL/PGVector, MongoDB, Redis, ChromaDB, and
SQLite/LanceDB use provider-local Containerfiles so they install only the extras
needed by that example.

## What It Does

- Stores structured conversations with validation and deduplication
- Searches semantic memory with conversation-aware grouping
- Answers questions with citations, confidence, and provenance
- Extracts useful profile/project facts for direct answers
- Supports multilingual memory when the configured embedding model supports the
  languages involved
- Serves both FastAPI endpoints and an MCP server
- Runs locally, in containers, or against your selected storage backend

## Runtime Choices

ai-memory-hub is the memory service. It stores conversations, builds embeddings
for retrieval, and exposes HTTP/MCP APIs. It does not bundle a hosted embedding
model or database service for you.

You choose three things:

| Choice | Local/default path | When to change it |
| --- | --- | --- |
| Embeddings | Deterministic local embeddings for smoke tests and demos | Use a real embedding model for useful semantic search, especially multilingual memory |
| Metadata storage | SQLite | Use Postgres for shared durable server setups, or MongoDB when it already owns application persistence |
| Vector storage | LanceDB, ChromaDB, Qdrant, Milvus, Weaviate, PGVector, MongoDB Atlas, Elasticsearch, OpenSearch, Redis/RediSearch, Vespa, Typesense, Pinecone, Turbopuffer, or in-memory | Use the backend that already fits your local or hosted operations stack |

## Multilingual Retrieval Rule

ai-memory-hub is not English-only. It stores Unicode text and uses the
configured embedding model for semantic retrieval. Multilingual retrieval works
when the configured embedding model supports the languages you store and query.

Use the same embedding provider, model, dimension, and options for ingestion and
query-time retrieval. If you change the embedding model, provider, dimension, or
embedding options for an existing persistent vector index, reindex the stored
memory or use a separate vector namespace/index. Do not mix vectors from
different embedding spaces, even when two models have the same dimension.

For persistent vector stores, startup records a redacted embedding/vector-index
compatibility fingerprint in the metadata store. If the fingerprint changes for
an index that already contains vectors, startup fails with a reindex-required
message. To migrate intentionally, point the new configuration at an empty
vector table, collection, index, or namespace and re-ingest/reindex the memory
from durable metadata or source transcripts.

Today the real embedding path is OpenAI-compatible: configure an
OpenAI-compatible embedding endpoint through the OpenAI client, such as OpenAI
itself or a local Ollama-compatible `/v1` endpoint. The deterministic local
embedding mode is for smoke tests and demos, not production-quality semantic or
multilingual retrieval.

Storage guidance:

- Use SQLite + LanceDB for the fastest single-machine development setup.
- Use SQLite + ChromaDB when you want a local-first vector backend with an optional HTTP client mode.
- Use SQLite + Qdrant when you want a local Docker or Qdrant Cloud vector backend.
- Use Postgres metadata when multiple clients/users or long-running containers
  need one shared database.
- Use Postgres + PGVector when you want one database to store both conversation
  metadata and vector indexes.
- Use MongoDB metadata or MongoDB Atlas Vector Search when MongoDB already owns
  the application's persistence layer.
- Use Milvus/Zilliz, Weaviate, Elasticsearch, OpenSearch, Redis/RediSearch, Vespa, or Typesense
  when those systems already own vector infrastructure in your environment.
- Use Pinecone or Turbopuffer when managed/serverless vector search is preferred
  and hosted
  credentials, namespace policy, and cost controls are already in place.
- Use in-memory vectors only for tests, demos, and disposable container smoke
  runs.

## Quick Start

Install:

```bash
git clone https://github.com/Artemon-line/ai-memory-hub.git
cd ai-memory-hub
uv sync --dev
```

Start the API and MCP server:

```bash
uv run aim serve --host 127.0.0.1 --port 8000
```

Insert a memory:

```bash
curl -X POST http://127.0.0.1:8000/memory/insert \
  -H "Content-Type: application/json" \
  -d '{
    "source": "codex",
    "messages": [
      {"role": "user", "text": "Remember that I prefer local-first tools."},
      {"role": "assistant", "text": "Stored."}
    ],
    "metadata": {"tags": ["preferences"]}
  }'
```

Ask over memory:

```bash
curl -X POST http://127.0.0.1:8000/memory/ask \
  -H "Content-Type: application/json" \
  -d '{"question": "What tool preference did the user mention?", "top_k": 5}'
```

MCP endpoint:

```text
http://127.0.0.1:8000/mcp/
```

## Common Workflows

Use the CLI during development:

```bash
uv run aim ingest conversation.json --json
uv run aim search "local-first tools" --top-k 5 --json
uv run aim ask "What did I store about local-first tools?" --top-k 5 --json
```

Run with Docker:

```bash
docker build -t ai-memory-hub:local -f Containerfile .
docker run --rm -p 8000:8000 ai-memory-hub:local
```

Run with Postgres and PGVector:

```bash
cd examples/storage_providers/postgres-pgvector
docker compose up --build
```

That Compose stack uses Postgres for metadata and PGVector for vectors. The
default checked-in config keeps embeddings deterministic and credential-free for
local smoke testing. For real memory quality, switch the embedding provider to a
real OpenAI-compatible local or hosted embedding model and set the matching
embedding dimension. For multilingual memory, choose an embedding model that
supports your languages. Reindex or use a separate vector namespace/index if
you change embedding model/provider/options on persistent data.

The Compose example is unauthenticated for local smoke testing. Before exposing
it on a LAN, create a bearer token in the metadata database and change
`api.auth` in the mounted config to `bearer_token`; clients then send
`Authorization: Bearer <token>` to both HTTP and MCP endpoints. See the PGVector
runbook in the checked-in Compose example directory for the exact commands.

Other checked-in provider examples live under `examples/storage_providers`:
ChromaDB, Qdrant, MongoDB, MongoDB Atlas, Milvus, Weaviate, Elasticsearch,
OpenSearch, Redis/RediSearch, Vespa, Typesense, Pinecone, Turbopuffer,
SQLite/LanceDB, and in-memory vectors.

The local MongoDB and Redis examples include their own Compose stack and
provider-local hub `Containerfile`, so they avoid building the all-extras image:

```bash
cd examples/storage_providers/mongodb
docker compose up --build

cd examples/storage_providers/redis
docker compose up --build
```

Example:

```bash
cd examples/storage_providers/qdrant
docker compose up --build
```

See [Storage provider examples](docs/storage_provider_examples.md) for the
provider matrix, smoke commands, CI coverage, and hosted-provider notes.

## Documentation

- [Technical overview](docs/overview.md)
- [Architecture](docs/architecture.md)
- [Agent integration](docs/agents.md)
- [MCP plan](docs/mcp_plan.md)
- [Real-client MCP smoke plan](docs/real_client_mcp_smoke_plan.md)
- [Browser extension capture plan](docs/browser_extension_capture_plan.md)
- [First release readiness plan](docs/first_release_readiness_plan.md)
- [Project promotion plan](docs/project_promotion_plan.md)
- [Storage provider examples](docs/storage_provider_examples.md)
- [Roadmap](docs/roadmap.md)
- [Improvements](docs/improvements.md)

Build the docs locally:

```bash
uv run python tools/prepare_mkdocs.py
uv run python -m mkdocs build --strict
```

## Status

The project currently includes deterministic ingestion, MCP tools/resources/prompts,
HTTP endpoints, CLI commands, fact-backed answers, SQLite/Postgres/MongoDB
metadata, LanceDB/ChromaDB/Qdrant/Milvus/Weaviate/PGVector/MongoDB Atlas/
Elasticsearch/OpenSearch/Redis/Vespa/Typesense/Pinecone/Turbopuffer/in-memory vectors, token-budgeted ask, container CI,
provider live-test CI, and GitHub Pages docs publishing.

Planned work includes broader importers, richer summaries, deletion/update
workflows, provider-specific index compatibility checks, and release publishing.

## Contributing

Contributions are welcome. Keep changes focused, add tests for behavior changes,
and run the relevant checks before opening a pull request.

```bash
uv run python -m ruff check memory tests tools
uv run python -m pyright
uv run pytest
uv run python -m mkdocs build --strict
```

## License

This project is licensed under the MIT License. See [LICENSE](LICENSE) for
details.
