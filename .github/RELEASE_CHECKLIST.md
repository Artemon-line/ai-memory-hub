# Release Checklist

Use this checklist for release PRs and release candidate drills.

## Before The Release PR

- [ ] Confirm `pyproject.toml` has the intended version.
- [ ] Update `CHANGELOG.md`.
- [ ] Review README quick start from a clean checkout.
- [ ] Review Docker/Compose quick start from a clean checkout.
- [ ] Confirm security/auth guidance is reachable from README.
- [ ] Confirm known limitations are accurate.

## Required Local Checks

```bash
uv run python -m ruff check memory tests tools
uv run pytest tests/unit tests/integration -q
uv run python -m pyright
uv run python tests/bruno/validate_files.py
uv run python tools/prepare_mkdocs.py
uv run python -m mkdocs build --strict
uv run python tools/validate_release_version.py v0.1.0
```

## GitHub Settings

- [ ] Branch protection blocks direct pushes to `main`.
- [ ] Pull requests are required before merge.
- [ ] Required status checks include the main CI jobs, docs build, and Bruno
      integration when relevant files change.
- [ ] Real-client MCP smoke remains manual/scheduled and non-required.
- [ ] Repository description and topics are reviewed.

## Release Candidate Drill

- [ ] Open the release-readiness PR.
- [ ] Confirm all required checks pass.
- [ ] Create a prerelease tag such as `v0.1.0-rc.1`.
- [ ] Publish a GitHub prerelease.
- [ ] Confirm Docker publishing creates version tags and does not update
      `latest`.
- [ ] Pull and run the image on a clean machine or clean container runtime.
- [ ] Run `curl -fsS http://127.0.0.1:8000/ready`.
- [ ] Run the README quick start from a clean checkout.

## Stable Release

- [ ] Publish `v0.1.0`.
- [ ] Confirm Docker version tags and `latest` were pushed.
- [ ] Add the image digest to the release notes.
- [ ] Confirm the docs site link works.
- [ ] Publish the launch/promotion note.
