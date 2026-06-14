# Recurring Codebase Cleanup Plan

This plan defines a periodic maintenance loop for removing dead code, stale
documentation, over-complex control flow, accidental hardcoding, weak tests, and
other quality drift. The goal is not cosmetic churn. Each pass should reduce
real maintenance risk while preserving existing behavior.

## Cadence

Run this cleanup loop on a predictable schedule:

- Weekly: automated detection checks and issue/PR triage.
- Monthly: one focused cleanup PR for the highest-impact findings.
- Before releases: documentation freshness, container/runtime assumptions, and
  public command examples.
- After major feature work: targeted cleanup around the changed subsystem before
  moving to the next feature.

Do not batch unrelated risky rewrites into one PR. Prefer small PRs with clear
behavior-preserving intent and focused verification.

## Scope

Each cleanup pass should look for:

- Dead code: unused functions, unreachable branches, stale compatibility paths,
  unused constants, obsolete config fields, and abandoned tests.
- Outdated docs: commands that no longer work, old runtime assumptions,
  stale roadmap status, missing endpoint/tool changes, and stale examples.
- Control-flow complexity: deep nesting, long functions, broad conditionals,
  duplicated branches, and exception handlers that contain policy logic.
- Hardcoded values: repeated ports, paths, model names, provider names,
  timeouts, magic numbers, and label/version metadata that should come from
  config, constants, build args, or a single source of truth.
- Weak boundaries: modules reaching around interfaces, global state,
  duplicated validation, and storage/provider logic mixed into API handlers.
- Test gaps: missing regression tests for bug fixes, error paths, config
  variants, and public API/MCP/CLI contracts.
- Observability gaps: silent failures, unclear errors, unsafe logs, and missing
  context for operational failures.

## Non-Goals

Avoid these unless there is a concrete quality or reliability reason:

- Formatting-only churn across unrelated files.
- Renaming public APIs without compatibility planning.
- Replacing stable local patterns with new abstractions because they are newer.
- Generating checked-in source files to avoid maintaining simple static files.
- Large rewrites that cannot be verified with focused tests.

## Detection Checklist

Run lightweight checks first:

```bash
rg -n "TODO|FIXME|HACK|deprecated|legacy|temporary|workaround" memory tests docs README.md
rg -n "/app/\\.venv/bin/aim|localhost|127\\.0\\.0\\.1|0\\.0\\.0\\.0|8000" README.md docs examples Containerfile
rg -n "except Exception|if .* else|elif|pass$|# noqa|type: ignore" memory tests
rg -n "api_key|password|secret|token|dsn|Authorization|X-API-Key" memory docs README.md examples
```

Then run structure and verification checks appropriate to the touched area:

```bash
uv run pytest
uv run python tools/prepare_mkdocs.py
uv run python -m mkdocs build --strict
```

For container changes, also run Hadolint when available and rely on CI container
smoke for actual image build verification if Docker/Podman is not installed
locally.

## Review Heuristics

Use these rules when deciding whether to change something:

- If a value is repeated in multiple runtime paths, move it to config or a
  named constant.
- If a value is a stable container contract, such as internal port `8000`, keep
  it explicit and document how users override it externally.
- If documentation describes a command, verify that command still maps to the
  current runtime path.
- If a branch encodes business policy, move the policy to a named helper or
  service boundary.
- If an exception handler decides retry/conflict policy, delegate that decision
  to a named helper and keep the handler small.
- If a function needs comments to explain every block, split it by behavior.
- If an abstraction has only one caller and adds no clarity, remove it.
- If behavior changes, add or update a regression test in the same PR.

## Cleanup PR Template

Each cleanup PR should include:

- Problem: the concrete drift or risk being removed.
- Scope: files/subsystems touched.
- Behavior: whether runtime behavior changed.
- Verification: focused tests, full tests when warranted, docs build if docs
  changed, and CI checks that must pass.
- Compatibility: public command/API/config impact.

## Priority Order

1. Broken docs or examples that cause users to run the wrong command.
2. Dead code that hides real behavior or confuses future changes.
3. Hardcoded values repeated across implementation, docs, and CI.
4. Complex conditional/error-handling code in API, MCP, storage, ingestion, and
   CLI paths.
5. Missing regression tests for previously fixed bugs.
6. Internal naming or layout cleanup that improves readability without changing
   behavior.

## Acceptance Criteria

A cleanup pass is complete when:

- The pass has a narrow theme and a clear diff.
- Removed code is either unused or replaced by a tested path.
- Docs examples reflect the current CLI, container, API, and MCP contracts.
- Hardcoded values are either justified as stable contracts or centralized.
- Any behavior change has focused regression coverage.
- Relevant tests and docs checks have passed or the remaining verification gap
  is documented.
