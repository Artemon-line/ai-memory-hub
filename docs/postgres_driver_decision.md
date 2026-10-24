# PostgreSQL Driver Decision

Status: accepted on 2026-10-24.

## Decision

Use `pg8000` for the synchronous PostgreSQL metadata and PGVector providers.
Keep DSN parsing and driver-specific transaction behavior in
`memory.backend.postgres_driver` so storage providers depend only on the
project's small internal connection adapter.

`pg8000` is a production/stable, synchronous DB-API 2.0 driver released under
BSD-3-Clause. It supports Python 3.9 and newer, ships a platform-independent
pure-Python wheel, uses `%s` parameter binding, handles PostgreSQL arrays, and
does not require `libpq` or a compiler. Version 1.31.5 was released in
September 2025. See the [pg8000 package record](https://pypi.org/project/pg8000/).

The adapter preserves `postgres://` and `postgresql://` URL configuration,
percent-encoded credentials and database names, host and port selection,
connection timeout, application name, startup parameters, and the common
`sslmode` values. It also commits a successful connection context and rolls
back a failed one, preserving the transaction contract previously supplied by
psycopg.

OpenTelemetry uses the generic DB-API integration because no maintained
pg8000-specific instrumentor is available. The generic integration traces
DB-API connect and cursor operations without capturing bound parameters. See
the [OpenTelemetry DB-API instrumentation](https://opentelemetry-python-contrib.readthedocs.io/en/latest/instrumentation/dbapi/dbapi.html).

## Candidates

| Driver | License | Maintenance and Python support | Binary/native requirements | Compatibility result |
| --- | --- | --- | --- | --- |
| `pg8000` | BSD-3-Clause | Production/stable; latest evaluated release 1.31.5; Python 3.9+ | Pure Python; no `libpq` | Selected. Synchronous DB-API and `%s` binding minimize provider changes. A small adapter is required for URL parsing and connection-context transactions. |
| `asyncpg` | Apache-2.0 | Production/stable; actively released; Python 3.9+ | Native-extension wheels or a source build | Rejected for this migration. It is asyncio-native, is not DB-API, and uses `$1` binding, requiring a backend-wide async contract and SQL rewrite. See the [asyncpg package record](https://pypi.org/project/asyncpg/). |
| `PyGreSQL` | PostgreSQL License | Mature; actively released; Python 3.8-3.14 at evaluation | C extension and system `libpq` | Rejected. It is permissive and synchronous, but adds native runtime/build requirements that the existing binary psycopg extra avoided. See the [PyGreSQL package record](https://pypi.org/project/PyGreSQL/). |
| `psycopg` 3 | LGPL-3.0-only | Production/stable; Python 3.10+ | Pure Python with `libpq`, or bundled binary extra | Rejected because the runtime license is the reason for this migration. See the [psycopg package record](https://pypi.org/project/psycopg/). |

## Compatibility Notes

- Provider names, YAML fields, DSN fields, schemas, and stored data do not
  change.
- `sslmode=prefer` and `sslmode=allow` use pg8000's default opportunistic TLS.
  `require`, `verify-ca`, and `verify-full` create explicit TLS contexts.
- `sslrootcert`, `sslcert`, and `sslkey` URL parameters are supported. Other
  unrecognized URL query parameters are sent as PostgreSQL startup parameters.
- pg8000 reports server errors as a diagnostic dictionary in the first
  exception argument. Conflict handling reads SQLSTATE `23505` from that shape
  in addition to the previous `sqlstate` and `pgcode` attributes.
- The pure-Python protocol implementation can have different throughput than a
  C-backed libpq driver. The service currently opens short-lived synchronous
  connections, so deployment-specific load testing remains appropriate.

## Validation

Unit coverage verifies DSN translation, TLS mode handling, commit/rollback,
pg8000's unique-violation shape, the optional dependency set, and generic
OpenTelemetry wiring. The existing live PostgreSQL and PGVector contract tests
remain the compatibility gate and run in CI with `uv sync --extra postgres`.
A fresh FOSSA scan should confirm that neither `psycopg` nor
`psycopg-binary` remains in the resolved dependency graph.
