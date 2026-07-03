# Contributing

Thanks for helping improve ai-memory-hub. This project is a local-first memory
service for AI agents, so contributions should keep user control, privacy, and
repeatable local operation at the center.

## Local Setup

```bash
uv sync --all-extras
uv run aim health --json
```

Run the API and MCP server locally:

```bash
uv run aim serve --host 127.0.0.1 --port 8000
curl -fsS http://127.0.0.1:8000/ready
```

## Tests And Checks

For Python changes, run the focused tests first, then broaden as needed:

```bash
uv run python -m ruff check memory tests tools
uv run pytest tests/unit tests/integration -q
uv run python -m pyright
```

For documentation changes:

```bash
uv run python tools/prepare_mkdocs.py
uv run python -m mkdocs build --strict
uv run pytest tests/unit/test_docs_build.py -q
```

For Bruno API/MCP collection changes:

```bash
uv run python tests/bruno/validate_files.py
```

For container changes, run the container definition tests and, when Docker is
available, a local smoke build:

```bash
uv run pytest tests/unit/test_container_definition.py -q
docker build -t ai-memory-hub:local -f Containerfile .
docker run --rm -p 8000:8000 ai-memory-hub:local
```

## Pull Request Policy

- Use one focused change per pull request.
- Include tests or documentation for behavior changes.
- Do not push directly to `main` once branch protection is enabled.
- Keep release changes in a release-readiness or release PR.
- Note security, privacy, storage, and compatibility impacts in the PR template.
- Avoid logging secrets, API keys, private conversation text, embeddings, or full
  user queries.

## Branch And Commit Policy

Use short-lived branches for normal work. The default branch should stay green,
and release tags should be created only after the release checklist is complete.

Signed commits are not required for the first release unless repository settings
are updated to enforce them.

## Release PR Policy

Release PRs should update the project version, release notes source, release
checklist status, and documentation links together. Release tags must match the
version in `pyproject.toml`; for example, `v0.1.0` must match
`version = "0.1.0"`.
