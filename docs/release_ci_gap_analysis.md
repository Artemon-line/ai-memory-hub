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
  - Updates existing GitHub release notes with the published image tags and
    digest after the published-image smoke test passes.
- Real-client MCP smoke workflow:
  - Runs on pull requests, pushes to `main`, weekly schedule, and manual
    dispatch.
  - Uploads real-client smoke artifacts for every run.
- Root `Containerfile` tests:
  - Ensure the default image remains the SQLite/LanceDB quickstart image and
    does not silently regain optional provider extras.
  - Ensure every repository-owned container image pins the `uv` installer
    version used during image builds.
- GitHub Actions workflow refs:
  - Third-party actions are pinned to immutable commit SHAs with version
    comments for update readability.
- E2E Ollama workflow:
  - Uses a pinned `ollama/ollama:0.22.1` image digest instead of the upstream
    installer script.
- Conversation schema validation:
  - Enforces declared JSON Schema formats such as `uuid` and `date-time`.

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
- PR/push/weekly/manual real-client MCP smoke harness with artifact upload.

## Still Manual Or Repository-Setting Driven

- Enabling branch protection/rulesets for `main`.
- Setting required checks in GitHub.
- Creating the Docker Hub repository.
- Adding `DOCKERHUB_USERNAME`, `DOCKERHUB_TOKEN`, and optional
  `DOCKERHUB_NAMESPACE` secrets.
- Enabling GitHub Pages with source `GitHub Actions`.
- Enabling repository security features such as Dependabot alerts, secret
  scanning, push protection, and private vulnerability reporting where available.
- Posting release announcements and pinning launch material.

## Recommended Next Automations

- Promote dependency review from warning mode to blocking after the first
  release establishes a vulnerability triage rhythm.
- Pin Docker base images and provider service images by digest once the release
  image cadence is settled.
