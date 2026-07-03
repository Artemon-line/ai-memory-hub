# First Release Readiness Plan

This plan tracks what must be true before the first public release of
ai-memory-hub. It complements `release_container_docs_plan.md`, which focuses on
container and docs publishing mechanics. This plan covers the broader release
surface: repository governance, PR policy, security posture, release artifacts,
Docker publishing, documentation, and launch readiness.

## Release Goal

Ship `v0.1.0` as a credible first public release:

- users can understand what the project does within one minute;
- a clean checkout can run the documented quick start;
- CI is green and visible;
- releases are created through a repeatable process;
- Docker images are published from releases, not from ad hoc local pushes;
- external contributors know how to report issues and contribute;
- `main` is protected from direct pushes.

## Current State

Already in place:

- [x] MIT license.
- [x] Project version in `pyproject.toml`.
- [x] README with quick start, runtime choices, docs links, and CI badge.
- [x] MkDocs site and GitHub Pages workflow.
- [x] Main CI workflow with unit, integration, E2E, storage, container, and
      Hadolint checks.
- [x] Bruno black-box integration workflow for API/MCP smoke coverage.
- [x] Pytest and Bruno JUnit result publishing in CI.
- [x] Containerfile with OCI labels, non-root runtime, and CI smoke coverage.
- [x] Release/container publishing plan.
- [x] Promotion plan.

Missing or not yet enforced:

- [x] Docker Hub publish workflow.
- [x] Release workflow/checklist.
- [x] Version/tag validation.
- [x] `CHANGELOG.md` or chosen release notes source.
- [x] `CONTRIBUTING.md`.
- [x] `CODE_OF_CONDUCT.md`.
- [x] `SECURITY.md`.
- [x] Pull request template.
- [x] Issue templates.
- [ ] Branch protection policy that blocks direct pushes to `main`.
- [ ] Required status checks selected in GitHub settings.
- [ ] Repository topics and description reviewed for discoverability.

## Release Scope

For `v0.1.0`, the release should promise the implemented backend/service
capabilities only:

- HTTP memory API.
- Streamable HTTP MCP tools.
- SQLite/LanceDB local default.
- Postgres/PGVector runtime option.
- Deterministic ingestion, search, retrieve, ask, facts, and generated summaries.
- CLI and container runtime.
- Bearer-token auth and project workspace boundaries.
- Local-first, bring-your-own embedding model/storage posture.

Do not market unreleased browser extensions, hosted sync, UI dashboards, SDKs, or
cloud service behavior as shipped release features.

## P0: Repository Governance

These are required before the first release is promoted.

- [ ] Stop direct pushes to `main`.
- [ ] Require pull requests for all changes after the release-readiness PR lands.
- [ ] Require the main CI workflow before merge:
  - `Unit and Integration Tests`
  - `E2E Scenario (Ollama)`
  - `Storage Config Variations`
  - `Storage Postgres Integration`
  - `Containerfile Lint`
  - `Container Build and Smoke`
- [ ] Require the docs build workflow before merge.
- [ ] Require the Bruno workflow when files under `memory/**`, `tests/bruno/**`,
      `.github/workflows/bruno-integration.yml`, `pyproject.toml`, or `uv.lock`
      change.
- [ ] Keep real-client MCP smoke manual/scheduled and non-required until it is
      stable enough for PR gating.
- [ ] Require branch to be up to date before merge once queue/noise is
      manageable.
- [ ] Decide whether signed commits are mandatory immediately. If yes, configure
      local signing before enabling enforcement.
- [x] Document the PR strategy in `CONTRIBUTING.md`:
  - one focused change per PR;
  - tests or docs for behavior changes;
  - no direct `main` pushes;
  - release changes go through a release PR.

## P0: Community And Support Files

Add these root-level files before release:

- [x] `CONTRIBUTING.md`
  - local setup;
  - test commands;
  - documentation build commands;
  - PR expectations;
  - branch/commit policy;
  - release PR policy.
- [x] `CODE_OF_CONDUCT.md`
  - use a standard Contributor Covenant-style policy unless there is a reason
    to customize heavily.
- [x] `SECURITY.md`
  - supported versions;
  - how to report vulnerabilities privately;
  - expected response window;
  - reminder not to include secrets or private conversation data in reports.
- [x] `.github/PULL_REQUEST_TEMPLATE.md`
  - summary;
  - validation run;
  - docs impact;
  - security/privacy impact;
  - release note needed.
- [x] `.github/ISSUE_TEMPLATE/bug_report.yml`
- [x] `.github/ISSUE_TEMPLATE/feature_request.yml`
- [x] `.github/ISSUE_TEMPLATE/config.yml`

## P0: Versioning And Release Notes

- [x] Decide release notes source:
  - GitHub generated release notes for the first release; or
  - committed `CHANGELOG.md` from the start.
- [x] Add a release checklist file, such as
      `.github/RELEASE_CHECKLIST.md`.
- [x] Add version validation:
  - release tag must be `vMAJOR.MINOR.PATCH`;
  - tag `v0.1.0` must match `pyproject.toml` `version = "0.1.0"`;
  - release workflow fails if tag and project version differ.
- [x] Define prerelease policy:
  - `v0.1.0-rc.1` is allowed for release candidates;
  - prereleases do not update Docker `latest`.
- [x] Release notes must include:
  - one-paragraph project summary;
  - install/run commands;
  - Docker image tags and digest once publishing exists;
  - docs URL;
  - known limitations;
  - upgrade notes for config, storage schema, or container behavior.

## P0: Docker Image Publishing

Use `release_container_docs_plan.md` for the detailed workflow. Minimum first
release requirements:

- [ ] Create Docker Hub repository for `ai-memory-hub`.
- [ ] Add GitHub Actions secrets:
  - `DOCKERHUB_USERNAME`
  - `DOCKERHUB_TOKEN`
  - optional `DOCKERHUB_NAMESPACE`
- [x] Add `.github/workflows/docker-publish.yml`.
- [x] Trigger publish only on `release.published` and manual retry.
- [x] Build with Docker Buildx from the checked-in `Containerfile`.
- [x] Validate tag against `pyproject.toml`.
- [x] Push immutable version tags:
  - `v0.1.0`
  - `0.1.0`
- [x] Push `latest` only for stable releases.
- [x] Add image digest to the workflow summary and release notes.
- [x] Keep PR and push builds as smoke-only, with no registry push.

## P0: Documentation Readiness

- [ ] README first screen clearly says what the project is, who it is for, and
      why local-first agent memory matters.
- [ ] Quick start works from a clean checkout.
- [ ] Docker/Compose quick start works from a clean checkout.
- [x] MCP client setup is reachable from README.
- [x] Security/auth guidance is reachable before LAN/container exposure docs.
- [x] Release notes link to the generated docs site.
- [x] Known limitations are explicit:
  - no hosted memory service;
  - bring-your-own embedding model for production-quality retrieval;
  - browser extensions are future/separate repos;
  - UI and SDKs are future work.

## P0: Release Candidate Drill

Before publishing `v0.1.0`, run one release-candidate rehearsal:

1. Open a release-readiness PR.
2. Confirm all required checks pass.
3. Create a prerelease tag such as `v0.1.0-rc.1`.
4. Publish a GitHub prerelease.
5. Confirm Docker publish workflow produces only prerelease/version tags and not
   `latest`.
6. Pull the image on a clean machine or clean container runtime.
7. Run:

```bash
docker run --rm -p 8000:8000 <image>:v0.1.0-rc.1
curl -fsS http://127.0.0.1:8000/ready
```

8. Run the README quick start from a clean checkout.
9. Fix any release-note, Docker, docs, or setup issues before stable release.

## P1: Supply Chain And Security Hardening

These are valuable but should not block `v0.1.0` unless the image is promoted as
production-ready.

- [x] Add Trivy image scan in warning mode.
- [x] Add dependency vulnerability reporting.
- [x] Add SBOM generation for Docker images.
- [x] Add GitHub artifact attestations or provenance.
- [ ] Consider Cosign signing after the basic release process is stable.
- [x] Document image support lifecycle and security-fix policy.

## P1: Repository Discoverability

- [ ] Set GitHub repository description to match README positioning.
- [ ] Add repository topics:
  - `mcp`
  - `ai-agents`
  - `memory`
  - `rag`
  - `fastapi`
  - `pgvector`
  - `local-first`
  - `openai-compatible`
- [ ] Pin the first release announcement issue or discussion.
- [ ] Add a small demo GIF or terminal transcript when available.
- [ ] Make sure the promotion plan has one launch-day post and one follow-up
      technical post ready.

## First Release Checklist

Use this checklist for the release PR:

- [x] `CONTRIBUTING.md` added.
- [x] `CODE_OF_CONDUCT.md` added.
- [x] `SECURITY.md` added.
- [x] Issue templates added.
- [x] Pull request template added.
- [x] Docker publish workflow added.
- [x] Version/tag validation added.
- [x] Release checklist added.
- [ ] README and docs reviewed.
- [ ] Branch protection enabled.
- [ ] Required checks selected.
- [ ] `main` is green.
- [ ] `v0.1.0-rc.1` release candidate drill completed.
- [ ] Stable `v0.1.0` release published.
- [ ] Docker image pull/run verified.
- [ ] Release notes include image digest and docs URL.
- [ ] Launch/promotion note posted.

## Acceptance Criteria

The project is release-ready when:

- no routine work requires direct pushes to `main`;
- new contributors have clear conduct, contribution, security, issue, and PR
  guidance;
- the release process can be repeated from a tag without local manual Docker
  publishing;
- CI, docs, Bruno, and container smoke checks are visible and passing;
- users can install or run from source and Docker using documented commands;
- the first release notes accurately describe shipped behavior and known limits.
