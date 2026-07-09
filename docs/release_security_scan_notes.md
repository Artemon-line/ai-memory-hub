# Release Security Scan Notes

This is a best-effort release security scan note for the first release branch.
The local Codex Security preflight could not certify a full multi-agent scan
capacity check in this desktop session, so treat this as a practical release
hardening pass rather than a formal audit sign-off.

## Fixed In This Pass

- MCP write tools now require `memory:write` even though `/mcp` itself only
  requires `memory:read` to initialize and call read tools. A read-only OAuth
  token can still search, but `memory_insert`, fact supersede, pending approve,
  and pending reject return `insufficient_scope`.
- LanceDB delete/replace filters now escape single quotes in `memory_id` values
  before passing filter strings to LanceDB.
- The root image remains a SQLite/LanceDB quickstart image. Optional providers
  stay in provider-local images or local `uv sync --extra ...` installs.
- Repository-owned container builds pin `uv==0.10.3` instead of pulling an
  unpinned installer from PyPI.
- Docker publish now checks out the requested manual release tag, builds a local
  candidate image, blocks on high/critical Trivy findings before pushing, then
  smoke-tests the published image by digest.
- User-facing Docker examples default to loopback binding. Remote exposure docs
  steer users to protected auth before binding beyond `127.0.0.1`.

## Still Open Security Hardening

- GitHub Actions are version-tag pinned, not commit-SHA pinned. Move to SHA pins
  once the release workflow set settles.
- Docker base images and provider service images use mutable tags. Pin by digest
  when you are ready to manage image refresh cadence explicitly.
- Dependency Review is still warning-only. Promote it to blocking after you have
  a clear vulnerability triage policy.
- The Ollama E2E workflow still uses the upstream install script. Replace it
  with a pinned package or cached test image when that job becomes release
  blocking.
- JSON schema validation checks structural payload shape, but format fields such
  as timestamps and URIs should keep targeted edge-case tests around release
  boundaries.

## GitHub Settings To Verify

- Require `Release Readiness`, `CodeQL Analysis`, `Dependency Review`, `Image
  Scan and SBOM`, and the existing unit/integration/container workflows before
  merging to `main`.
- Enable Dependabot alerts, secret scanning, push protection, CodeQL default
  setup or this repository workflow, and private vulnerability reporting where
  available.
- Keep `DOCKERHUB_USERNAME`, `DOCKERHUB_TOKEN`, and optional
  `DOCKERHUB_NAMESPACE` scoped to Docker publish only.
