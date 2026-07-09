# Local Observability Profile

This profile runs ai-memory-hub with JSON logs, OpenTelemetry traces, OpenTelemetry
metrics, an OTel Collector, Jaeger, and Prometheus.

Start it from the repository root:

```bash
cd examples/observability
docker compose up --build
```

Local endpoints:

- ai-memory-hub API: <http://127.0.0.1:8000>
- Readiness: <http://127.0.0.1:8000/ready>
- Runtime summary: <http://127.0.0.1:8000/observability>
- Jaeger UI: <http://127.0.0.1:16686>
- Prometheus: <http://127.0.0.1:9090>
- OTel Collector gRPC: `127.0.0.1:4317`
- OTel Collector HTTP: `127.0.0.1:4318`

The included config uses local deterministic embeddings and in-memory vectors so
the profile does not require Ollama, OpenAI, Postgres, or PGVector.

For trusted LAN testing, change the ai-memory-hub port binding in `compose.yaml`
from `127.0.0.1:8000:8000` to an explicit LAN binding only after enabling
protected MCP/API authentication and a TLS, VPN, or trusted reverse-proxy
boundary.
