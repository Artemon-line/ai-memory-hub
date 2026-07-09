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
uv run python tools/validate_release_version.py v0.1.0
```

Ruff, Pyright, Bruno file validation, strict docs build, and release tag policy
are covered by the `Release Readiness` workflow. Run them locally only when
debugging failures or preparing an offline release candidate.

## GitHub Settings

- [ ] Branch protection blocks direct pushes to `main`.
- [ ] Pull requests are required before merge.
- [ ] Required status checks include the main CI jobs, docs build, and Bruno
      integration when relevant files change.
- [ ] Required status checks include `Release Readiness` and `CodeQL Analysis`.
- [ ] Required status checks include `Real-Client MCP Smoke`.
- [ ] Repository description and topics are reviewed.

## Release Candidate Drill

- [ ] Open the release-readiness PR.
- [ ] Confirm all required checks pass.
- [ ] Create a prerelease tag such as `v0.1.0-rc.1`.
- [ ] Publish a GitHub prerelease.
- [ ] Confirm Docker publishing creates version tags and does not update
      `latest`.
- [ ] Confirm Docker publishing checked out the requested release tag and passed
      the blocking image scan.
- [ ] Pull and run the image on a clean machine or clean container runtime.
- [ ] Confirm the Docker publish workflow smoke-tested the pushed image by
      digest.
- [ ] Confirm the Docker publish workflow updated the release notes digest
      block.
- [ ] Run the README quick start from a clean checkout.

## Stable Release

- [ ] Publish `v0.1.0`.
- [ ] Confirm Docker version tags and `latest` were pushed.
- [ ] Confirm the image digest is present in the release notes.
- [ ] Confirm the docs site link works.
- [ ] Publish the launch/promotion note.
