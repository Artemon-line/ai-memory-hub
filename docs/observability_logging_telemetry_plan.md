# Observability, Logging, And Telemetry Plan

## Goal

Make ai-memory-hub debuggable when used as a long-running MCP/API memory service.
The immediate target is to make failures like MCP insert, embedding provider,
Postgres, and PGVector issues visible with enough context to diagnose them from
container logs and traces.

## Current Status

Implemented:

- [x] Standard Python loggers exist in some modules.
- [x] Secret redaction helper exists for DSNs and key-value secrets.
- [x] Storage health helpers exist for metadata and vector providers.
- [x] `/health` and `/ready` HTTP endpoints.
- [x] Request-id middleware for FastAPI HTTP requests.
- [x] Structured HTTP failure logs include method, path, status, auth mode, and
  request id without logging request bodies or query-string values.
- [x] Startup logs fallback/dry-run policy state.
- [x] Persistent vector providers with fallback enabled emit a warning about
  non-durable fallback.
- [x] MCP `memory_insert`, `memory_search`, and `memory_ask` now log unexpected
  exceptions with stack traces before returning stable error envelopes.

Not implemented yet:

- [x] Global logging configuration for JSON/text format, log level, and access logs.
- [x] Request or operation correlation IDs.
- [x] Trace IDs in logs.
- [x] OpenTelemetry SDK setup.
- [x] FastAPI, HTTP client, and psycopg instrumentation.
- [ ] Manual spans around MCP tools and ingestion stages.
- [x] Metrics for tool/API latency, insert/search/ask outcomes, provider failures,
  vector rows, and fallback state.
- [x] `/health` and `/ready` HTTP endpoints.
- [x] Docker Compose observability profile with OTel Collector and Jaeger.
- [x] Documentation for local and LAN observability usage.

## Recommended Stack

Keep the default runtime lightweight:

- App logs to stdout.
- Optional OpenTelemetry SDK exports traces and metrics over OTLP.
- OpenTelemetry Collector receives app telemetry.
- Jaeger all-in-one stores and displays traces for local development.
- Prometheus scrapes metrics from the collector or app.
- Grafana is optional for metrics dashboards once metric names stabilize.

Why this shape:

- OTLP keeps the application vendor-neutral.
- The Collector isolates exporter and backend choices from application code.
- Jaeger all-in-one is enough for local trace debugging.
- Prometheus/Grafana can be added without changing application spans.

References:

- OpenTelemetry Python documentation: https://opentelemetry.io/docs/languages/python/
- OpenTelemetry Python instrumentation: https://opentelemetry.io/docs/languages/python/instrumentation/
- OpenTelemetry Python exporters: https://opentelemetry.io/docs/languages/python/exporters/
- OpenTelemetry Collector quick start: https://opentelemetry.io/docs/collector/quick-start/
- Jaeger getting started: https://www.jaegertracing.io/docs/2.19/getting-started/
- Jaeger OTLP receiving APIs: https://www.jaegertracing.io/docs/2.19/architecture/apis/

## Design Principles

- Do not log conversation payloads, message text, embeddings, API keys, DSNs, or
  raw MCP tool arguments by default.
- Every error log must include enough context to reproduce the failing component:
  operation, source, provider names, memory id when known, dedupe state, and
  exception class.
- Tool/API responses stay stable; detailed stack traces go to server logs and
  traces, not MCP response bodies.
- Telemetry must be optional and disabled by default unless configured.
- OpenTelemetry failures must never break memory operations.
- Use low-cardinality metric labels. Avoid labels containing query text, memory
  ids, thread ids, or user-provided tags.

## Phase 1: Logging Foundation

### Config

Add config model:

```yaml
observability:
  service_name: ai-memory-hub
  environment: local
  log_level: INFO
  log_format: text  # text | json
  access_logs: true
  request_id_header: x-request-id
  include_stack_traces: true
```

### Implementation Tasks

- [x] Add `memory/observability/logging.py`.
- [x] Configure root logger once during app startup.
- [x] Support text logs for humans and JSON logs for containers.
- [x] Add request-id middleware for FastAPI HTTP requests.
- [x] Include `request_id`, `trace_id`, `span_id`, `operation`, and `provider`
  fields when available.
- [ ] Standardize exception logs:
  - `logger.exception("MCP tool failed", extra={...})`
  - `logger.warning("Provider fallback activated", extra={...})`
  - `logger.info("Conversation inserted", extra={...})`
- [ ] Keep secret redaction on all config/provider exception messages.

### Key Events To Log

- Startup configuration summary:
  - embeddings provider
  - metadata provider
  - vector provider
  - tokenizer mode
  - MCP/API enabled
  - telemetry enabled
- MCP tool start/end:
  - `memory_validate`
  - `memory_insert`
  - `memory_search`
  - `memory_retrieve`
  - `memory_ask`
- Ingestion stages:
  - normalize
  - validate
  - dedupe lookup
  - metadata insert
  - chunking
  - embedding
  - vector insert
  - index-state update
- Provider failures and fallback activation.

## Phase 2: Health And Readiness

Add endpoints:

- `GET /health`: process is alive.
- `GET /ready`: metadata store, vector store, tokenizer, and embedding provider
  are ready enough for requests.
- `GET /observability`: redacted observability/runtime summary for local debugging.

Response shape:

```json
{
  "status": "ok",
  "mode": "ok",
  "metadata_provider": "postgres",
  "vector_provider": "pgvector",
  "embedding_provider": "openai",
  "telemetry_enabled": true
}
```

Tasks:

- [x] Reuse `mvp_ingestion.runtime_health()`.
- [x] Add lightweight embedding-provider readiness check that does not perform a
  real embedding call unless explicitly configured.
- [x] Add tests for ok/degraded/dry-run readiness.
- [x] Update Compose healthcheck to call `/ready` once available.

## Phase 3: OpenTelemetry Tracing

### Dependencies

Add optional extra:

```toml
[project.optional-dependencies]
observability = [
  "opentelemetry-api",
  "opentelemetry-sdk",
  "opentelemetry-exporter-otlp-proto-grpc",
  "opentelemetry-instrumentation-fastapi",
  "opentelemetry-instrumentation-httpx",
  "opentelemetry-instrumentation-requests",
  "opentelemetry-instrumentation-psycopg",
]
```

Use broad compatible ranges in the real patch, pinned by `uv.lock`.

### Config

```yaml
observability:
  tracing:
    enabled: false
    endpoint: http://otel-collector:4317
    protocol: grpc
    sample_ratio: 1.0
```

### Automatic Instrumentation

- [x] FastAPI request spans.
- [x] HTTP client spans for OpenAI/Ollama embedding requests.
- [x] Psycopg spans for Postgres and PGVector SQL.

### Manual Spans

Add explicit spans around domain operations:

- `mcp.memory_validate`
- `mcp.memory_insert`
- `mcp.memory_search`
- `mcp.memory_ask`
- `ingestion.normalize`
- `ingestion.validate_schema`
- `ingestion.dedupe_lookup`
- `ingestion.metadata_insert`
- `ingestion.chunk`
- `ingestion.embed`
- `ingestion.vector_insert`
- `retrieval.vector_search`
- `retrieval.keyword_search`
- `retrieval.rerank`

Recommended span attributes:

- `memory.operation`
- `memory.source`
- `memory.id` when known
- `memory.deduplicated`
- `memory.appended_messages`
- `memory.chunks`
- `memory.metadata_provider`
- `memory.vector_provider`
- `memory.embedding_provider`
- `memory.embedding_model`
- `error.type`

Avoid:

- message text
- search query text
- full MCP arguments
- embeddings
- DSNs
- API keys

## Phase 4: Metrics

### Config

```yaml
observability:
  metrics:
    enabled: false
    endpoint: http://otel-collector:4317
    protocol: grpc
```

### Initial Metrics

Counters:

- [x] `memory_mcp_tool_calls_total{tool,status,error_code}`
- [x] `memory_api_requests_total{route,status_code}`
- [x] `memory_insert_total{source,status,deduplicated}`
- [x] `memory_embedding_failures_total{provider,error_type}`
- [x] `memory_provider_fallback_total{requested_provider,effective_provider}`

Histograms:

- [x] `memory_mcp_tool_duration_ms{tool,status}`
- [x] `memory_api_request_duration_ms{route,status_code}`
- [ ] `memory_ingestion_stage_duration_ms{stage}`
- [x] `memory_embedding_duration_ms{provider,model}`
- [x] `memory_vector_search_duration_ms{provider}`

Gauges:

- [x] `memory_vector_rows{provider}`
- [x] `memory_health_mode{mode}`
- [x] `memory_tokenizer_enabled`

Label rules:

- `source` is acceptable if bounded to known client names.
- Do not use memory IDs, queries, titles, tags, or user text as metric labels.

## Phase 5: Local Observability Compose Profile

Add `examples/observability/`:

```text
examples/observability/
  compose.yaml
  config.yaml
  otel-collector.yaml
  prometheus.yaml
  README.md
```

Recommended local stack:

- `otel-collector`
- `jaeger`
- `prometheus`
- optional `grafana`

Ports:

- OTel gRPC: `4317`
- OTel HTTP: `4318`
- Jaeger UI: `16686`
- Prometheus: `9090`
- Grafana: `3000` if enabled

The Jaeger all-in-one image exposes OTLP ports `4317` and `4318`, and the UI on
`16686`, according to Jaeger 2.19 docs. For this project, prefer app -> Collector
-> Jaeger instead of app -> Jaeger directly so metrics and future log export can
share the same pipeline.

## Phase 6: Log Correlation

After traces are active:

- [x] Add trace id and span id to every application log record.
- [x] Add request id to every HTTP log.
- [x] Add MCP tool call id when available.
- [x] Ensure exception logs include `exc_info=True` through `logger.exception`.
- [x] Add one integration test proving a failed MCP insert logs an exception with
  operation and error code.

## Phase 7: Privacy And Redaction

Hard requirements:

- [ ] Redact DSNs, API keys, bearer tokens, passwords, and OpenAI/Ollama keys.
- [ ] Do not log raw conversation payloads.
- [ ] Do not log raw search queries by default.
- [ ] Do not export user message text as span attributes.
- [x] Add `observability.debug_payloads: false` and keep it false by default.

If payload debugging is ever needed, require an explicit local-only flag and log
only truncated, redacted snippets.

## Phase 8: Test Plan

Unit tests:

- [x] Logging config chooses text or JSON format.
- [x] Secret redaction applies to structured log extras.
- [x] Request ID middleware uses incoming `x-request-id` or generates one.
- [x] OTel setup is no-op when disabled.
- [x] OTel setup does not raise when exporter endpoint is unavailable.

Integration tests:

- [x] Failed MCP insert logs stack trace and stable error envelope.
- [x] `GET /health` and `GET /ready` return expected mode.
- [ ] Manual spans are created for ingestion stages with safe attributes.
- [x] Metrics counters increment for successful and failed MCP tools.

Local smoke:

- [ ] Start ai-memory-hub + Postgres + PGVector + OTel Collector + Jaeger.
- [ ] Insert Velvet Lantern conversation.
- [ ] Open Jaeger UI and find `mcp.memory_insert`.
- [ ] Confirm child spans show metadata insert, embedding, and vector insert.
- [ ] Confirm logs include matching trace id.

## Implementation Order

1. Logging config and request IDs.
2. Health/readiness endpoints.
3. MCP/API exception logging tests.
4. OpenTelemetry optional dependencies and config.
5. FastAPI + HTTP + psycopg instrumentation.
6. Manual spans around MCP and ingestion stages.
7. Metrics.
8. Observability Compose profile.
9. Documentation and screenshots.

## Acceptance Criteria

- A failed `memory_insert` has a visible stack trace in container logs.
- Every MCP tool failure log includes operation, error code, exception type, and
  trace id when tracing is enabled.
- Jaeger shows a trace for `memory_insert` with ingestion subspans.
- Prometheus shows request/tool counters and latency histograms.
- No stored message text, embeddings, API keys, or DSNs appear in logs, spans, or
  metric labels by default.
- Observability can be fully disabled with config.
