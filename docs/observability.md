# Observability

ai-memory-hub is designed to be debuggable as a long-running local, LAN, or
containerized MCP/API memory service while keeping memory content private by
default.

## What Exists Now

| Capability | Status |
| --- | --- |
| Log formats | Text and JSON logs with configurable level and access logging. |
| Correlation | Request IDs for HTTP requests and correlation IDs for MCP tool calls where clients provide them. |
| Trace context | Trace and span IDs are included in logs when OpenTelemetry tracing is active. |
| Health | `/health`, `/ready`, CLI health, and `memory://health` surfaces. |
| Runtime summary | `/observability` returns redacted runtime and telemetry configuration. |
| Tracing | Optional OpenTelemetry setup with FastAPI, HTTP client, requests, and psycopg instrumentation. |
| Metrics | API/MCP outcome and latency metrics, health metrics, provider failure counts, vector row gauges, and fallback state. |
| Local stack | `examples/observability` compose profile with OpenTelemetry Collector, Jaeger, Prometheus, and Grafana-compatible metric flow. |

## Privacy Rules

The default observability path does not log:

- conversation payloads or message text
- search query text
- embeddings
- raw MCP tool arguments
- API keys, DSNs, or provider credentials
- user-provided tags as metric labels

Use `observability.debug_payloads: true` only for short-lived local debugging
with non-sensitive data.

## Basic Configuration

```yaml
observability:
  logging:
    enabled: true
    format: text
    level: INFO
    access_logs: true
    request_id_header: x-request-id
    include_stack_traces: true
  tracing:
    enabled: false
    endpoint: http://otel-collector:4317
    protocol: grpc
    sample_ratio: 1.0
  metrics:
    enabled: false
    endpoint: http://otel-collector:4317
    protocol: grpc
  debug_payloads: false
  embedding_readiness_probe: false
```

Install OpenTelemetry dependencies only when you need them:

```bash
uv sync --extra observability
```

## Local Observability Stack

The local example lives in `examples/observability`:

```bash
docker compose -f examples/observability/compose.yaml up --build
```

The example starts ai-memory-hub with OTLP export, an OpenTelemetry Collector,
Jaeger for traces, and Prometheus for metric scraping. Keep it as a development
tool, not a production observability opinion.

## Health And Readiness

Use readiness for orchestration and smoke checks:

```bash
curl -fsS http://127.0.0.1:8000/ready
```

Use liveness for process health:

```bash
curl -fsS http://127.0.0.1:8000/health
```

Use the redacted observability summary when debugging local configuration:

```bash
curl -fsS http://127.0.0.1:8000/observability
```

## Still Planned

Manual domain spans around MCP tools and ingestion stages are implemented; retrieval
stage spans remain a follow-up. See the
[observability implementation plan](observability_logging_telemetry_plan.md) for
the detailed checklist.
