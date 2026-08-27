# P0 Beta Governance Checklist

Source date: `27-08-2026`

Status: implemented

Priority: P0 before `v0.1.0-beta`

## Goal

Ship the beta with a clear governed-memory contract: ai-memory-hub keeps
append-only memory history, protects sensitive captures before they enter normal
retrieval, enforces project/auth boundaries consistently, and documents only
the behavior that actually ships.

This plan intentionally does not add destructive memory update or delete
workflows. History is the product record. Corrections, supersession,
quarantine, approval, rejection, archive, and restore are visibility or
governance events over immutable stored history.

## Lifecycle Decision

- [x] Treat accepted memory payloads as immutable history.
- [x] Avoid general-purpose memory update and delete tools for
      `v0.1.0-beta`.
- [x] Document that corrections are represented as new memories, superseding
      facts, or explicit governance events.
- [x] Document that archive remains a post-beta visibility/storage optimization
      and must not erase history when it ships.
- [x] Document that quarantine prevents memories from entering default
      retrieval until approval.
- [x] Ensure public docs do not describe destructive delete/update as a shipped
      beta capability.

## P0 Workstreams

### Audit Events

- [x] Add a durable append-only audit event model for security- and
      governance-relevant actions.
- [x] Record `memory.inserted` events.
- [ ] Record `memory.archived` and `memory.restored` events if archive support
      is added for beta.
- [x] Record `memory.quarantined` events when sensitive-content quarantine
      ships.
- [x] Record `memory.approved` and `memory.rejected`
      events.
- [x] Record `memory.searched`, `memory.retrieved`, and `memory.asked` events
      without storing raw search queries or full memory payloads.
- [x] Record `fact.superseded` events.
- [x] Record `project.access_denied` events for failed project authorization.
- [x] Record auth lifecycle events such as token creation and revocation where
      supported.
- [x] Include actor, project, memory id or fact id, request id, source surface,
      timestamp, outcome, and non-sensitive reason codes.
- [x] Add metadata and storage-provider tests for audit event persistence.
- [x] Add API/MCP tests proving audit events are emitted for representative
      write, read, denial, and review paths.

### Secrets And PII Quarantine

- [x] Add an insert-time sensitive-content scanner before normal persistence.
- [x] Detect and quarantine likely API keys, bearer tokens, private keys,
      passwords, credential URLs, and connection strings.
- [x] Detect and quarantine high-confidence sensitive PII patterns such as
      credit cards, SSNs, passport-like identifiers, and private key material.
- [x] Keep normal names, ordinary email addresses, and project references out of
      the default quarantine path unless stricter rules are configured.
- [x] Return safe reason codes for quarantine decisions.
- [x] Store quarantined content outside default search, retrieve, ask, fact, and
      profile flows.
- [x] Expose quarantine review through the existing pending-review/admin flow or
      a small beta-specific review surface.
- [x] Emit audit events for quarantine, approval, and rejection.
- [x] Add tests for secret detection, false-positive tolerance, approval,
      rejection, and retrieval exclusion.

### Auth And Project Negative Tests

- [x] Add HTTP tests showing protected memory routes fail without credentials
      when auth is enabled.
- [x] Add HTTP tests for invalid, expired, or revoked tokens.
- [x] Add HTTP tests showing a user cannot read, ask, retrieve, or write another
      project without the required role.
- [x] Add HTTP tests showing reader, writer, owner, and admin roles cannot
      perform actions outside their permissions.
- [x] Add MCP tests proving the same auth and project checks apply through MCP
      tools.
- [x] Add tests showing access-denied paths do not reveal private memory text,
      fact text, project metadata, or existence details beyond the chosen error
      contract.
- [x] Add tests showing quarantined memories are excluded from normal search and
      ask; archived memory tests wait until archive support ships.

### Archive Visibility

- [x] Decide whether archive/restore ships in `v0.1.0-beta` or remains a P1
      optimization.
- [x] Keep archived memory state, archive/restore endpoints, and archive/restore
      MCP tools out of `v0.1.0-beta`.
- [x] Document future archive as a visibility state over preserved history, not
      history deletion.
- [x] Leave archive exclusion, administrative history reads, and
      `memory.archived`/`memory.restored` audit events as P1 implementation
      work with archive support.
- [x] If archive does not ship, document it as a near-term optimization and keep
      destructive delete/update out of beta docs.

### Clean Install And Compose Verification

- [x] Run a clean checkout setup with `uv sync --dev --group docs`.
- [x] Run `uv run python -m ruff check memory tests tools`.
- [x] Run `uv run python -m pyright`.
- [x] Run the focused tests added for audit, quarantine, auth, project, and
      archive behavior.
- [x] Run `uv run pytest tests/unit tests/integration -q`.
- [x] Run `uv run python tools/prepare_mkdocs.py`.
- [x] Run `uv run python -m mkdocs build --strict`.
- [x] Run the README source quick start from a clean checkout.
- [x] Run the Docker quick start from a clean checkout.
- [x] Run `examples/local-stack` Compose verification with deterministic
      embeddings.
- [x] Verify `/health`, `/ready`, insert, search, retrieve, ask, and MCP smoke
      paths against the clean stack.

### Docs Match What Ships

- [x] Update README status and known-limits text for immutable memory history.
- [x] Update architecture docs to describe archive/quarantine as visibility
      states rather than destructive mutation.
- [x] Update feature docs with the exact shipped HTTP endpoints.
- [x] Update agent docs with the exact shipped MCP tools.
- [x] Update auth docs with the exact shipped auth modes and provider status.
- [x] Update planned-features docs so future dashboard, SDK, hosted service,
      destructive deletion, and enterprise governance work is clearly separate
      from beta.
- [x] Update release notes or release checklist with beta limitations.
- [x] Run strict docs build after every docs wording change.

## Verification Evidence

Clean checkout path:
`C:/Users/tyran/.codex/visualizations/2026/08/27/01a041f5-840a-7c63-b3dd-1c982ce3ed7c/amh-phase4-clean-4f7c9a`.

Phase 4 verification on `2026-08-27`:

- `uv sync --dev --group docs` passed from the clean checkout.
- `uv run python -m ruff check memory tests tools` passed.
- `uv run python -m pyright` passed with 0 errors.
- Focused P0 governance slice passed with 20 tests:
  audit events, sensitive-content quarantine, auth/project negative paths, and
  the no-destructive beta HTTP/MCP surface guard.
- `uv run pytest tests/unit tests/integration -q` passed with 605 passed,
  17 skipped, and 14 warnings.
- `uv run python tools/prepare_mkdocs.py` passed.
- `uv run pytest tests/unit/test_docs_build.py -q` passed with 2 tests.
- `uv run python -m mkdocs build --strict` passed.
- README source quick start passed by serving on `127.0.0.1:8010`, then
  verifying `/ready`, `/health`, insert, search, retrieve, ask, and MCP
  initialize.
- Docker quick start passed with `docker build -t ai-memory-hub:phase4-docker-smoke
  -f Containerfile .`, then serving on `127.0.0.1:8012` and verifying
  `/ready`, `/health`, insert, search, ask, and MCP initialize.
- `docker compose -f examples/local-stack/compose.yaml config` passed against
  the checked-in local-stack Compose file.
- `examples/local-stack` deterministic Compose smoke passed with Postgres,
  PGVector, local deterministic embeddings, and the hub on `127.0.0.1:8000`.
  The smoke verified `/ready`, `/health`, insert, search, retrieve, ask, MCP
  initialize, and MCP `tools/list`.

Local runtime note: Docker Desktop could not pull Docker Hub images because the
daemon returned `authentication required - incorrect username or password`,
including for `hello-world`. The Compose runtime smoke therefore used a
temporary clean-checkout override for the Postgres image only, after importing
the exact pinned `pgvector/pgvector@sha256:a36250871de0833b8757561c72f2477ef1ddd1101afa4e617fb552e0de514c6b`
image through WSL Podman. The checked-in Compose file itself was not changed for
that registry-auth workaround. A separate portability issue found during the
smoke was fixed by keeping Postgres inside the Compose network instead of
publishing host port `5432`.

## Acceptance Criteria

- [x] The beta contract says memory is append-only history.
- [x] No public beta docs promise destructive update or delete.
- [x] Audit events exist for critical write, read, denial, review, and auth
      lifecycle actions.
- [x] Secrets and high-confidence sensitive PII are blocked or quarantined
      before normal retrieval.
- [x] Quarantined content is excluded from default search, retrieve, ask, fact,
      profile, and graph paths.
- [x] Project and auth negative tests cover both HTTP and MCP surfaces.
- [x] Archive/restore does not ship in beta; archived content exclusion tests
      remain P1 with archive support.
- [x] Clean source and Docker/Compose verification can be repeated from a clean
      checkout.
- [x] README, architecture, feature, agent, auth, and release docs precisely
      match shipped beta behavior.
