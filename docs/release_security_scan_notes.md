# Release Security Scan Notes

This is a best-effort release security scan note for the first release branch.
The local Codex Security preflight could not certify a full multi-agent scan
capacity check in this desktop session, so treat this as a practical release
hardening pass rather than a formal audit sign-off.

## Fixed In This Pass

- ChromaDB is excluded from `v0.1.0` installable extras, live provider CI, and
  checked-in runnable examples because the upstream `chromadb` package has an
  unresolved critical advisory with no patched release. Re-enable only after a
  safe upstream version is available and Dependabot no longer reports the
  critical alert.
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
  smoke-tests the published image by digest and updates existing release notes
  with the promoted image digest.
- GitHub Actions are commit-SHA pinned with version comments.
- The Ollama E2E workflow uses a pinned `ollama/ollama:0.22.1` image digest
  instead of the upstream installer script.
- Real-client MCP smoke now runs on pull requests and pushes to `main`, in
  addition to weekly and manual runs.
- JSON schema validation now enforces declared formats such as `uuid` and
  `date-time`.
- User-facing Docker examples default to loopback binding. Remote exposure docs
  steer users to protected auth before binding beyond `127.0.0.1`.

## Still Open Security Hardening

- Docker base images and provider service images use mutable tags. Pin by digest
  when you are ready to manage image refresh cadence explicitly.
- Dependency Review is still warning-only. Promote it to blocking after you have
  a clear vulnerability triage policy.

## GitHub Settings To Verify

- Require `Release Readiness`, `CodeQL Analysis`, `Dependency Review`, `Image
  Scan and SBOM`, `Real-Client MCP Smoke`, and the existing
  unit/integration/container workflows before merging to `main`.
- Enable Dependabot alerts, secret scanning, push protection, CodeQL default
  setup or this repository workflow, and private vulnerability reporting where
  available.
- Keep `DOCKERHUB_USERNAME`, `DOCKERHUB_TOKEN`, and optional
  `DOCKERHUB_NAMESPACE` scoped to Docker publish only.
