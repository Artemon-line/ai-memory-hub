# Bruno Integration Tests

This workspace contains black-box smoke tests for a running ai-memory-hub
server. The collection calls the public HTTP API and streamable HTTP MCP
endpoint, then verifies inserted memory can be found and cited through search
and ask.

## Local Run

Start ai-memory-hub first:

```bash
uv run aim serve --config examples/container/config.yaml --host 127.0.0.1 --port 8000
```

Or use the Postgres/PGVector Compose example:

```bash
cd examples/postgres/pgvector
docker compose up --build
```

Then run the collection from the repository root:

```bash
cd tests/bruno/collections/ai-memory-hub-integration
bru run --global-env local --workspace-path ../.. --sandbox developer --noproxy
```

Run only the MCP smoke requests:

```bash
bru run --global-env local --workspace-path ../.. --tags mcp --sandbox developer --noproxy
```

Run the bearer auth and shared-project cases against an auth-enabled server:

```bash
uv run python tests/bruno/seed_auth.py
uv run aim serve --config tests/bruno/config.auth.ci.yaml --host 127.0.0.1 --port 8000
cd tests/bruno/collections/ai-memory-hub-integration
bru run --global-env local --workspace-path ../.. --tags auth --env-var run_id="$(date +%s)" --sandbox developer --noproxy
```

The OAuth case is currently a CI guard that confirms `api.auth:
oauth_resource_server` fails explicitly because OAuth resource-server mode is
not implemented yet.

Override the run id when you want stable test data:

```bash
bru run --global-env local --workspace-path ../.. --env-var run_id="$(date +%s)" --sandbox developer --noproxy
```

## Scope

The executable P0 slice covers:

- `/health`
- `/ready`
- `POST /memory/insert`
- `POST /memory/search`
- `POST /memory/ask`
- MCP `initialize`
- MCP `tools/list`
- MCP `memory_validate`
- MCP `memory_insert`
- MCP `memory_search`
- MCP `memory_ask`
- bearer-token missing-token rejection for API and MCP
- private owner isolation
- shared-project API search/ask across two project members
- shared-project MCP search with bearer auth
- OAuth resource-server fail-fast guard in CI

Detailed validation, auth edge cases, storage adapter behavior, and ranking
internals remain covered by pytest.
