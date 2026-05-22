# Storage-Agnostic BYOA Plan (Adjusted)

Plan for evolving `ai-memory-hub` from SQLite-first persistence to a storage-agnostic
architecture with explicit provider capabilities, schema compatibility checks, vector dimensionality guards,
controlled fallback behavior, and dry-run support.

## Scope and Constraints

- Modify only storage contracts, provider adapters, and factory wiring.
- Do not modify domain logic semantics.
- Do not change API/MCP interface or response shapes.

## Goals

- Keep storage providers pluggable through stable interfaces.
- Make optional provider features explicit and discoverable.
- Fail fast on metadata schema incompatibility.
- Prevent vector dimension mismatch errors at runtime.
- Keep hub available when vector provider fails to initialize only when explicitly allowed.
- Support safe dry-run execution for write paths.

## Non-Goals

- Redesigning domain workflows.
- Changing public API/MCP contracts.
- Broad provider expansion in this change set.

## Target Architecture

Layered storage design:

1. domain layer (unchanged behavior)
2. storage contracts (`MetadataStore`, `VectorStore`)
3. provider adapters (SQLite, LanceDB, others)
4. factory/wiring (startup selection, validation, fallback)

Core rule:

- Domain code depends on storage interfaces only.
- Optional capabilities are exposed but never required by domain logic.

## 1) Provider Capabilities Contract

Extend both `MetadataStore` and `VectorStore` with:

- `capabilities() -> ProviderCapabilities`

`ProviderCapabilities` fields:

- `supports_batch_insert: bool = False`
- `supports_transactions: bool = False`
- `supports_ttl: bool = False`
- `supports_tags: bool = False`
- `supports_metadata_indexing: bool = False`

Rules:

- Defaults are `False` in the base contract.
- Each adapter overrides only fields it actually supports.
- Domain layer must not rely on optional capabilities for correctness.
- Unsupported optional operations must raise deterministic `NotSupportedError`
(or equivalent storage-layer error type), never silent no-op.

Implementation notes:

- Add a typed capabilities object in contracts (dataclass or TypedDict).
- Include capabilities in adapter health/debug internals if useful, but do not alter API/MCP payloads.

## 2) Metadata Schema Versioning

Add schema version tracking for metadata providers.

Contract additions:

- `schema_version: int` property or equivalent accessor on metadata store.
- `health()` must expose effective schema version.

Adapter behavior:

- SQLite adapter returns fixed schema version (initial value `1`).
- Postgres adapter reads schema version from `schema_version` table.
- On startup/initialization, fail fast if version is missing or incompatible.

Compatibility policy:

- Define supported version set/range in factory or adapter constants.
- Raise deterministic configuration/runtime error with actionable message.

Postgres version table policy:

- Enforce single-row schema version invariant.
- Read version in a transactionally consistent way.
- Define behavior for rolling deploys with mixed app versions (document supported overlap window or fail-fast policy).

## 3) Vector Dimensionality Validation

Extend vector contract with expected dimensionality and strict validation.

Contract additions:

- `expected_dimensionality: int`
- Validation in `insert()` and `search()` against incoming vectors.

Requirements:

- Raise deterministic error when embedding size mismatches expected dimension.
- Error message must include expected and actual dimensions.
- Add startup check in factory: embedding provider dimension vs vector store `expected_dimensionality`.
- Dimension mismatch is a hard error and must not trigger vector fallback.

Runtime drift policy:

- Re-validate dimensions on every insert/search call.
- If embedding provider configuration changes at runtime, fail fast on first mismatched request with deterministic error.

## 4) Provider Fallback Strategy

Implement explicit vector-store fallback only.

Behavior:

- If configured vector store initialization fails, fallback to in-memory vector store only when explicitly enabled.
- Log warning with sanitized failure reason and fallback provider.
- Hub continues startup only under allowed fallback policy.

Rules:

- No silent degradation: fallback event must be clearly logged.
- Metadata store must not fallback; metadata initialization failure is fatal.
- Fallback scope is factory/wiring only.
- Fallback activation must be reflected in health() as degraded state.
- Fallback vector store must adopt the same `expected_dimensionality` as the failed provider.

Configuration:

```yaml
storage:
  vector:
    allow_fallback: true | false
```

Policy:

- Recommended default for production: `allow_fallback: false`.
- `allow_fallback: true` may be used for local/dev or explicitly tolerated degraded environments.

## 5) Dry-Run Mode

Add storage config flag:

```yaml
storage:
  dry_run: true | false
```

Behavior when `dry_run = true`:

- All storage writes are no-ops.
- Reads continue to operate normally.
- Vector search continues to operate normally.
- Insert operations still run validation (including dimensionality checks) but skip persistence.
- Log `DRY-RUN: write skipped` for write operations.

Rules:

- Must not break ingestion pipeline execution flow.
- Must not alter API/MCP response shapes.
- Implement via storage-layer wrappers/decorators to avoid domain changes.

Return contract in dry-run:

- Write methods must return the same shape/type as non-dry-run paths.
- For ID-returning inserts, return deterministic IDs using
  the existing application ID strategy (no storage-generated IDs required).
- Never return `None` where normal flow returns an ID/object.

## Operational Safety

- Secret-safe logging:
  - redact DSNs, credentials, tokens, and connection secrets from all storage/fallback errors.
  - include provider name, error class, and safe diagnostic context only.
- Explicit degradation signaling:
  - `health()` exposes machine-readable mode/state: `ok`, `degraded`, or `dry_run`.
  - include active metadata/vector providers and whether vector fallback is active.
- Startup policy visibility:
  - log whether `allow_fallback` and `dry_run` are enabled at startup.
  - warn on risky combinations (for example `allow_fallback=true` in production profile).
- Failure semantics:
  - metadata initialization errors are fatal.
  - vector initialization errors are fatal unless explicit fallback policy allows continuation.
  - dimension mismatch and schema incompatibility are always hard errors.
- Auditability:
  - emit one structured event for fallback activation and one for dry-run skipped writes.

## 6) Implementation Plan

### Phase 1: Contract Extensions

Status: `IMPLEMENTED`

- [x] Add `ProviderCapabilities` type and `capabilities()` to both contracts.
- [x] Add metadata schema version surface and vector expected dimensionality surface.
- [x] Add deterministic storage error types/messages for unsupported operations, schema incompatibility, and dimension mismatches.

### Phase 2: Adapter Updates (SQLite + LanceDB)

Status: `IMPLEMENTED`

- [x] Update SQLite metadata adapter:
  - [x] implement `capabilities()`
  - [x] expose fixed `schema_version = 1`
  - [x] include version in `health()`
- [x] Update LanceDB vector adapter:
  - [x] implement `capabilities()`
  - [x] expose `expected_dimensionality`
  - [x] validate dimensions in `insert()` and `search()`

### Phase 3: Factory Wiring

Status: `IMPLEMENTED`

- [x] Add startup schema compatibility check for metadata store.
- [x] Add startup dimension compatibility check (embedding provider vs vector store).
- [x] Add policy-gated vector init fallback to in-memory store with warning log and preserved dimensionality.
- [x] Keep metadata init fail-fast behavior.
- [x] Surface fallback/degraded state via health.

### Phase 4: Dry-Run Wrappers

Status: `IMPLEMENTED`

- [x] Add storage-level wrappers for metadata/vector writes.
- [x] Preserve read/search behavior.
- [x] Emit `DRY-RUN: write skipped` logs consistently.
- [x] Enforce dry-run return shape parity.

### Phase 5: Tests

Unit tests for all five feature groups:

- [x] capabilities defaults and per-adapter overrides
- [x] unsupported operation deterministic errors
- [x] schema version exposure and incompatible-version fail-fast
- [x] dimension validation in vector `insert()` and `search()`
- [x] dry-run write no-op with validation + unchanged read/search semantics and return-shape parity

Integration tests (required):

Status: `IMPLEMENTED`

- [x] vector provider init failure with `allow_fallback=false` fails startup
- [x] vector provider init failure with `allow_fallback=true` activates in-memory fallback and degraded health
- [x] metadata init failure always fails startup
- [x] Postgres `schema_version` table invariants and incompatibility behavior
- [x] startup and runtime dimensionality mismatch behavior
- [x] log redaction checks for DSN/credential leakage

## Deliverables

- Updated storage contracts (`MetadataStore`, `VectorStore`) with capabilities/version/dimensionality surfaces.
- Updated SQLite and LanceDB adapters.
- Factory fallback + startup validation wiring.
- Dry-run wrappers for write paths.
- Unit + integration tests covering all required adjustments and safety guarantees.

## Acceptance Criteria

- Domain layer remains provider-agnostic and does not depend on optional capabilities.
- API/MCP interfaces and response shapes remain unchanged.
- Metadata store fails fast on schema incompatibility.
- Vector dimension mismatches fail deterministically with clear errors.
- Vector provider init failures fallback only when explicitly allowed and are clearly logged.
- Fallback/degraded mode is machine-visible in health output.
- Dry-run mode preserves pipeline flow while skipping persistence writes with shape-parity responses.
- Logs and errors are secret-safe by default.
