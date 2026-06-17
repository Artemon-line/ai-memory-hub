# Codex to opencode MCP Memory Handoff Test

This runbook tests this path:

```text
Codex chat -> ai-memory-hub MCP memory_insert -> close Codex
          -> open opencode -> ai-memory-hub MCP memory_ask/search
          -> opencode answers from the Codex conversation
```

It uses Docker or Podman for ai-memory-hub and Postgres with PGVector.

## Current Codebase State

- The API server is `memory.api.server:app`.
- The MCP endpoint is mounted at `http://127.0.0.1:8000/mcp/` when `interfaces.mcp: true`.
- MCP tools currently implemented:
  - `memory_validate`
  - `memory_insert`
  - `memory_search`
  - `memory_retrieve`
  - `memory_ask`
- MCP prompts currently implemented:
  - `save_conversation`
  - `search_memory`
  - `ask_memory`
  - `summarize_conversation`
- Metadata backends currently implemented:
  - SQLite
  - Postgres
- Vector backends currently implemented:
  - LanceDB
  - PGVector
  - in-memory
- `tests/e2e/test_client_smoke_profiles.py` already has contract profiles for Codex-shaped and opencode-shaped MCP payloads.
- The existing `Containerfile` installs the optional `postgres` extra, so it can be used for Postgres/PGVector container runs.
- The existing `Containerfile` also installs the optional `tokenizer` extra and prewarms `cl100k_base` for `tiktoken`.
- ai-memory-hub does not currently scrape Codex or opencode transcript files. The client must call MCP tools and send a structured conversation payload.

## Prerequisites

- Docker Desktop with WSL integration, or Docker Engine with the Compose plugin.
- Podman also works when `podman-compose` or a `podman compose` provider is installed.
- WSL users should run the container commands from the WSL shell inside the repo checkout.
- Codex CLI installed and authenticated.
- opencode installed and authenticated.
- Port `8000` available on the host.
- Port `8000` allowed through the host firewall if another PC on the same network will connect.
- Port `5432` available on the host loopback interface, or change the Postgres port mapping in the compose file.

## Use The Checked-In Container Example

This test uses the existing repo-root `Containerfile`. That file must install the
Postgres extra:

```dockerfile
RUN python -m pip install --upgrade pip && \
    pip install --no-cache-dir uv && \
    uv sync --frozen --no-dev --extra postgres
```

The existing `Containerfile` copies `example.config.yaml` to `/app/config.yaml`.
For this test, `examples/postgres/pgvector/compose.yaml` mounts
`examples/postgres/pgvector/config.yaml` over `/app/config.yaml` at runtime.
The config bind mount uses the `:Z` SELinux relabel option so rootless Podman works on SELinux-enabled Linux hosts. Docker accepts this option on Linux.

The config uses `providers.embeddings: local` to avoid requiring Ollama or OpenAI embeddings for this handoff test. It also enables `tokenizer.enabled: true` with `cl100k_base`, so `memory_ask` returns token-budget diagnostics using `tiktoken` when available. That keeps the test focused on MCP, Postgres, PGVector, tokenizer budgeting, and cross-client access.

If you want to use Ollama embeddings from the same PC that runs Docker instead
of deterministic local embeddings, start Compose with `config.ollama.yaml`:

```bash
AMH_CONFIG_FILE=config.ollama.yaml docker compose up --build
```

The default uses `host.docker.internal`, which points from the container back to
the host PC:

```yaml
providers:
  embeddings: openai
  embedding_model: nomic-embed-text
  embedding_dimension: 768

openai:
  base_url: http://host.docker.internal:11434/v1
  api_key: ollama
```

Do not use `http://localhost:11434/v1` inside the ai-memory-hub container unless
Ollama is running in that same container. From a container, `localhost` is the
container itself.

Podman users may need this host alias instead:

```yaml
openai:
  base_url: http://host.containers.internal:11434/v1
  api_key: ollama
```

If Ollama is on a different PC, replace `host.docker.internal` with that PC's
LAN hostname or IP, for example `http://impression-pc:11434/v1`.

## Start The Stack

From the repo root in WSL/Linux:

```bash
cd examples/postgres/pgvector
docker compose up --build
```

Keep that terminal running. Podman users can run `podman-compose up --build`, or
`podman compose up --build` after installing a Compose provider.

If `podman compose up --build` fails with `looking up compose provider failed`,
Podman is installed but no Compose provider is available. Use Docker Compose as
shown above, or install one Podman provider:

```bash
python3 -m pip install --user podman-compose
podman-compose up --build
```

The Compose example publishes ai-memory-hub on `0.0.0.0:8000`, so clients on
another PC can use the host machine's LAN IP. It keeps Postgres bound to
`127.0.0.1:5432` because remote clients do not need direct database access.

The checked-in config keeps `api.auth: none` so local smoke tests work without a
setup step. Before using this listener from another PC, create a local admin
user, issue a bearer token, and switch the mounted config to `bearer_token`.
The token create command prints the raw token once; store it in your shell or
password manager because later list commands do not expose it again:

```bash
cd examples/postgres/pgvector

AMH_OWNER="personal"
docker compose exec -T ai-memory-hub \
  uv run aim admin user create "$AMH_OWNER" --display-name "$AMH_OWNER" --json

TOKEN_JSON="$(docker compose exec -T ai-memory-hub \
  uv run aim admin token create --user "$AMH_OWNER" --display-name lan-client --json)"
AMH_TOKEN="$(printf '%s\n' "$TOKEN_JSON" | python3 -c 'import json,sys; print(json.load(sys.stdin)["token"])')"
printf 'ai-memory-hub bearer token: %s\n' "$AMH_TOKEN"

sed -i 's/^  auth: none$/  auth: bearer_token/' config.yaml
docker compose restart ai-memory-hub
```

If you started Compose with `AMH_CONFIG_FILE=config.ollama.yaml`, run the `sed`
command against `config.ollama.yaml` instead. After enabling bearer auth, direct
HTTP calls need this header:

```bash
-H "Authorization: Bearer $AMH_TOKEN"
```

Find the host LAN IP from WSL/Linux:

```bash
hostname -I | awk '{print $1}'
```

In the examples below:

- use `http://127.0.0.1:8000` from the same machine.
- use `http://<HOST_LAN_IP>:8000` from another PC on the same network.

If using Ollama embeddings, verify the hub container can reach Ollama:

```bash
docker compose exec ai-memory-hub \
  python -c "import urllib.request; print(urllib.request.urlopen('http://host.docker.internal:11434/api/tags', timeout=5).status)"
```

Expected output is `200`. Also make sure the embedding model is pulled on the
Ollama PC:

```bash
ollama pull nomic-embed-text
```

In a second terminal, verify the API is reachable:

```bash
curl -sS http://127.0.0.1:8000/memory/search \
  -H "Content-Type: application/json" \
  -d '{"query":"startup smoke","top_k":1}'
```

Expected result before inserting anything:

```json
{
  "status": "ok",
  "results": []
}
```

If the hub exits with `psycopg package is required`, the image was not built with `--extra postgres`. Recheck the existing `Containerfile`.

## Optional Direct MCP Smoke Test

Initialize an MCP session:

```bash
curl -sS -D /tmp/amh-mcp-headers.txt \
  -o /tmp/amh-mcp-init.txt \
  -X POST http://127.0.0.1:8000/mcp/ \
  -H "Content-Type: application/json" \
  -H "Accept: application/json, text/event-stream" \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"manual-smoke","version":"0.1"}}}'

SESSION_ID="$(
  awk 'BEGIN { IGNORECASE=1 } /^mcp-session-id:/ { gsub("\r", "", $2); print $2 }' \
    /tmp/amh-mcp-headers.txt
)"

printf '%s\n' "$SESSION_ID"
```

List tools:

```bash
curl -sS http://127.0.0.1:8000/mcp/ \
  -H "Content-Type: application/json" \
  -H "Accept: application/json, text/event-stream" \
  -H "Mcp-Session-Id: $SESSION_ID" \
  -d '{"jsonrpc":"2.0","id":2,"method":"tools/list","params":{}}'
```

You should see the `memory_*` tools in the event-stream response.

Check that the container is using `tiktoken`:

```bash
cd examples/postgres/pgvector
docker compose exec ai-memory-hub \
  uv run python -m memory.cli tokenizer-check --json
```

Expected `tokenizer_used` starts with `tiktoken:`.

## Configure Codex

Codex configuration lives in `~/.codex/config.toml` unless you use a different `CODEX_HOME`.

If Codex runs on the same machine as ai-memory-hub, add:

```toml
[mcp_servers.ai_memory_hub]
url = "http://127.0.0.1:8000/mcp/"
```

If Codex runs on another PC on the same network, use the host LAN IP:

```toml
[mcp_servers.ai_memory_hub]
url = "http://<HOST_LAN_IP>:8000/mcp/"
```

Codex's current config reference documents `mcp_servers.<id>.url` as the endpoint for a streamable HTTP MCP server.

Restart Codex after changing the config.

If the hub runs inside WSL and Codex runs as a Windows-native process on the same
machine, first try
`http://127.0.0.1:8000/mcp/`. WSL usually forwards localhost to Windows. If that
does not work, get the WSL IP with `hostname -I` inside WSL and use
`http://<WSL_IP>:8000/mcp/`.

## Configure opencode

opencode can register remote MCP servers under the `mcp` config key.

Add this to your opencode config, usually `opencode.json` or `opencode.jsonc`.
Use `127.0.0.1` on the same machine, or `<HOST_LAN_IP>` from another PC:

```jsonc
{
  "$schema": "https://opencode.ai/config.json",
  "mcp": {
    "ai_memory_hub": {
      "type": "remote",
      "url": "http://<HOST_LAN_IP>:8000/mcp/",
      "enabled": true,
      "timeout": 30000
    }
  }
}
```

Restart opencode after changing the config.

## Run The Real Handoff Test

### 1. Start a new Codex conversation

Use a random but memorable topic. Example:

```text
Let's talk about a made-up project called Velvet Lantern.
The key facts are:
- Velvet Lantern is a CLI for cataloging old sci-fi paperbacks.
- The indexing strategy is "nebula shelves".
- My favorite command name is "lantern glow".

Reply naturally and ask one follow-up question.
```

Continue for one or two turns so the conversation has enough content.

### 2. Ask Codex to save the conversation

Send this prompt in the same Codex session:

```text
Use the ai_memory_hub MCP server to save this conversation.

Call memory_validate first, then memory_insert, then memory_retrieve.
Use source "codex".
Use title "Codex Velvet Lantern test".
Tag it with "codex", "opencode-handoff", and "velvet-lantern".
Do not include this save instruction in the stored messages.
After retrieval, tell me the stored memory id.
```

Expected behavior:

- Codex calls `memory_validate`.
- Codex calls `memory_insert`.
- Codex calls `memory_retrieve` with the returned ID.
- Codex gives you a UUID memory ID.

Keep that memory ID for debugging, but the opencode test should work without it.

### 3. Verify persistence outside Codex

In WSL/Linux:

```bash
curl -sS http://127.0.0.1:8000/memory/ask \
  -H "Content-Type: application/json" \
  -d '{"question":"What did I discuss with Codex about Velvet Lantern and nebula shelves?","top_k":5}'
```

Expected:

- `status` is `ok`.
- `answer` mentions Velvet Lantern, sci-fi paperbacks, nebula shelves, or lantern glow.
- `citations` includes the stored Codex memory ID.

### 4. Close Codex

Exit the Codex session completely.

### 5. Open opencode

Start opencode in any workspace where the opencode MCP config is active.

Ask:

```text
Use ai_memory_hub to answer this from memory:
What was my recent Codex conversation about? Include the specific project name, indexing strategy, and command name if available.
```

Expected behavior:

- opencode calls `memory_ask` or `memory_search` on `ai_memory_hub`.
- opencode answers from the Codex-saved memory.
- The answer should mention:
  - `Velvet Lantern`
  - `nebula shelves`
  - `lantern glow`
  - that the memory came from a Codex conversation, if it uses source metadata.

## Useful Debug Commands

Search directly through HTTP:

```bash
curl -sS http://127.0.0.1:8000/memory/search \
  -H "Content-Type: application/json" \
  -d '{"query":"Velvet Lantern nebula shelves lantern glow","top_k":5}'
```

Retrieve a known memory:

```bash
curl -sS http://127.0.0.1:8000/memory/retrieve \
  -H "Content-Type: application/json" \
  -d '{"id":"PASTE_MEMORY_ID_HERE"}'
```

Inspect Postgres tables:

```bash
cd examples/postgres/pgvector
docker compose exec postgres \
  psql -U memory -d memory -c "\dt"
```

Check metadata rows:

```bash
cd examples/postgres/pgvector
docker compose exec postgres \
  psql -U memory -d memory -c "select id, source, title, created_at from conversations order by created_at desc limit 5;"
```

Check vector rows:

```bash
cd examples/postgres/pgvector
docker compose exec postgres \
  psql -U memory -d memory -c "select memory_id, chunk_index, role, left(text, 80) from memory_vectors order by memory_id, chunk_index limit 10;"
```

Watch hub logs:

```bash
cd examples/postgres/pgvector
docker compose logs -f ai-memory-hub
```

## Cleanup

Stop containers but keep stored memory:

```bash
cd examples/postgres/pgvector
docker compose down
```

Delete the test database and stored memory:

```bash
cd examples/postgres/pgvector
docker compose down -v
```

## Known Limitations For This Test

- Codex and opencode must be explicitly prompted to use the MCP server. ai-memory-hub does not automatically capture their transcripts.
- The current `save_conversation` MCP prompt helps agents build payloads, but the client still decides whether and how to call tools.
- The Docker/Podman instructions use `providers.embeddings: local` with 32-dimensional deterministic embeddings. That is best for this integration test. For more realistic semantic retrieval, switch to OpenAI or Ollama-compatible embeddings and set `embedding_dimension` to match the embedding model.
- The checked-in `Containerfile` must install `psycopg` through `uv sync --frozen --no-dev --extra postgres` before using Postgres in containers.
- The checked-in `Containerfile` must install `tiktoken` through `uv sync --frozen --no-dev --extra tokenizer` before precise token budgeting is available.
- `storage.vector.allow_fallback: false` is intentional. If PGVector fails, the container should fail instead of silently using in-memory vectors.
- The Compose example exposes ai-memory-hub on the LAN. Keep `api.auth: none`
  only for local smoke tests; use `api.auth: bearer_token` before trusted-LAN
  use, and do not expose it on untrusted networks without TLS, a VPN, or a
  trusted reverse proxy.

## References

- ai-memory-hub README: `README.md`
- MCP profile smoke plan: `docs/mcp_client_smoke_plan.md`
- Codex config reference: https://developers.openai.com/codex/config-reference
- opencode MCP server docs: https://opencode.ai/docs/mcp-servers/
