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
- [ ] Container build is verified in CI.
- [ ] Container smoke test is verified in CI.
- [ ] Docker Hub publishing workflow exists.
- [ ] GitHub release workflow exists.
- [ ] GitHub Pages docs workflow exists.

## 1) Containerfile Maintenance

Status: `PLANNED`

Container contract:

- [ ] Build from the checked-in `Containerfile`.
- [ ] Use Python version compatible with `pyproject.toml`.
- [ ] Install dependencies from `uv.lock` with frozen resolution.
- [ ] Include `memory/`, `README.md`, `pyproject.toml`, `uv.lock`, and default config.
- [ ] Expose API/MCP service on port `8000`.
- [ ] Start through the project CLI once the serve command exists.
- [ ] Use direct `uvicorn memory.api.server:app` only as the current transitional fallback.
- [ ] Keep runtime defaults local-safe.

CLI dependency:

- [ ] Implement the serve command tracked in `docs/cli_implementation_plan.md`.
- [ ] Preferred final container command:

```bash
aim serve --host 0.0.0.0 --port 8000
```

- [ ] Development fallback until the console script exists:

```bash
python -m memory.cli serve --host 0.0.0.0 --port 8000
```

- [ ] Add packaging metadata for the selected console script before using `aim` in `Containerfile`.
- [ ] Keep `Containerfile` and CLI docs aligned whenever the serve command changes.

Required improvements:

- [ ] Add `serve` to the CLI implementation plan:
  - [ ] starts the same FastAPI/MCP app as the current `uvicorn` command
  - [ ] supports `--host`
  - [ ] supports `--port`
  - [ ] supports `--config`
  - [ ] returns stable exit code `3` on runtime initialization failure
  - [ ] logs the active config path and redacted provider summary
- [ ] Add OCI labels:
  - [ ] `org.opencontainers.image.title`
  - [ ] `org.opencontainers.image.description`
  - [ ] `org.opencontainers.image.source`
  - [ ] `org.opencontainers.image.revision`
  - [ ] `org.opencontainers.image.version`
  - [ ] `org.opencontainers.image.licenses`
- [ ] Add `.dockerignore` to keep builds small:
  - [ ] `.git`
  - [ ] `.venv`
  - [ ] `.pytest_cache`
  - [ ] `.ruff_cache`
  - [ ] `data`
  - [ ] local logs/artifacts
- [ ] Decide whether the image should install optional extras:
  - [ ] default image: core dependencies only
  - [ ] optional image variant: Postgres/PGVector support
  - [ ] optional image variant: tokenizer support
- [ ] Add a healthcheck command or document health endpoint once the API exposes one.
- [ ] Document runtime volume mounts:
  - [ ] `/app/data`
  - [ ] optional config override path
- [ ] Document runtime environment variables for config/secrets.

Container acceptance criteria:

- [ ] `docker build -f Containerfile .` succeeds locally.
- [ ] Container starts successfully.
- [ ] `GET /health` or equivalent smoke check succeeds if/when available.
- [ ] MCP endpoint remains reachable at `/mcp/`.
- [ ] Image does not include repo-local `data/`, `.git/`, or virtualenv files.

## 2) CI Container Verification

Status: `PLANNED`

Add CI checks before publishing is enabled:

- [ ] Add a `container` job to `.github/workflows/pipeline.yml`.
- [ ] Build the image on pull requests and pushes to `main`.
- [ ] Tag CI image locally as `ai-memory-hub:ci`.
- [ ] Run a container smoke test:
  - [ ] start container on port `8000`
  - [ ] wait for service readiness
  - [ ] call API health or docs endpoint
  - [ ] call MCP initialize smoke if lightweight enough
- [ ] Fail CI if container exits early.
- [ ] Upload container logs as an artifact on failure.
- [ ] Keep publishing disabled in PR builds.

Optional hardening:

- [ ] Add Hadolint or similar Dockerfile linting.
- [ ] Add Trivy image scan in warning mode first.
- [ ] Promote image scanning to required once false positives are triaged.

## 3) Release Version Policy

Status: `PLANNED`

Version source:

- [ ] Use `pyproject.toml` as the source package version.
- [ ] Use GitHub release tag as the released version.
- [ ] Require release tag to match project version:
  - [ ] `v0.1.0` tag must match `version = "0.1.0"`
  - [ ] fail release workflow if they differ

Tag policy:

- [ ] Release tags use `vMAJOR.MINOR.PATCH`.
- [ ] Docker image tags:
  - [ ] `docker.io/<dockerhub-namespace>/ai-memory-hub:vMAJOR.MINOR.PATCH`
  - [ ] `docker.io/<dockerhub-namespace>/ai-memory-hub:MAJOR.MINOR.PATCH`
  - [ ] `docker.io/<dockerhub-namespace>/ai-memory-hub:latest` for stable releases only
- [ ] Pre-release tags use suffixes such as `v0.2.0-rc.1`.
- [ ] Pre-releases publish version tags but do not update `latest`.

Release checklist:

- [ ] Update `pyproject.toml` version.
- [ ] Update changelog or release notes source.
- [ ] Ensure CI is green on `main`.
- [ ] Create GitHub release.
- [ ] Publish Docker Hub image from release workflow.
- [ ] Publish GitHub Pages docs from release or `main`.

## 4) Docker Hub Publishing Workflow

Status: `PLANNED`

Workflow trigger:

- [ ] Run on GitHub `release.published`.
- [ ] Allow manual `workflow_dispatch` for recovery/retry.
- [ ] Do not publish from pull requests.

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
- [ ] Optionally attach SBOM/provenance artifact.

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

## 5) GitHub Pages Documentation

Status: `PLANNED`

Approach:

- [ ] Generate a static site from Markdown docs.
- [ ] Use `README.md` as the site landing page.
- [ ] Include every top-level `docs/*.md`.
- [ ] Include `docs/improvements/*.md`.
- [ ] Keep source Markdown as the canonical docs.

Recommended tooling:

- [ ] Use MkDocs with Material theme for the first implementation.
- [ ] Add `mkdocs.yml`.
- [ ] Add docs publishing dependencies as a dev/docs dependency group if desired.
- [ ] Copy or include `README.md` as `index.md` during docs build.
- [ ] Generate navigation from existing docs.

Initial site structure:

- [ ] Home: README
- [ ] Architecture
- [ ] Roadmap
- [ ] Storage BYOA Plan
- [ ] Release, Container, and Docs Publishing Plan
- [ ] Agent Integration
- [ ] MCP Plan
- [ ] MCP Client Smoke Plan
- [ ] Deterministic Ingestion Plan
- [ ] Token Budget Plan
- [ ] CLI Implementation Plan
- [ ] Improvements

Pages workflow:

- [ ] Add `.github/workflows/pages.yml`.
- [ ] Trigger on pushes to `main`.
- [ ] Trigger manually with `workflow_dispatch`.
- [ ] Build docs site.
- [ ] Upload Pages artifact.
- [ ] Deploy to GitHub Pages.

Required GitHub settings:

- [ ] Enable GitHub Pages.
- [ ] Set Pages source to GitHub Actions.
- [ ] Ensure workflow has:
  - [ ] `contents: read`
  - [ ] `pages: write`
  - [ ] `id-token: write`

Docs quality checks:

- [ ] Validate internal links.
- [ ] Fail docs build on broken links.
- [ ] Keep README links valid both on GitHub and in generated Pages.
- [ ] Add a docs build check to CI before enabling deploy as required.

Pages acceptance criteria:

- [ ] GitHub Pages deploys from `main`.
- [ ] README content appears as the home page.
- [ ] All important Markdown docs are reachable from navigation.
- [ ] Broken internal links fail the docs workflow.
- [ ] Release docs plan is visible on the site.

## 6) GitHub Release Notes

Status: `PLANNED`

- [ ] Decide release notes source:
  - [ ] GitHub auto-generated release notes
  - [ ] committed `CHANGELOG.md`
  - [ ] generated notes from conventional commits
- [ ] Add release template/checklist.
- [ ] Include Docker image tags and digest in release notes.
- [ ] Include docs URL in release notes.
- [ ] Include upgrade notes when config, storage schema, or container behavior changes.
- [ ] Call out whether the release updates `latest`.

## 7) Security And Supply Chain

Status: `PLANNED`

- [ ] Use least-privilege GitHub workflow permissions.
- [ ] Store Docker Hub credentials only in GitHub Actions secrets.
- [ ] Prefer Docker Hub access token over account password.
- [ ] Add dependency vulnerability scan plan.
- [ ] Add image vulnerability scan plan.
- [ ] Consider SBOM generation:
  - [ ] SPDX or CycloneDX
  - [ ] attach to release or publish as image attestation
- [ ] Consider provenance/signing:
  - [ ] GitHub artifact attestations
  - [ ] Cosign signing later if needed
- [ ] Document supported image lifecycle:
  - [ ] supported tags
  - [ ] deprecation policy
  - [ ] security fix policy

## 8) Implementation Order

Status: `PLANNED`

Recommended sequence:

1. Add `.dockerignore`.
2. Add container CI build and smoke test.
3. Improve `Containerfile` labels and runtime docs.
4. Add GitHub Pages MkDocs config and Pages workflow.
5. Add release version validation script/check.
6. Add Docker Hub publish workflow for GitHub releases.
7. Add release template and release notes checklist.
8. Add image scanning/SBOM/provenance after the basic path is reliable.

## Final Acceptance Criteria

- [ ] Pull requests verify the container still builds.
- [ ] Pull requests verify docs still build.
- [ ] Pushes to `main` publish GitHub Pages docs.
- [ ] GitHub releases publish Docker Hub images.
- [ ] Docker image tags match release tags and project version.
- [ ] Stable releases update `latest`; pre-releases do not.
- [ ] Release notes include image tags, image digest, and docs URL.
- [ ] Secrets are never printed in workflow logs.
