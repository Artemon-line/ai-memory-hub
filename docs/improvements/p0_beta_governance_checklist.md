# P0 Beta Governance Checklist

Source date: `27-08-2026`

Status: planned

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

- [ ] Run a clean checkout setup with `uv sync --dev --group docs`.
- [ ] Run `uv run python -m ruff check memory tests tools`.
- [ ] Run `uv run python -m pyright`.
- [ ] Run the focused tests added for audit, quarantine, auth, project, and
      archive behavior.
- [ ] Run `uv run pytest tests/unit tests/integration -q`.
- [ ] Run `uv run python tools/prepare_mkdocs.py`.
- [ ] Run `uv run python -m mkdocs build --strict`.
- [ ] Run the README source quick start from a clean checkout.
- [ ] Run the Docker quick start from a clean checkout.
- [ ] Run `examples/local-stack` Compose verification with deterministic
      embeddings.
- [ ] Verify `/health`, `/ready`, insert, search, retrieve, ask, and MCP smoke
      paths against the clean stack.

### Docs Match What Ships

- [ ] Update README status and known-limits text for immutable memory history.
- [ ] Update architecture docs to describe archive/quarantine as visibility
      states rather than destructive mutation.
- [ ] Update feature docs with the exact shipped HTTP endpoints.
- [ ] Update agent docs with the exact shipped MCP tools.
- [ ] Update auth docs with the exact shipped auth modes and provider status.
- [ ] Update planned-features docs so future dashboard, SDK, hosted service,
      destructive deletion, and enterprise governance work is clearly separate
      from beta.
- [ ] Update release notes or release checklist with beta limitations.
- [ ] Run strict docs build after every docs wording change.

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
- [ ] Clean source and Docker/Compose verification can be repeated from a clean
      checkout.
- [ ] README, architecture, feature, agent, auth, and release docs precisely
      match shipped beta behavior.
