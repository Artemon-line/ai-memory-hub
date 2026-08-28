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
| Metrics | API/MCP outcome and latency metrics, health metrics, provider failure counts, vector row gauges, fallback state, and local-stack Postgres exporter metrics. |
| Local stack | `examples/local-stack` compose profile with OpenTelemetry Collector, Jaeger, Prometheus, Postgres exporter, and a provisioned Grafana dashboard. |

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

The local example lives in `examples/local-stack`:

```bash
docker compose -f examples/local-stack/compose.yaml up --build
```

The example starts ai-memory-hub with OTLP export, an OpenTelemetry Collector,
Jaeger for traces, Prometheus for metric scraping, a Postgres exporter for
database telemetry, and Grafana with preloaded hub dashboards. Keep it as a
development tool, not a production observability opinion.

Useful local telemetry endpoints:

```text
http://127.0.0.1:8000/observability
http://127.0.0.1:9187/metrics
http://127.0.0.1:16686
http://127.0.0.1:9090
http://127.0.0.1:3000
```

Useful PromQL starters:

```promql
sum by (route, status_code) (rate(memory_api_requests_total[5m]))
sum by (tool, status, error_code) (rate(memory_mcp_tool_calls_total[5m]))
sum by (status, deduplicated) (rate(memory_insert_total[5m]))
rate(pg_stat_database_xact_commit{datname="memory"}[5m])
rate(pg_stat_database_xact_rollback{datname="memory"}[5m])
rate(pg_stat_database_blks_read{datname="memory"}[5m])
rate(pg_stat_database_blks_hit{datname="memory"}[5m])
rate(pg_stat_database_tup_inserted{datname="memory"}[5m])
rate(pg_stat_database_tup_updated{datname="memory"}[5m])
rate(pg_stat_database_tup_deleted{datname="memory"}[5m])
pg_stat_database_numbackends{datname="memory"}
```

Grafana opens with the `ai-memory-hub Local Overview` dashboard provisioned
from `examples/local-stack/grafana/dashboards`. It includes hub HTTP and MCP
request rates, insert rates, p95 latencies, ingestion/embedding/vector timings,
provider fallback signals, Postgres transactions, block reads/cache hits, row
writes, and active connections.

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
