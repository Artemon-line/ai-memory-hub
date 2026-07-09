# Release CI Gap Analysis

This document tracks release checks that are automated, newly automated, or still
manual because they depend on external systems outside the repository.

## Newly Automated

- `Release Readiness` workflow:
  - Ruff over `memory`, `tests`, and `tools`.
  - Pyright.
  - Bruno collection file validation.
  - Strict MkDocs build.
  - Release tag policy validation against the current `pyproject.toml` version.
- `CodeQL Analysis` workflow:
  - Python static security analysis on pull requests, pushes to `main`, weekly
    schedule, and manual dispatch.
- Docker publish workflow:
  - Manual `workflow_dispatch` publishes explicitly fetch and check out the
    requested release tag before version validation and image build.
  - Builds a local candidate image and blocks release publishing on
    high/critical Trivy findings.
  - Pulls/runs the just-pushed image by digest and checks `/ready` before the
    publish summary is treated as successful.
- Root `Containerfile` tests:
  - Ensure the default image remains the SQLite/LanceDB quickstart image and
    does not silently regain optional provider extras.
  - Ensure every repository-owned container image pins the `uv` installer
    version used during image builds.

## Already Covered

- Unit and integration tests.
- E2E tests with Ollama-backed models.
- Storage config variation checks.
- Postgres/PGVector live integration checks.
- Container build, `/ready`, MCP initialize, non-root runtime, and writable path
  smoke checks.
- Bruno API/MCP integration, including OAuth protected-resource metadata and
  authenticated project flows.
- Provider contract and live provider workflows for supported storage backends.
- Trivy image scan in warning mode for pull requests and scheduled reporting.
  The release publish path blocks on high/critical image findings before push.
- CycloneDX SBOM generation.
- Dependency review in warning mode.
- GitHub Pages strict docs build and deployment.
- Weekly/manual real-client MCP smoke harness with artifact upload.

## Still Manual Or Repository-Setting Driven

- Enabling branch protection/rulesets for `main`.
- Setting required checks in GitHub.
- Creating the Docker Hub repository.
- Adding `DOCKERHUB_USERNAME`, `DOCKERHUB_TOKEN`, and optional
  `DOCKERHUB_NAMESPACE` secrets.
- Enabling GitHub Pages with source `GitHub Actions`.
- Enabling repository security features such as Dependabot alerts, secret
  scanning, push protection, and private vulnerability reporting where available.
- Verifying real Codex/opencode/Claude/Copilot/Gemini client behavior on a
  developer machine until stable client binaries/configs are available in CI.
- Posting release announcements and pinning launch material.

## Recommended Next Automations

- Add Dependabot version updates for GitHub Actions and Python dependencies once
  dependency churn is acceptable.
- Promote dependency review from warning mode to blocking after the first
  release establishes a vulnerability triage rhythm.
- Configure real-client smoke command templates in CI and promote the clients
  that are stable for three consecutive scheduled runs.
- Add a release-note check that verifies the Docker digest from the publish
  workflow is copied into the GitHub release notes before stable promotion.
- Pin third-party GitHub Actions to commit SHAs after the workflow set stops
  changing daily.
- Pin Docker base images and provider service images by digest once the release
  image cadence is settled.
