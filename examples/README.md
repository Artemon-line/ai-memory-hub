# Examples

Use two human-facing examples:

1. **Quickstart**: the repository root config and root `Containerfile`.
   This uses SQLite + LanceDB with deterministic local embeddings so it starts
   without external databases, OAuth, or model services.
2. **Local stack**: `local-stack`.
   This is the full local-server setup for Postgres metadata, PGVector vectors,
   Ollama embeddings, Google OAuth, and a public HTTPS URL for remote agents.
   The included OAuth template uses ngrok as a local-host-friendly tunnel
   example, but any stable HTTPS reverse proxy or tunnel can use the same
   pattern.

Provider-specific directories under `storage_providers` are still kept as
maintainer fixtures for CI, contract testing, and advanced adapter work. New
users should start with one of the two examples above.

## Quickstart

From the repository root:

```bash
uv sync --dev
uv run aim serve --host 127.0.0.1 --port 8000
```

Or with the root container image:

```bash
docker build -t ai-memory-hub:local -f Containerfile .
docker run --rm -p 127.0.0.1:8000:8000 ai-memory-hub:local
```

## Local Stack

Use this when running the hub on a desktop machine and connecting agents from a
different laptop or client machine:

```bash
cd examples/local-stack
docker compose up --build
```

For the OAuth path, set `AMH_CONFIG_FILE=config.oauth-ngrok.yaml`, replace the
placeholder ngrok URL in that config with your active public HTTPS base URL, and
export the Google/OAuth secrets required by `compose.yaml`. If you use something
other than ngrok, keep `public_base_url`, callback URLs, and the Google redirect
URI pointed at that same HTTPS origin.
