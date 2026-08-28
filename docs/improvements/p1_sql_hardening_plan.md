# P1 SQL Hardening Plan

Source date: `27-08-2026`

Status: planned

Priority: P1 follow-up after `v0.1.0-beta`

## Goal

Make SQL construction rules explicit, testable, and hard to regress.

The hub can keep using raw SQL where it is clearer than an ORM, but SQL text
must never be shaped by untrusted memory payloads, search text, project ids,
owner ids, auth tokens, fact values, or audit filters. Runtime values must use
database-driver parameters. Dynamic identifiers must be validated or chosen from
owned allowlists.

This plan does not add user-facing destructive memory update or delete behavior.
Memory history remains append-only for beta. Any SQL `UPDATE` or `DELETE` used
inside storage providers must stay limited to internal lifecycle work such as
auth/session state, schema migration, fact supersession, vector reindex cleanup,
or future archive visibility state.

## Current Assessment

- SQLite metadata storage uses `?` placeholders for runtime values.
- Postgres metadata storage uses `%s` placeholders for runtime values.
- Dynamic `WHERE` clauses are assembled from hardcoded fragments while values
  remain bound separately.
- `IN (...)` queries generate placeholder counts, not interpolated memory ids.
- PGVector table-name interpolation is guarded by a simple SQL identifier check
  in `memory/backend/vector_store.py`.
- SQLite migration helper SQL interpolates table and column identifiers in
  `memory/backend/metadata_store.py`; current callers pass internal constants,
  but an explicit allowlist would make that assumption enforceable.

## Approved SQL Patterns

- [ ] Runtime values are passed through DB-driver parameter binding.
- [ ] Optional filters are built from hardcoded clause fragments only.
- [ ] Variable-length `IN` clauses interpolate placeholder tokens only.
- [ ] Dynamic table or column names are rejected unless they pass an explicit
      validator or are selected from an owned allowlist.
- [ ] Raw SQL f-strings are allowed only for reviewed identifier or clause
      assembly helpers.
- [ ] Storage maintenance `UPDATE`/`DELETE` paths are documented separately
      from public memory lifecycle semantics.

## Phase 1: Inventory And Classification

- [ ] Inventory every SQL construction site in `memory/backend`.
- [ ] Classify each site as static SQL, parameterized value query, hardcoded
      clause assembly, generated placeholder list, dynamic identifier, or
      storage lifecycle mutation.
- [ ] Record which call sites are reachable from HTTP, MCP, importers, CLI
      tools, startup migrations, and background maintenance.
- [ ] Flag any SQL string where user-controlled input can change SQL structure.
- [ ] Add a short SQL safety section to `docs/architecture.md` or
      `docs/security.md` once the inventory is complete.

## Phase 2: Identifier Guardrails

- [ ] Add a shared helper for SQLite identifiers used by migration helpers.
- [ ] Replace direct SQLite `PRAGMA table_info({table})` and `ALTER TABLE`
      identifier interpolation with validated or allowlisted identifiers.
- [ ] Keep PGVector `table_name` validation and add regression tests for
      rejected names such as `memory_vectors;DROP TABLE conversations`.
- [ ] Document why identifiers cannot use ordinary value placeholders.
- [ ] Ensure future config-driven identifiers are validated before storage
      providers are initialized.

## Phase 3: Injection Regression Tests

- [ ] Add SQLite tests showing malicious memory ids remain data, not SQL.
- [ ] Add project and owner boundary tests with SQL-shaped ids where validation
      allows the value shape.
- [ ] Add search tests using payloads such as `' OR 1=1 --` and verify results
      do not broaden.
- [ ] Add fact search and fact supersession tests with SQL-shaped
      subject/predicate/object values.
- [ ] Add audit filter tests proving SQL-shaped actor, project, memory, and
      fact ids do not broaden audit history reads.
- [ ] Add vector-store delete/reindex tests for providers with SQL-like filter
      strings where the repository can exercise them locally.

## Phase 4: Append-Only Contract Tests

- [ ] Add tests proving same memory `id` with changed content is rejected.
- [ ] Add tests proving duplicate same-hash inserts are idempotent.
- [ ] Add tests proving same upstream thread appends are allowed only through
      the trusted append path when that setting is enabled.
- [ ] Add tests proving no HTTP route exposes memory delete, memory update,
      archive, or restore in `v0.1.0-beta`.
- [ ] Add tests proving no MCP tool exposes memory delete, memory update,
      archive, or restore in `v0.1.0-beta`.
- [ ] Keep vector `delete(ids)` documented and tested as internal index
      maintenance, not a public memory-history deletion feature.

## Phase 5: Static Guardrail

- [ ] Add a small static test that scans backend SQL call sites.
- [ ] Allowlist reviewed f-string SQL patterns for validated identifiers,
      generated placeholder lists, and hardcoded clause joins.
- [ ] Fail CI when a new SQL f-string or string-format query appears outside
      the allowlist.
- [ ] Require allowlist entries to include a short reason and owner path.
- [ ] Keep the static test narrow enough that ordinary parameterized SQL remains
      pleasant to write.

## Phase 6: Provider Parity

- [ ] Run the same safety expectations against SQLite and Postgres metadata
      providers.
- [ ] Add Postgres-focused tests for array parameters such as
      `WHERE id = ANY(%s)`.
- [ ] Add pgvector tests for validated table names and bound vector/search
      parameters.
- [ ] Review Mongo and non-SQL providers for equivalent filter-construction
      safety, even though they do not use SQL strings.
- [ ] Record any provider-specific exceptions in the SQL safety docs.

## Phase 7: Documentation And Review Checklist

- [ ] Add reviewer guidance to `CONTRIBUTING.md` for raw SQL changes.
- [ ] Document the difference between SQL storage mutations and public memory
      lifecycle semantics.
- [ ] Document that memory corrections use new memory entries, fact
      supersession, review decisions, and audit events rather than destructive
      history updates.
- [ ] Add release-note wording when the static guardrail lands.
- [ ] Link this plan from the improvement-plan index.

## Acceptance Criteria

- [ ] No user-controlled value is interpolated directly into SQL text.
- [ ] Every dynamic identifier is validated or selected from an allowlist.
- [ ] Injection-shaped inputs are covered for memory, project, auth, search,
      facts, audit filters, and vector maintenance paths.
- [ ] CI fails on new unreviewed SQL string interpolation.
- [ ] Docs explain the approved raw SQL patterns and the beta append-only memory
      contract.
- [ ] The shipped HTTP and MCP surfaces still do not expose destructive memory
      update/delete/archive/restore behavior for `v0.1.0-beta`.
