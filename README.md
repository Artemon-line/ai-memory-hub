# ai-memory-hub

[![ci](https://github.com/Artemon-line/ai-memory-hub/actions/workflows/pipeline.yml/badge.svg?branch=main)](https://github.com/Artemon-line/ai-memory-hub/actions/workflows/pipeline.yml)
[![Python 3.14+](https://img.shields.io/badge/python-3.14%2B-blue.svg)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

Provider live checks:
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

```text
                         +------------------+
                         |  ai-memory-hub   |
                         | local/LAN/hosted |
                         +---------+--------+
                                   |
              HTTP API + MCP + auth + storage contract
                                   |
        +--------------------------+--------------------------+
        |                          |                          |
  Codex plugin              Browser extension           Other clients
  skills / MCP setup        Chrome/Edge/etc.            agents, CLIs, apps
        |                          |                          |
        |                          |                          |
  capture + retrieval       capture + retrieval         capture + retrieval
  context injection         context injection           context injection
```

Adapters do not own memory. Adapters own capture and context injection. The hub
owns storage, retrieval, auth, permissions, and memory quality.

Terminology:

- Capture: an adapter sends selected conversation, page, note, or code context
  into the hub.
- Ingestion: the hub validates, normalizes, deduplicates, enriches, embeds, and
  stores captured content.
- Retrieval: the hub returns relevant memories, facts, citations, or answers.
- Context injection: an adapter places retrieved memory into the active agent,
  browser, editor, or CLI session.

## Why It Exists

Agent conversations contain useful project context, preferences, decisions, and
facts, but that memory is usually trapped inside one tool. ai-memory-hub keeps it
in your own local storage and exposes it through stable interfaces.

It is built for:

- Cross-client handoff between agent tools
- Local-first project and profile memory
- MCP-native workflows
- Deterministic capture, ingestion, retrieval, and context injection boundaries
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
or `uv sync --extra qdrant` for Qdrant. The checked-in root `Containerfile` is
the quickstart image and installs only the default SQLite/LanceDB runtime
dependencies. Provider-specific Compose examples use provider-local
Containerfiles so they install only the extras needed by that example.

## What It Does

- Stores structured conversations with validation and deduplication
- Searches semantic memory with conversation-aware grouping
- Answers questions with concise MCP defaults or detailed citations,
  confidence, and provenance on request
- Extracts useful profile/project facts for direct answers
- Supports multilingual memory when the configured embedding model supports the
  languages involved
- Serves both FastAPI endpoints and an MCP server
- Supports MCP OAuth setup through the Connect UI with durable dynamic clients,
  rotating refresh tokens, and revocation
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
| Vector storage | LanceDB, Qdrant, Milvus, Weaviate, PGVector, MongoDB Atlas, Elasticsearch, OpenSearch, Redis/RediSearch, Vespa, Typesense, Pinecone, Turbopuffer, or in-memory | Use the backend that already fits your local or hosted operations stack |

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

### Changing Embedding Models

Changing embedding model, provider, dimension, or embedding options creates a
new vector space. Treat it as a reindex operation, even when the old and new
models produce the same number of dimensions.

Safe migration checklist:

1. Stop the hub and any clients that are writing memory.
2. Back up the metadata database and the current vector store.
3. Update `providers.embeddings`, `providers.embedding_model`, and
   `providers.embedding_dimension` for the new model.
4. Point `providers.vector_db` at an empty vector table, collection, index, or
   namespace. Keep the same metadata database if you want to preserve existing
   conversations, facts, projects, and auth data.
5. Recalculate embeddings from the stored metadata:
   `uv run aim reindex --config <new-config.yaml> --json`.
6. Run `uv run aim storage-check --config <new-config.yaml> --json`, then test
   `search` and `ask` before retiring the old vector store.

If startup reports that reindexing is required, do not disable the guardrail to
force the old vector index to load. Switch back to the old embedding settings, or
move the new settings to an empty vector destination and run `aim reindex`.

Today the real embedding path is a generic HTTP embeddings endpoint using the
OpenAI-compatible `/v1/embeddings` request/response schema, such as a local
Ollama-compatible `/v1` endpoint. ai-memory-hub uses a small embeddings-only
HTTP client and does not require the OpenAI Python SDK. The deterministic local
embedding mode is for smoke tests and demos, not production-quality semantic or
multilingual retrieval.

Storage guidance:

- Use SQLite + LanceDB for the fastest single-machine development setup.
- Use SQLite + Qdrant when you want a local Docker or Qdrant Cloud vector backend.
- Use Postgres metadata when multiple clients/users or long-running containers
  need one shared database.
- Use Postgres + PGVector when you want one database to store both conversation
  metadata and vector indexes.

ChromaDB is temporarily unavailable in `v0.1.0` because the upstream
`chromadb` package has an unresolved critical advisory with no patched release.
The adapter remains in the repository for future re-enable.

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

For user-facing MCP setup, use the local stack example with
`api.auth: oauth_resource_server` and open `/connect`. The Connect UI shows the
active MCP URL, configured passport providers, sign-in status, short-lived hub
token workflow, and copyable client snippets. Google is the current live
provider; Meta and X config slots are disabled placeholders until their
provider-specific flows are implemented. See the
[Connect UI and OAuth setup guide](docs/connect_ui.md) for packages, provider
status, and client verification notes.

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
docker run --rm -p 127.0.0.1:8000:8000 ai-memory-hub:local
```

Run the full local stack with Postgres, PGVector, Ollama embeddings, and
Google OAuth:

```bash
cd examples/local-stack
docker compose up --build
```

That Compose stack is the main non-quickstart example. It can run
credential-free with deterministic embeddings, or use
`config.oauth-public.yaml` for the local-server path:

```bash
PUBLIC_BASE_URL="https://YOUR-CUSTOM-DOMAIN.app"
sed "s#https://YOUR-CUSTOM-DOMAIN.app#${PUBLIC_BASE_URL}#g" \
  config.oauth-public.yaml > config.oauth-local.yaml
AMH_CONFIG_FILE=config.oauth-local.yaml docker compose up --build
```

Generate the local config with the same public HTTPS base URL you register as
the Google callback origin, export the Google/OAuth environment variables
required by Compose, and keep the hub port bound to `127.0.0.1:8000` when
publishing through a tunnel or reverse proxy. Before exposing
it beyond loopback, use `api.auth: oauth_resource_server` with TLS.
For real memory quality, keep
`providers.embeddings: http`, point
`embedding_endpoint.base_url` at Ollama or another OpenAI-compatible embeddings
endpoint, and set the matching embedding dimension. Reindex or use a separate
vector namespace/index if you change embedding model/provider/options on
persistent data.

See [Storage provider examples](docs/storage_provider_examples.md) for the
provider fixture matrix, smoke commands, CI coverage, and hosted-provider notes.

## Documentation

- [Technical overview](docs/overview.md)
- [Architecture](docs/architecture.md)
- [Agent integration](docs/agents.md)
- [MCP plan](docs/mcp_plan.md)
- [Google OAuth Connect UI plan](docs/improvements/google_oauth_connect_ui_plan.md)
- [Real-client MCP smoke plan](docs/real_client_mcp_smoke_plan.md)
- [Browser extension capture plan](docs/browser_extension_capture_plan.md)
- [Plugin readiness plan](docs/plugin_readiness_plan.md)
- [First release readiness plan](docs/first_release_readiness_plan.md)
- [Release CI gap analysis](docs/release_ci_gap_analysis.md)
- [Release security scan notes](docs/release_security_scan_notes.md)
- [Repository governance settings](docs/repository_governance_settings.md)
- [Release promotion assets](docs/release_promotion_assets.md)
- [Changelog](CHANGELOG.md)
- [Contributing guide](CONTRIBUTING.md)
- [Security policy](SECURITY.md)
- [Project promotion plan](docs/project_promotion_plan.md)
- [Storage provider examples](docs/storage_provider_examples.md)
- [Roadmap](docs/roadmap.md)
- [Improvements](docs/improvements.md)

Build the docs locally:

```bash
uv sync --group docs
uv run python tools/prepare_mkdocs.py
uv run python -m mkdocs build --strict
```

## Status

The project currently includes deterministic append-only ingestion, MCP
tools/resources/prompts, HTTP endpoints, CLI commands, fact-backed answers,
sensitive-content quarantine, SQLite/Postgres/MongoDB metadata,
LanceDB/Qdrant/Milvus/Weaviate/PGVector/MongoDB Atlas/Elasticsearch/OpenSearch/
Redis/Vespa/Typesense/Pinecone/Turbopuffer/in-memory vectors, token-budgeted
ask, container CI, provider live-test CI, and GitHub Pages docs publishing.

Planned work includes broader importers, richer summaries, admin-only
archive/retention workflows, provider-specific index compatibility checks, and
release publishing. General destructive memory update/delete workflows are not
part of `v0.1.0-beta`; corrections should be represented as new memory, fact
supersession, or explicit governance events over immutable history.

Known first-release limits: ai-memory-hub does not ship as a hosted memory
service, production-quality retrieval requires a bring-your-own embedding model,
browser extensions are planned as separate adapters, UI dashboards/SDKs are
future work, and archive/restore does not ship in `v0.1.0-beta`.

## Contributing

Contributions are welcome. Keep changes focused, add tests for behavior changes,
and run the relevant checks before opening a pull request.

```bash
uv sync --dev --group docs
uv run python -m ruff check memory tests tools
uv run python -m pyright
uv run pytest
uv run python -m mkdocs build --strict
```

## Dedication

Dedicated to my little brother,

Andrii Hladenko.

Вічна Слава Героям. ![Ukraine flag](docs/assets/flags/ua.svg)

## License

This project is licensed under the MIT License. See [LICENSE](LICENSE) for
details.
