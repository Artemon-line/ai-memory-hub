# Changelog

ai-memory-hub uses this changelog as the committed release notes source. GitHub
releases may additionally use generated notes, but the release summary here
should describe the supported public behavior for each tagged release.

## Unreleased

- Added first-release governance, support, and release automation scaffolding.

## 0.1.0

Initial public release candidate scope:

- HTTP memory API and streamable HTTP MCP tools.
- SQLite/LanceDB local default storage.
- Postgres/PGVector runtime option.
- Deterministic ingestion, search, retrieve, ask, facts, and summaries.
- CLI and container runtime.
- Bearer-token auth and project workspace boundaries.
- Local-first, bring-your-own embedding model/storage posture.

Install and run from source:

```bash
uv sync
uv run aim serve --host 127.0.0.1 --port 8000
curl -fsS http://127.0.0.1:8000/ready
```

Run the release image after Docker Hub publishing is configured:

```bash
docker run --rm -p 8000:8000 docker.io/<namespace>/ai-memory-hub:v0.1.0
curl -fsS http://127.0.0.1:8000/ready
```

Release notes must be updated with the final image digest after the publish
workflow completes. The documentation site is published from the checked-in
MkDocs configuration through the `pages` workflow.

Known limitations:

- No hosted memory service is included.
- Production-quality retrieval requires a bring-your-own embedding model.
- Browser extensions are planned as separate adapters and are not part of this
  release.
- UI dashboards and SDKs are future work.
