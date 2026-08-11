# Agent Guidance

This repository is often maintained through coding agents. Keep agent-created
changes focused, reviewable, and consistent with the repository's human-facing
templates.

## Project Snapshot

ai-memory-hub is a local-first memory service for AI agents. It stores
conversation payloads once, then exposes validation, insert, search, retrieve,
and ask-over-memory workflows through HTTP and MCP. The hub owns schema
validation, hashing, deduplication, embedding, storage, retrieval, auth,
permissions, and memory quality; clients should not reimplement those rules.

## Repository Map

- `memory/api/`: FastAPI app, HTTP routes, OAuth, bearer/API-key auth, Connect UI
  support, and runtime dependency wiring.
- `memory/interfaces/`: MCP server tools, resources, prompts, and MCP argument
  normalization.
- `memory/ingestion/`: schema validation, normalization, deterministic
  ingestion, hashing, deduplication, fact extraction, and ingestion agents.
- `memory/backend/`: metadata and vector storage providers, dry-run wrappers,
  provider capability checks, and storage safety behavior.
- `memory/importers/`: import helpers for external or pasted conversation
  formats before they enter ingestion.
- `memory/tools/`: operational tools and real-client smoke harnesses.
- `tests/unit/`, `tests/integration/`, `tests/e2e/`: regression coverage by
  blast radius.
- `docs/`: architecture, plans, setup guides, and user-facing documentation.
- `examples/`: runnable provider and deployment examples.

Important docs:

- `docs/architecture.md`: implemented system shape and provider model.
- `docs/agents.md`: MCP/HTTP agent contract and expected client behavior.
- `CONTRIBUTING.md`: local setup, validation commands, and PR policy.

## Development Workflow

- Prefer small, focused pull requests with tests or documentation for behavior
  changes.
- Do not log secrets, API keys, private conversation text, embeddings, or full
  user queries.
- Use bash-style commands in documentation examples.
- Preserve existing code style and local abstractions before adding new ones.
- Keep exception handlers simple; delegate complex recovery or conflict handling
  to named helpers.

## Validation

- For Python changes, run focused tests first, then broaden when the change
  touches shared behavior.
- Run the configured linter and type checker for code changes:

```bash
uv run python -m ruff check memory tests tools
uv run python -m pyright
```

- Run focused tests for the files or behavior changed. Broaden to the relevant
  suite when shared behavior, storage contracts, auth, MCP, or ingestion changes:

```bash
uv run pytest tests/unit tests/integration -q
```

- For documentation changes, run the docs preparation/build checks described in
  `CONTRIBUTING.md`:

```bash
uv run python tools/prepare_mkdocs.py
uv run python -m mkdocs build --strict
```

- For Bruno API/MCP collection changes:

```bash
uv run python tests/bruno/validate_files.py
```

## GitHub Issues

Agents should not create free-form GitHub issues directly from ad hoc text.
GitHub issue templates can be bypassed by the CLI and API, so preserve the
template structure manually:

1. Draft the issue body as a local Markdown file first.
2. Use sections that match the appropriate issue template or repo convention.
3. Let the user review the title/body when the issue is security-sensitive,
   broad, or likely to create follow-up work.
4. Create the issue with `gh issue create --body-file <path>` instead of passing
   a long inline `--body` string.

For findings from static analysis, dependency review, code review, or security
tools, batch related items into a PR-sized tracking issue instead of opening one
issue per finding. Use a structure like:

```markdown
## Summary

## Findings

## Impact

## Proposed Direction

## Acceptance Criteria

## Notes
```

When a later PR fixes the issue, reference it with `Fixes #<issue>` or
`Refs #<issue>` as appropriate.
