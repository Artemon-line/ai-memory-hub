# Current Features

This page summarizes what ai-memory-hub can do today. Detailed design notes and
implementation plans are linked from the navigation, but this page is the quick
inventory for GitHub Pages readers.

## Runtime Surfaces

| Area | Available now | Where to start |
| --- | --- | --- |
| HTTP API | Memory insert, search, retrieve, ask, fact search, profile, fact supersession, review, project, health, readiness, observability, and OAuth metadata/routes. | [Technical overview](overview.md) |
| MCP server | Memory validate, insert, search, retrieve, ask, fact, profile, review, and project tools plus health/search/timeline-style resources. | [Agent integration](agents.md) |
| CLI | `aim ingest`, `aim search`, `aim retrieve`, `aim ask`, `aim serve`, `aim health`, config inspection, and storage checks. | [Technical overview](overview.md#cli) |
| Containers | Non-root container image, readiness and liveness checks, and provider-specific compose examples. | [Storage provider examples](storage_provider_examples.md) |
| Docs site | GitHub Pages build through MkDocs Material. | [Documentation map](plans.md) |

## Memory Capabilities

- Schema-validated conversation ingestion from HTTP, MCP, and CLI entrypoints.
- Deterministic normalization, message chunking, deduplication, and content hash
  handling.
- Append-only accepted memory history for `v0.1.0-beta`; destructive memory
  update/delete endpoints and MCP tools are not part of the beta surface.
- Semantic search over chunked memories with stable API and MCP response shapes.
- Conversation retrieval by memory ID.
- Question answering over retrieved memories with citations, confidence, and
  provenance-oriented metadata.
- Profile and project fact extraction paths for direct recall.
- Sensitive-content quarantine for likely secrets and high-confidence PII before
  default recall, fact extraction, profile reads, or vector indexing.
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
| Embeddings | Deterministic local embeddings for smoke tests and HTTP embedding endpoints for useful semantic retrieval |

Provider startup checks cover schema compatibility, vector dimensions, selected
distance modes, fallback behavior, health state, and secret redaction.

## Security And Privacy

- Auth modes are `none` for CI/maintainer loopback smoke tests,
  `bearer_token` for personal-token compatibility, and
  `oauth_resource_server` for user-facing MCP OAuth setup.
- Bearer-token authentication protects `/memory/*` and `/mcp/*` when auth is
  enabled.
- Public `/health`, `/ready`, `/observability`, OAuth protected-resource
  metadata, authorization-server metadata, and Connect/OAuth browser routes.
- Google is the currently supported Connect UI passport provider. Meta and X
  remain disabled placeholders until their provider flows and tests ship.
- Secret redaction for DSNs, API keys, and sensitive provider configuration.
- Conservative telemetry defaults: no payloads, message text, embeddings, raw
  tool arguments, or query text in logs by default.
- `observability.debug_payloads` remains false by default for privacy.
- Runtime health and diagnostics avoid exposing credentials.

## Shipped HTTP Surface

- `POST /memory/insert`
- `POST /memory/search`
- `POST /memory/retrieve`
- `POST /memory/ask`
- `POST /memory/facts/search`
- `POST /memory/profile/get`
- `POST /memory/facts/supersede`
- `POST /memory/pending/approve`
- `POST /memory/pending/reject`
- `GET /memory/projects`
- `GET /memory/projects/default`
- `GET /memory/projects/{project_id}`
- `GET /health`
- `GET /ready`
- `GET /observability`
- `GET /.well-known/oauth-protected-resource`
- `GET /.well-known/oauth-protected-resource/mcp`
- `GET /.well-known/oauth-authorization-server`
- Connect UI and local OAuth routes under `/connect`, `/auth/*`, and `/oauth/*`

## Shipped MCP Tool Surface

- `memory_validate`
- `memory_insert`
- `memory_search`
- `memory_retrieve`
- `memory_ask`
- `memory_fact_search`
- `memory_profile_get`
- `memory_fact_supersede`
- `memory_pending_approve`
- `memory_pending_reject`
- `memory_project_list`
- `memory_project_default_get`
- `memory_project_get`

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
