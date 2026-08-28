# Examples

Use three human-facing examples:

1. **Quickstart**: the repository root config and root `Containerfile`.
   This uses SQLite + LanceDB with deterministic local embeddings so it starts
   without external databases, OAuth, or model services.
2. **Local stack**: `local-stack`.
   This is the full local-server setup for Postgres metadata, PGVector vectors,
   Ollama embeddings, Google OAuth, and a public HTTPS URL for remote agents.
   The included OAuth template uses ngrok as a local-host-friendly tunnel
   example, but any stable HTTPS reverse proxy or tunnel can use the same
   pattern.
3. **Kubernetes local bearer stack**: `k3s-local`.
   This is the private LAN/VPN-oriented Kubernetes setup for a Raspberry Pi,
   mini PC, or other always-on local server. It uses Postgres, PGVector,
   bearer-token auth, and optional observability. K3s can run the same manifests
   with the small command and image-loading adjustments documented in the
   example README.

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

## Kubernetes Local Bearer Stack

Use this when running the hub on a Raspberry Pi, mini PC, or home Kubernetes
node without exposing it to the public internet:

```bash
cd examples/k3s-local
```

Follow `examples/k3s-local/README.md` to build the image, render local secrets,
apply the Kubernetes manifests, create a bearer token, and expose only the hub
service to your trusted LAN or VPN. For K3s, use the same YAML but import local
images into K3s containerd or push them to a registry the node can pull from.
