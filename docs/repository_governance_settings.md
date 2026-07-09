# Repository Governance Settings

This file records the GitHub repository settings to apply before the first
public release. These settings require repository admin access; checking this
file in does not apply them automatically.

## Repository Description And Topics

Description:

```text
Local-first memory service for AI agents, with HTTP and MCP APIs.
```

Topics:

```text
mcp
ai-agents
memory
rag
fastapi
pgvector
local-first
openai-compatible
```

Apply with GitHub CLI:

```bash
gh repo edit Artemon-line/ai-memory-hub \
  --description "Local-first memory service for AI agents, with HTTP and MCP APIs." \
  --add-topic mcp \
  --add-topic ai-agents \
  --add-topic memory \
  --add-topic rag \
  --add-topic fastapi \
  --add-topic pgvector \
  --add-topic local-first \
  --add-topic openai-compatible
```

## Branch Protection

Protect `main` before publishing `v0.1.0`.

Required policy:

- Require pull requests before merging.
- Block direct pushes to `main`.
- Require conversation resolution before merging.
- Require branches to be up to date before merging once check noise is stable.
- Do not require signed commits for `v0.1.0` unless maintainer signing is already
  configured locally.
- Require the real-client MCP smoke workflow now that it runs on pull requests
  and skips unavailable clients with explicit diagnostics.

Required status checks for `main`:

```text
Unit and Integration Tests
E2E Scenario (Ollama)
Storage Config Variations
Storage Postgres Integration
Containerfile Lint
Container Build and Smoke
Release Readiness
Build Documentation
Bruno API/MCP Integration
Dependency Review
Image Scan and SBOM
CodeQL Analysis
Real-Client MCP Smoke
```

The Bruno check can be configured as required once GitHub path-based rulesets are
available for the repository. Until then, it is acceptable to require it for all
PRs if runtime is acceptable, or keep it as a visible non-required check and
manually enforce it for API/MCP changes.

Keep provider live-matrix jobs visible but non-required until their external
service availability is stable enough for every PR.

## Docker Hub Secrets

Create the Docker Hub repository before publishing the first release:

```text
docker.io/<namespace>/ai-memory-hub
```

Add these GitHub Actions secrets:

```text
DOCKERHUB_USERNAME
DOCKERHUB_TOKEN
DOCKERHUB_NAMESPACE
```

`DOCKERHUB_NAMESPACE` is optional when it matches `DOCKERHUB_USERNAME`. Prefer a
Docker Hub access token over an account password.

## GitHub Pages

Required settings:

- Pages source: GitHub Actions.
- Environment: `github-pages`.

The checked-in `pages` workflow already has `contents: read`, `pages: write`,
and `id-token: write`.

## Release Candidate Checks

Before publishing the stable release:

```bash
uv run python tools/validate_release_version.py v0.1.0-rc.1
uv run python tests/bruno/validate_files.py
uv run python tools/prepare_mkdocs.py
uv run python -m mkdocs build --strict
```

After the prerelease is published, confirm Docker publishing created prerelease
tags and did not update `latest`.
