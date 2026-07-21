# Current Features

This page summarizes what ai-memory-hub can do today. Detailed design notes and
implementation plans are linked from the navigation, but this page is the quick
inventory for GitHub Pages readers.

## Runtime Surfaces

| Area | Available now | Where to start |
| --- | --- | --- |
| HTTP API | Insert, search, retrieve, ask, health, readiness, OAuth protected-resource metadata, and redacted observability summary endpoints. | [Technical overview](overview.md) |
| MCP server | Memory insert, validate, search, retrieve, and ask tools plus health/search/timeline-style resources. | [Agent integration](agents.md) |
| CLI | `aim ingest`, `aim search`, `aim retrieve`, `aim ask`, `aim serve`, `aim health`, config inspection, and storage checks. | [Technical overview](overview.md#cli) |
| Containers | Non-root container image, readiness and liveness checks, and provider-specific compose examples. | [Storage provider examples](storage_provider_examples.md) |
| Docs site | GitHub Pages build through MkDocs Material. | [Documentation map](plans.md) |

## Memory Capabilities

- Schema-validated conversation ingestion from HTTP, MCP, and CLI entrypoints.
- Deterministic normalization, message chunking, deduplication, and content hash
  handling.
- Semantic search over chunked memories with stable API and MCP response shapes.
- Conversation retrieval by memory ID.
- Question answering over retrieved memories with citations, confidence, and
  provenance-oriented metadata.
- Profile and project fact extraction paths for direct recall.
- Conversation-aware grouping and retrieval precision improvements.
- Unicode text storage and multilingual retrieval when the configured embedding
  model supports the stored and queried languages.

## Storage And Embeddings

ai-memory-hub uses one metadata provider and one vector provider at runtime.
SQLite plus LanceDB is the easiest local path, while larger or shared setups can
switch providers through configuration.

| Provider type | Available providers |
| --- | --- |
| Metadata | SQLite, Postgres, MongoDB |
| Vector | LanceDB, Qdrant, Milvus, Weaviate, PGVector, MongoDB Atlas Vector Search, Elasticsearch, OpenSearch, Redis/RediSearch, Pinecone, Turbopuffer, Vespa, Typesense, in-memory |
| Embeddings | Deterministic local embeddings for smoke tests and OpenAI-compatible embedding endpoints for useful semantic retrieval |

Provider startup checks cover schema compatibility, vector dimensions, selected
distance modes, fallback behavior, health state, and secret redaction.

## Security And Privacy

- Bearer-token authentication for protected HTTP and MCP flows.
- Public `/health`, `/ready`, and OAuth protected-resource metadata endpoints.
- Secret redaction for DSNs, API keys, and sensitive provider configuration.
- Conservative telemetry defaults: no payloads, message text, embeddings, raw
  tool arguments, or query text in logs by default.
- `observability.debug_payloads` remains false by default for privacy.
- Runtime health and diagnostics avoid exposing credentials.

## Observability

The service now has a practical local observability baseline:

- Structured text or JSON logs with request IDs, trace IDs, span IDs, operation
  names, and provider context where available.
- HTTP request-id middleware.
- `/health`, `/ready`, and `/observability` endpoints.
- Optional OpenTelemetry tracing for FastAPI, HTTP clients, and psycopg.
- Metrics for API/MCP outcomes, latency, health, vector rows, provider failures,
  and fallback state.
- A local observability compose example with OpenTelemetry Collector, Jaeger,
  Prometheus, and Grafana-compatible metrics flow.

See [Observability](observability.md) for setup and operator notes.

## Integration And Testing

- Bruno integration smoke coverage for health, HTTP memory flow, and MCP memory
  flow.
- Provider live-check workflows for common local and hosted-adjacent storage
  backends.
- Unit, integration, and end-to-end test layout in the repository.
- Release readiness, container, docs publishing, and governance checklists.
