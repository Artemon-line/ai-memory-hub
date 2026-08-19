# Local Stack

This is the main non-quickstart example. It runs ai-memory-hub with Postgres
metadata, PGVector vectors, and an observability sidecar stack. It can be used
in two modes:

- **Local smoke**: deterministic local embeddings and no auth, bound to
  `127.0.0.1:8000`.
- **Local server**: Ollama embeddings, Google OAuth, and a public HTTPS URL
  for agents running on another machine. This README uses ngrok as the tunnel
  example.

## Local Smoke

```bash
cd examples/local-stack
docker compose up --build
```

Check readiness:

```text
http://127.0.0.1:8000/ready
```

Observability endpoints are also bound to loopback:

```text
http://127.0.0.1:8000/observability
http://127.0.0.1:16686
http://127.0.0.1:9090
```

## Local Server With OAuth

1. Start Ollama on the host running the hub and pull the embedding model:

```bash
ollama pull nomic-embed-text
```

2. Start ngrok for the hub port:

```bash
ngrok http 8000
```

3. Replace every `https://YOUR-NGROK-DOMAIN.ngrok-free.app` placeholder in
   `config.oauth-ngrok.yaml` with the active public HTTPS URL.

4. In Google Cloud Console, register the matching redirect URI:

```text
https://YOUR-NGROK-DOMAIN.ngrok-free.app/auth/google/callback
```

If the Connect page still shows `YOUR-NGROK-DOMAIN` after startup, stop and fix
`config.oauth-ngrok.yaml` before starting client auth. MCP clients discover
OAuth endpoints from the advertised public URL, so a stale placeholder sends
registration and token flows to the wrong host.

5. Export secrets and start Compose:

```bash
export GOOGLE_CLIENT_ID="your-google-client-id"
export GOOGLE_CLIENT_SECRET="your-google-client-secret"
export AMH_OAUTH_JWT_SECRET="$(openssl rand -base64 48)"
export AMH_SESSION_SECRET="$(openssl rand -base64 48)"
export AMH_CONFIG_FILE=config.oauth-ngrok.yaml
docker compose up --build
```

Remote agents should use:

```text
https://YOUR-NGROK-DOMAIN.ngrok-free.app/mcp
```

The browser setup page is:

```text
https://YOUR-NGROK-DOMAIN.ngrok-free.app/connect
```

Keep the Compose port binding at `127.0.0.1:8000:8000`; your tunnel or reverse
proxy should publish only the hub port, and Postgres stays local.

Jaeger and Prometheus are for local diagnostics only. Do not expose their ports
through the public tunnel.
