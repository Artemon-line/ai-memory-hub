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
cd examples/storage_providers/postgres-pgvector
docker compose up --build
```

Then run the collection from the repository root:

```bash
cd tests/bruno/collections/ai-memory-hub-integration
pnpm exec bru run --global-env local --workspace-path ../.. --sandbox developer --noproxy
```

Run only the MCP smoke requests:

```bash
pnpm exec bru run --global-env local --workspace-path ../.. --tags mcp --sandbox developer --noproxy
```

Run only the source/date/tag filter smoke requests:

```bash
pnpm exec bru run --global-env local --workspace-path ../.. --tags filters --sandbox developer --noproxy
```

Run the bearer auth and shared-project cases against an auth-enabled server:

```bash
uv run python tests/bruno/seed_auth.py
uv run aim serve --config tests/bruno/config.auth.ci.yaml --host 127.0.0.1 --port 8000
cd tests/bruno/collections/ai-memory-hub-integration
pnpm exec bru run --global-env local --workspace-path ../.. --tags auth --env-var run_id="$(date +%s)" --sandbox developer --noproxy
```

The OAuth case is a CI guard that starts `api.auth: oauth_resource_server` and
checks the protected-resource metadata endpoint.

Override the run id when you want stable test data:

```bash
pnpm exec bru run --global-env local --workspace-path ../.. --env-var run_id="$(date +%s)" --sandbox developer --noproxy
```

## CI Reports

The GitHub Actions Bruno workflow writes Bruno's native HTML and JUnit reports
under `tests/bruno/reports`. CI uploads that directory as the
`bruno-integration-report` artifact and publishes the JUnit XML through the
GitHub test result check/job summary.

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
- API/MCP source, date range, and tag filter smoke coverage
- bearer-token missing-token rejection for API and MCP
- private owner isolation
- shared-project API search/ask across two project members
- shared-project MCP search with bearer auth
- OAuth resource-server protected-resource metadata guard in CI

Detailed validation, auth edge cases, storage adapter behavior, and ranking
internals remain covered by pytest.
