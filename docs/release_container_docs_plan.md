# Release, Container, and Docs Publishing Plan

Plan for keeping the project container image aligned with the codebase, publishing Docker Hub
images for every GitHub release, and generating GitHub Pages from README/docs Markdown.

## Goals

- Keep `Containerfile` buildable and tested as part of CI.
- Publish a versioned Docker image for every GitHub release.
- Publish a moving `latest` image only from stable releases.
- Generate a GitHub Pages documentation site from `README.md` and files under `docs/`.
- Keep release automation reproducible, secret-safe, and easy to audit.

## Non-Goals

- Replacing the Python package/release process.
- Publishing images from every pull request.
- Adding a full custom documentation application.
- Changing runtime API/MCP behavior.

## Current Repo State

- [x] `Containerfile` exists at repo root.
- [x] CI exists in `.github/workflows/pipeline.yml`.
- [x] README and Markdown docs exist.
- [x] Project version is declared in `pyproject.toml`.
- [x] Container build is verified in CI.
- [x] Container smoke test is verified in CI.
- [ ] Docker Hub publishing workflow exists.
- [ ] GitHub release workflow exists.
- [x] GitHub Pages docs workflow exists.

## 1) Containerfile Maintenance

Status: `IMPLEMENTED`

Container contract:

- [x] Build from the checked-in `Containerfile`.
- [x] Use Python version compatible with `pyproject.toml`.
- [x] Install dependencies from `uv.lock` with frozen resolution.
- [x] Include `memory/`, `README.md`, `pyproject.toml`, `uv.lock`, and default config.
- [x] Expose API/MCP service on port `8000`.
- [x] Start through the project CLI once the serve command exists.
- [x] Remove the direct `uvicorn memory.api.server:app` transitional fallback.
- [x] Run as non-root by default.
- [x] Support OpenShift arbitrary runtime UIDs in root group by making runtime
      paths group-writable.
- [x] Execute the installed `aim` console script directly instead of invoking
      `uv` at runtime.
- [x] Keep runtime defaults local-safe.

CLI dependency:

- [x] Implement the serve command tracked in `docs/cli_implementation_plan.md`.
- [x] Preferred final container command:

```bash
aim serve --host 0.0.0.0 --port 8000
```

- [x] Development fallback until the console script exists:

```bash
python -m memory.cli serve --host 0.0.0.0 --port 8000
```

- [x] Add packaging metadata for the selected console script before using `aim` in `Containerfile`.
- [x] Keep `Containerfile` and CLI docs aligned whenever the serve command changes.

Required improvements:

- [x] Add `serve` to the CLI implementation plan:
  - [x] starts the same FastAPI/MCP app as the current `uvicorn` command
  - [x] supports `--host`
  - [x] supports `--port`
  - [x] supports `--config`
  - [x] returns stable exit code `3` on runtime initialization failure
  - [x] logs the active config path and redacted provider summary
- [x] Add OCI labels:
  - [x] `org.opencontainers.image.title`
  - [x] `org.opencontainers.image.description`
  - [x] `org.opencontainers.image.source`
  - [x] `org.opencontainers.image.revision`
  - [x] `org.opencontainers.image.version`
  - [x] `org.opencontainers.image.licenses`
- [x] Add `.dockerignore` to keep builds small:
  - [x] `.git`
  - [x] `.venv`
  - [x] `.pytest_cache`
  - [x] `.ruff_cache`
  - [x] `data`
  - [x] local logs/artifacts
- [x] Decide whether the image should install optional extras:
  - [x] default image is the SQLite/LanceDB quickstart image with no optional provider extras
  - [x] Postgres/PGVector and tokenizer extras live in the provider-local example image
- [x] Add a healthcheck command or document health endpoint once the API exposes one.
- [x] Add Containerfile linting with Hadolint.
- [x] Simulate OpenShift arbitrary UID startup in CI with `--user 12345:0`.
- [x] Document runtime volume mounts:
  - [x] `/app/data`
  - [x] `/app/logs`
  - [x] optional config override path
- [x] Document runtime environment variables for config/secrets.

Container acceptance criteria:

- [x] `docker build -f Containerfile .` succeeds in CI.
- [x] Container starts successfully in CI.
- [x] `GET /ready` smoke check succeeds.
- [x] MCP endpoint remains reachable at `/mcp/`.
- [x] Container smoke verifies non-root writable runtime paths.
- [x] Image does not include repo-local `data/`, `.git/`, or virtualenv files.

## 2) CI Container Verification

Status: `IMPLEMENTED`

Add CI checks before publishing is enabled:

- [x] Add a `container` job to `.github/workflows/pipeline.yml`.
- [x] Build the image on pull requests and pushes to `main`.
- [x] Tag CI image locally as `ai-memory-hub:ci`.
- [x] Run a container smoke test:
  - [x] start container on port `8000`
  - [x] run with an arbitrary non-root UID in root group
  - [x] wait for service readiness
  - [x] call `/ready`
  - [x] call MCP initialize smoke
  - [x] verify `/app/data`, `/app/logs`, and tokenizer cache are writable
- [x] Fail CI if container exits early.
- [x] Upload container logs as an artifact on failure.
- [x] Keep publishing disabled in PR builds.

Optional hardening:

- [x] Add Hadolint or similar Dockerfile linting.
- [x] Add Trivy image scan in warning mode first.
- [x] Block Docker release publishing on high/critical Trivy image findings
      before the image is pushed.
- [ ] Promote image scanning to required once false positives are triaged.

## 3) Release Version Policy

Status: `PARTIAL`

Version source:

- [ ] Use `pyproject.toml` as the source package version.
- [x] Use GitHub release tag as the released version.
- [x] Require release tag to match project version:
  - [x] `v0.1.0` tag must match `version = "0.1.0"`
  - [x] fail release workflow if they differ

Tag policy:

- [x] Release tags use `vMAJOR.MINOR.PATCH`.
- [x] Docker image tags:
  - [x] `docker.io/<dockerhub-namespace>/ai-memory-hub:vMAJOR.MINOR.PATCH`
  - [x] `docker.io/<dockerhub-namespace>/ai-memory-hub:MAJOR.MINOR.PATCH`
  - [x] `docker.io/<dockerhub-namespace>/ai-memory-hub:latest` for stable releases only
- [x] Pre-release tags use suffixes such as `v0.2.0-rc.1`.
- [x] Pre-releases publish version tags but do not update `latest`.

Release checklist:

- [ ] Update `pyproject.toml` version.
- [ ] Update changelog or release notes source.
- [ ] Ensure CI is green on `main`.
- [ ] Create GitHub release.
- [ ] Publish Docker Hub image from release workflow.
- [ ] Publish GitHub Pages docs from release or `main`.

## 4) Docker Hub Publishing Workflow

Status: `IMPLEMENTED`

Workflow trigger:

- [x] Run on GitHub `release.published`.
- [x] Allow manual `workflow_dispatch` for recovery/retry.
- [x] Do not publish from pull requests.

Required GitHub secrets:

- [ ] `DOCKERHUB_USERNAME`
- [ ] `DOCKERHUB_TOKEN`
- [ ] optional `DOCKERHUB_NAMESPACE` if different from username

Workflow permissions:

- [ ] `contents: read`
- [ ] no broad write permissions unless attaching release artifacts

Workflow steps:

- [ ] Checkout repository.
- [ ] Set up Docker Buildx.
- [ ] Log in to Docker Hub using secrets.
- [ ] Extract release version from `github.event.release.tag_name`.
- [ ] Validate tag format.
- [ ] Validate tag matches `pyproject.toml` version.
- [ ] Generate Docker metadata/tags.
- [ ] Build image from `Containerfile`.
- [ ] Run a post-build smoke test before push where practical.
- [ ] Push image to Docker Hub.
- [ ] Add image digest to workflow summary.
- [x] Optionally attach SBOM/provenance artifact.

Recommended GitHub Actions:

- [ ] `docker/setup-buildx-action`
- [ ] `docker/login-action`
- [ ] `docker/metadata-action`
- [ ] `docker/build-push-action`

Publishing acceptance criteria:

- [ ] Every GitHub release publishes an immutable versioned image.
- [ ] Stable releases update `latest`.
- [ ] Pre-releases do not update `latest`.
- [ ] Failed image publish fails the release workflow visibly.
- [ ] Logs do not expose Docker Hub tokens or runtime secrets.
- [x] Published image is smoke-tested by digest before the workflow summary is
      written.
- [x] Manual Docker publish retries check out the requested release tag before
      building.
- [x] Existing GitHub release notes are updated with image tags and digest after
      the published-image smoke test passes.

## 5) GitHub Pages Documentation

Status: `IMPLEMENTED`

Approach:

- [x] Generate a static site from Markdown docs.
- [x] Use `README.md` as the site landing page.
- [x] Include every top-level `docs/*.md`.
- [x] Include `docs/improvements/*.md`.
- [x] Keep source Markdown as the canonical docs.

Recommended tooling:

- [x] Use MkDocs with Material theme for the first implementation.
- [x] Add `mkdocs.yml`.
- [x] Add docs publishing dependencies in `docs/requirements.txt`.
- [x] Copy or include `README.md` as `index.md` during docs build.
- [x] Generate navigation from existing docs.

Initial site structure:

- [x] Home: README
- [x] Architecture
- [x] Roadmap
- [x] Storage BYOA Plan
- [x] Release, Container, and Docs Publishing Plan
- [x] Agent Integration
- [x] MCP Plan
- [x] MCP Client Smoke Plan
- [x] Deterministic Ingestion Plan
- [x] Token Budget Plan
- [x] CLI Implementation Plan
- [x] Improvements

Pages workflow:

- [x] Add `.github/workflows/pages.yml`.
- [x] Trigger on pushes to `main`.
- [x] Trigger manually with `workflow_dispatch`.
- [x] Build docs site.
- [x] Upload Pages artifact.
- [x] Deploy to GitHub Pages.

Required GitHub settings:

- [ ] Enable GitHub Pages.
- [ ] Set Pages source to GitHub Actions.
- [x] Ensure workflow has:
  - [x] `contents: read`
  - [x] `pages: write`
  - [x] `id-token: write`

Docs quality checks:

- [x] Validate internal links.
- [x] Fail docs build on broken links.
- [x] Keep README links valid both on GitHub and in generated Pages.
- [x] Add a docs build check to CI before enabling deploy as required.

Pages acceptance criteria:

- [x] GitHub Pages deploys from `main`.
- [x] README content appears as the home page.
- [x] All important Markdown docs are reachable from navigation.
- [x] Broken internal links fail the docs workflow.
- [x] Release docs plan is visible on the site.

## 6) GitHub Release Notes

Status: `IMPLEMENTED`

- [x] Decide release notes source:
  - [ ] GitHub auto-generated release notes
  - [x] committed `CHANGELOG.md`
  - [ ] generated notes from conventional commits
- [x] Add release template/checklist.
- [x] Include Docker image tags and digest in release notes.
- [x] Automate release-note digest insertion in the Docker publish workflow.
- [x] Include docs URL in release notes.
- [ ] Include upgrade notes when config, storage schema, or container behavior changes.
- [ ] Call out whether the release updates `latest`.

## 7) Security And Supply Chain

Status: `PARTIAL`

- [x] Use least-privilege GitHub workflow permissions.
- [ ] Store Docker Hub credentials only in GitHub Actions secrets.
- [ ] Prefer Docker Hub access token over account password.
- [x] Add dependency vulnerability scan plan.
- [x] Add image vulnerability scan plan.
- [x] Consider SBOM generation:
  - [x] SPDX or CycloneDX
  - [x] attach to release or publish as image attestation
- [x] Consider provenance/signing:
  - [x] GitHub artifact attestations
  - [ ] Cosign signing later if needed
- [x] Document supported image lifecycle:
  - [x] supported tags
  - [x] deprecation policy
  - [x] security fix policy

## 8) Implementation Order

Status: `PLANNED`

Recommended sequence:

1. Add `.dockerignore`. Done.
2. Add container CI build and smoke test. Done.
3. Improve `Containerfile` labels and runtime docs. Done.
4. Add GitHub Pages MkDocs config and Pages workflow. Done.
5. Add release version validation script/check. Done.
6. Add Docker Hub publish workflow for GitHub releases. Done.
7. Add release template and release notes checklist. Done.
8. Add image scanning/SBOM/provenance after the basic path is reliable. Done.

## Final Acceptance Criteria

- [ ] Pull requests verify the container still builds.
- [x] Pull requests verify docs still build.
- [x] Pushes to `main` publish GitHub Pages docs.
- [x] GitHub releases publish Docker Hub images.
- [x] Docker image tags match release tags and project version.
- [x] Stable releases update `latest`; pre-releases do not.
- [x] Release notes include image tags, image digest, and docs URL.
- [x] Secrets are never printed in workflow logs.
