# Test Guide

This directory contains the project test suites:

- `unit/`: fast unit tests for configuration, CLI behavior, ingestion helpers,
  MCP helpers, docs checks, and benchmarks.
- `integration/`: in-process API/storage tests and optional live storage checks.
- `e2e/`: MCP and real-client smoke scenarios.
- `bruno/`: black-box API/MCP tests for a running ai-memory-hub server.

## Python Test Requirements

Install the project development dependencies:

```bash
uv sync --dev
```

For live Postgres/PGVector tests, also install the Postgres extra:

```bash
uv sync --dev --extra postgres
```

Run the default Python suite:

```bash
uv run pytest
```

Run the usual fast CI-equivalent Python checks:

```bash
uv run pytest tests/unit tests/integration
```

Run docs checks:

```bash
uv run pytest tests/unit/test_docs_build.py -q
uv run python tools/prepare_mkdocs.py
uv run python -m mkdocs build --strict
```

Run lint checks used by the Bruno workflow:

```bash
uv run python -m ruff check tests/bruno/seed_auth.py tests/bruno/validate_files.py
uv run python tests/bruno/validate_files.py
```

## Optional Live Postgres/PGVector Tests

Default Python tests do not require Postgres. To run live Postgres and PGVector
tests locally:

```bash
docker run --name aim-pgvector-test \
  -e POSTGRES_USER=test \
  -e POSTGRES_PASSWORD=test \
  -e POSTGRES_DB=memory \
  -p 5432:5432 \
  -d pgvector/pgvector:pg16

export AMH_TEST_POSTGRES_DSN="postgresql://test:test@127.0.0.1:5432/memory"
uv run pytest -q tests/integration/test_storage_features.py -k "postgres_live_integration_when_dsn_provided or postgres_schema_version or pgvector_live_integration_when_dsn_provided or runtime_postgres_pgvector_live_integration_when_dsn_provided"

docker rm -f aim-pgvector-test
```

## Bruno Requirements

Bruno tests require:

- Node.js and npm.
- pnpm.
- The repo-local Bruno CLI dependency declared in `tests/bruno/package.json`.
- A running ai-memory-hub server.

Install the local Bruno test dependencies:

```bash
cd tests/bruno
corepack enable
pnpm install
```

Check the CLI is available:

```bash
pnpm exec bru --version
```

The local commands use `--sandbox developer` because the MCP `.bru` tests import
the shared helper at `tests/bruno/collections/ai-memory-hub-integration/scripts/mcp.js`.
They also use `--noproxy` so localhost calls do not go through system proxy
configuration.

## Bruno Smoke Tests

Start ai-memory-hub with local deterministic embeddings:

```bash
uv run aim serve --config examples/container/config.yaml --host 127.0.0.1 --port 8000
```

In another shell, run the smoke collection:

```bash
cd tests/bruno/collections/ai-memory-hub-integration
pnpm exec bru run --global-env local --workspace-path ../.. --tags smoke --env-var run_id="$(date +%s)" --sandbox developer --noproxy
```

Run only the MCP smoke requests:

```bash
pnpm exec bru run --global-env local --workspace-path ../.. --tags mcp --env-var run_id="$(date +%s)" --sandbox developer --noproxy
```

## Bruno Auth And Shared Project Tests

The auth/project Bruno suite expects Postgres/PGVector because it uses
`tests/bruno/config.auth.ci.yaml`.

Start Postgres/PGVector:

```bash
docker run --name aim-bruno-pgvector \
  -e POSTGRES_USER=test \
  -e POSTGRES_PASSWORD=test \
  -e POSTGRES_DB=memory \
  -p 5432:5432 \
  -d pgvector/pgvector:pg16
```

Seed synthetic users, bearer tokens, and the shared project:

```bash
uv sync --dev --extra postgres
uv run python tests/bruno/seed_auth.py
```

Start the auth-enabled server:

```bash
uv run aim serve --config tests/bruno/config.auth.ci.yaml --host 127.0.0.1 --port 8000
```

Run the auth/project Bruno suite:

```bash
cd tests/bruno/collections/ai-memory-hub-integration
pnpm exec bru run --global-env local --workspace-path ../.. --tags auth --env-var run_id="$(date +%s)" --sandbox developer --noproxy
```

Clean up:

```bash
docker rm -f aim-bruno-pgvector
```

## OAuth Guard

OAuth resource-server mode is not implemented yet. The GitHub workflow verifies
that `api.auth: oauth_resource_server` fails explicitly with:

```text
api.auth=oauth_resource_server is not implemented yet
```

Replace this guard with live OAuth protected-resource metadata and token
validation tests when OAuth is implemented.
