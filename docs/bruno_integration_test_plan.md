# Bruno Integration Test Plan

Add a Bruno-based black-box integration layer that exercises a real
ai-memory-hub server through HTTP API and MCP, with persistence backed by the
configured metadata and vector stores.

This layer is for local smoke testing and CI visibility. It does not replace
pytest unit, integration, or end-to-end tests.

## Status

P0 implemented. The initial unauthenticated health, API memory, MCP memory,
bearer auth owner isolation, shared project access, OAuth protected-resource
metadata guard, filter smoke coverage, local workspace, CI config, and GitHub
Actions workflow are implemented.

## Goals

- Provide a local test collection that contributors can run from Bruno Desktop
  or the repo-local Bruno CLI dependency.
- Verify the public API and MCP contracts against a running server instead of
  in-process test clients only.
- Exercise the real persistence path: MCP/API request -> ingestion -> metadata
  DB -> vector store -> search/ask retrieval.
- Produce a human-readable CI report artifact for API/MCP smoke failures.
- Produce machine-readable JUnit results for GitHub Actions test summaries and
  per-job result checks.
- Keep the collection deterministic, secret-safe, and cheap enough for regular
  CI once stable.

## Non-Goals

- Replacing pytest coverage for validation, storage adapters, auth edge cases,
  or internal ranking behavior.
- Testing every malformed payload in Bruno. Negative and boundary cases remain
  better suited to pytest.
- Launching real agent clients. That remains covered by
  `real_client_mcp_smoke_plan.md`.
- Querying Postgres directly from Bruno scripts as the primary assertion path.
  Bruno should assert behavior through the public API/MCP surface.

## Proposed Layout

```text
tests/bruno/
  workspace.yml
  environments/
    local.yml
    ci.yml
  collections/
    ai-memory-hub-integration/
      bruno.json
      01-health/
      02-api-memory/
      03-mcp-memory/
      04-auth-projects/
      05-filters/
```

Environment variables should be explicit and safe:

```text
base_url=http://127.0.0.1:8000
mcp_url=http://127.0.0.1:8000/mcp/
run_id=<local-or-ci-run-id>
bearer_token=<optional>
project_id=<optional>
```

The committed local environment must not contain real secrets.

## P0 Collection Scope

### Health

- `GET /health` returns a successful liveness response.
- `GET /ready` returns a successful readiness response.
- Readiness exposes enough provider state to confirm the server is not running
  in an unexpected degraded mode when the test requires Postgres/PGVector.

### HTTP API Memory Flow

- Insert a unique conversation through `POST /memory/insert`.
- Search for a unique `run_id` phrase through `POST /memory/search`.
- Ask a question through `POST /memory/ask`.
- Assert the response includes the inserted memory, a stable success envelope,
  and usable provenance/citation fields.

### MCP Memory Flow

- Initialize a streamable HTTP MCP session at `/mcp/`.
- Call `tools/list` and assert core tools are present:
  - `memory_validate`
  - `memory_insert`
  - `memory_search`
  - `memory_retrieve`
  - `memory_ask`
- Call `memory_validate` with the same conversation shape used by real clients.
- Call `memory_insert`.
- Call `memory_search`.
- Call `memory_ask`.
- Reuse the MCP session id returned by initialize for subsequent requests.

### Auth And Project Flow

The authenticated profile covers:

- [x] Missing bearer token is rejected for `/memory/*`.
- [x] Missing bearer token is rejected for `/mcp/`.
- [x] A valid bearer token can call API and MCP flows.
- [x] A private memory inserted by one token is not visible to another token.
- [x] A shared project memory is visible to project members.
- [x] A non-member is denied access to the shared project.

This setup should use admin CLI commands or a small CI seed script before
`pnpm exec bru run`; token values must be injected at runtime and never
committed.

### OAuth Guard

The CI workflow starts ai-memory-hub with `api.auth: oauth_resource_server` and
verifies the MCP protected-resource metadata endpoint. Detailed JWT validation
coverage remains in pytest.

### Filter Flow

- [x] Insert conversations with source, date, and tags.
- [x] Verify API/MCP search filters narrow results.
- [x] Verify API/MCP ask filters narrow answer context.
- Verify fact/profile filter smoke coverage only where the setup remains
  readable and deterministic. Detailed combinations remain in pytest.

## Local Workflow

Local contributors should be able to run the collection against either:

1. A local app process:

```bash
uv run aim serve --host 127.0.0.1 --port 8000
```

2. The reusable Postgres/PGVector Compose stack:

```bash
cd examples/storage_providers/postgres-pgvector
docker compose up --build
```

Then run Bruno:

```bash
cd tests/bruno/collections/ai-memory-hub-integration
pnpm exec bru run --global-env local --workspace-path ../.. --sandbox developer --noproxy
```

Use tags to keep local runs focused:

```bash
pnpm exec bru run --global-env local --workspace-path ../.. --tags smoke,mcp --sandbox developer --noproxy
```

## CI Workflow

Add a dedicated GitHub Actions job after the collection is stable locally:

1. Check out the repository.
2. Install Python and project dependencies with `uv`.
3. Start Postgres/PGVector as a service.
4. Start ai-memory-hub on `127.0.0.1:8000` using a CI config with:
   - `interfaces.api: true`
   - `interfaces.mcp: true`
   - `providers.metadata_db: postgres`
   - `providers.vector_db: pgvector`
   - deterministic local embeddings
5. Wait for `/ready`.
6. Install Node.js, pnpm, and the local `tests/bruno` package dependencies.
7. Run:

```bash
cd tests/bruno/collections/ai-memory-hub-integration
pnpm exec bru run \
  --global-env ci \
  --workspace-path ../.. \
  --tags smoke,api,mcp \
  --env-var run_id="${GITHUB_RUN_ID}" \
  --reporter-html ../../reports/bruno-integration-report.html \
  --reporter-junit ../../reports/bruno-integration-junit.xml \
  --sandbox developer \
  --noproxy
```

8. Publish the JUnit XML with
   `EnricoMi/publish-unit-test-result-action@v2`.
9. Upload the HTML and JUnit reports as workflow artifacts.

Start as manual or non-blocking CI. Promote to required PR gating only after the
job is stable and consistently faster than the acceptable CI budget.

## Optional DB Assertion

The main Bruno assertions should use public API/MCP behavior. If a hard
database assertion is needed, add a small CI shell step after Bruno:

```bash
psql "$AMH_TEST_POSTGRES_DSN" \
  -c "select count(*) from conversations where metadata::text like '%${GITHUB_RUN_ID}%';"
```

Keep direct SQL outside Bruno request scripts so the Bruno collection remains
portable for local users.

## Acceptance Criteria

- [x] `tests/bruno` contains a runnable Bruno workspace and collection.
- [x] Local docs explain how to run the collection against `uv run aim serve` and
  the Postgres/PGVector Compose example.
- [x] CI uploads Bruno HTML and JUnit report artifacts.
- [x] CI publishes Bruno JUnit results through the GitHub Actions test report
  check and job summary.
- [x] The collection proves API insert/search/ask and MCP initialize/tools/list/tool
  calls against a live server.
- [x] Requests use Bruno `assert` blocks for stable status and simple
  response-body checks, with scripts kept for cross-request state and richer
  API/MCP payload assertions.
- [x] The collection uses unique run data and can be run repeatedly without manual
  cleanup.
- [x] No bearer tokens, API keys, DSNs with credentials, conversations, or
  embeddings are printed in CI logs beyond safe synthetic test data.
- [x] The unauthenticated smoke collection has been verified with the real Bruno
  CLI locally.

## Rollout

1. Add the Bruno workspace, local/CI environments, and unauthenticated health +
   API + MCP smoke collection.
   Done.
2. Add a local docs section and runbook commands.
   Done.
3. Add a GitHub Actions workflow that uploads HTML/JUnit report artifacts and
   publishes JUnit test results.
   Done.
4. Add authenticated bearer/project tests once token seeding is scripted.
   Done.
5. Add an OAuth fail-fast guard until resource-server mode is implemented.
   Done.
6. Verify the unauthenticated smoke collection with the real Bruno CLI locally.
   Done.
7. Add focused filter smoke tests.
   Done.
8. Promote the Bruno lane to required CI only after several stable runs.
