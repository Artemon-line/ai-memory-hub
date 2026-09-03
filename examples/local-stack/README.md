# Local Stack

This is the main non-quickstart example. It runs ai-memory-hub with Postgres
metadata, PGVector vectors, and an observability sidecar stack. It can be used
in two modes:

- **Local smoke**: deterministic local embeddings and no auth, bound to
  `127.0.0.1:8000`.
- **Local server**: Ollama embeddings, Google OAuth, and a public HTTPS URL
  for agents running on another machine.

## Local Smoke

```bash
cd examples/local-stack
docker compose up --build
```

Check readiness:

```text
http://127.0.0.1:8000/ready
```

The default smoke mode publishes the hub API/MCP port to loopback. Postgres
stays inside the Compose network and is not exposed on the host.

Observability endpoints are also bound to loopback:

```text
http://127.0.0.1:8000/observability
http://127.0.0.1:9187/metrics
http://127.0.0.1:16686
http://127.0.0.1:9090
http://127.0.0.1:3000
```

Prometheus scrapes both ai-memory-hub telemetry from the OpenTelemetry Collector
and Postgres database telemetry from `postgres-exporter`. Useful PromQL queries:

```promql
# Hub HTTP requests per second by route/status.
sum by (route, status_code) (rate(memory_api_requests_total[5m]))

# MCP tool calls per second by tool/status.
sum by (tool, status, error_code) (rate(memory_mcp_tool_calls_total[5m]))

# Insert/write attempts per second through the hub.
sum by (status, deduplicated) (rate(memory_insert_total[5m]))

# Postgres transaction rate.
rate(pg_stat_database_xact_commit{datname="memory"}[5m])
rate(pg_stat_database_xact_rollback{datname="memory"}[5m])

# Postgres physical block reads and cache hits per second.
rate(pg_stat_database_blks_read{datname="memory"}[5m])
rate(pg_stat_database_blks_hit{datname="memory"}[5m])

# Postgres row-write activity per second.
rate(pg_stat_database_tup_inserted{datname="memory"}[5m])
rate(pg_stat_database_tup_updated{datname="memory"}[5m])
rate(pg_stat_database_tup_deleted{datname="memory"}[5m])

# Active Postgres connections.
pg_stat_database_numbackends{datname="memory"}
```

Grafana is preconfigured with Prometheus and Jaeger datasources and opens with
the `ai-memory-hub Local Overview` dashboard. The dashboard includes hub HTTP
and MCP request rates, insert rates, p95 latencies, ingestion/embedding/vector
timings, provider fallback signals, Postgres transactions, block reads/cache
hits, row writes, and active connections. Anonymous viewer access is enabled on
loopback; use `admin` / `admin` if you want to edit the local dashboard.

## Local Server With OAuth

1. Start Ollama on the host running the hub and pull the embedding model:

```bash
ollama pull nomic-embed-text
```

2. Generate a local OAuth config from the checked-in template:

```bash
export PUBLIC_BASE_URL="https://YOUR-CUSTOM-DOMAIN.app"
sed "s#https://YOUR-CUSTOM-DOMAIN.app#${PUBLIC_BASE_URL}#g" \
  config.oauth-public.yaml > config.oauth-local.yaml
```

3. In Google Cloud Console, register the matching redirect URI:

```text
https://YOUR-CUSTOM-DOMAIN.app/auth/google/callback
```

The redirect URI must use the same public base URL as `config.oauth-local.yaml`.
If the Connect page still shows `YOUR-CUSTOM-DOMAIN` after startup, stop and fix
`config.oauth-public.yaml` before starting client auth. MCP clients discover
OAuth endpoints from the advertised public URL, so a stale placeholder sends
registration and token flows to the wrong host.

4. Export secrets and start Compose:

```bash
export GOOGLE_CLIENT_ID="your-google-client-id"
export GOOGLE_CLIENT_SECRET="your-google-client-secret"
export AMH_OAUTH_JWT_SECRET="$(openssl rand -base64 48)"
export AMH_SESSION_SECRET="$(openssl rand -base64 48)"
export AMH_CONFIG_FILE=config.oauth-local.yaml
docker compose up --build
```

Remote agents should use:

```text
https://YOUR-CUSTOM-DOMAIN.app/mcp
```

The browser setup page is:

```text
https://YOUR-CUSTOM-DOMAIN.app/connect
```

Keep the Compose port binding at `127.0.0.1:8000:8000`; your tunnel or reverse
proxy should publish only the hub port, and Postgres should stay inside the
Compose network.

Grafana, Jaeger, Prometheus, and the Postgres exporter are for local diagnostics
only. Do not expose their ports through the public tunnel.
