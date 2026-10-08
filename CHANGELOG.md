# Changelog

ai-memory-hub uses this changelog as the committed release notes source. GitHub
releases may additionally use generated notes, but the release summary here
should describe the supported public behavior for each tagged release.

## Unreleased

- Added a schema-backed Hermes Agent JSONL importer for single-session and
  multi-session exports, preserving safe provenance and live conversational
  text while excluding system, tool, reasoning, rewound, synthetic summary,
  billing, usage, and identity data.
- Added a versioned Factory Droid Exec capture importer for prompt/result and
  bidirectional JSON-RPC recordings, preserving completed visible turns while
  excluding reasoning, tools, errors, usage, and control frames.
- Added a schema-backed DeepSeek Harness canonical session JSONL importer for
  extracted root and descendant logs, preserving safe session provenance while
  excluding internal context, reasoning, tools, compaction, usage, and
  attachments.
- Added a schema-backed GitHub Copilot CLI `events.jsonl` importer that keeps
  top-level user and assistant text while excluding synthetic prompts,
  sub-agent traffic, reasoning, tools, compaction, usage, and telemetry.
- Added a native Qwen Code session importer for `/export json` and
  `/export jsonl` with schema-backed message validation, UUID deduplication,
  and omission of system, goal-state, tool, usage, and unknown records.
- Added a native Pi-family session JSONL importer for Pi, Oh My Pi, and
  archived/legacy OpenClaw transcripts with active-branch reconstruction and
  schema-backed tree validation.
- Added a native Gemini CLI session JSON importer for `/export-session`,
  `/chat share` JSON, and compatible legacy saved-chat objects while omitting
  injected context, thoughts, tools, token usage, and diagnostics.
- Added a native Claude Code session JSONL importer that keeps visible user and
  assistant text while omitting reasoning, tools, system records, usage,
  compaction summaries, and file snapshots.
- Added a native Codex CLI/app rollout JSONL importer, verified against Codex
  CLI 0.152.0, that keeps canonical user and assistant text while omitting
  reasoning, tools, injected startup context, and runtime-only events.
- Added a native OpenCode session-export JSON importer, including CLI support
  and a live OpenCode/Ollama export, hub insert, and cited query round-trip
  smoke test.
- Updated the container PCRE package and locked urllib3 release to their fixed
  versions after the supply-chain scan detected new high-severity advisories.

## 1.0.0-beta

- Added first-release governance, support, and release automation scaffolding.
- Fixed detailed `memory_retrieve` index manifests so successfully embedded
  chunks report `indexed` instead of stale `pending_index`.
- Fixed `memory_ask` routing so active latest fact projections are preferred
  before direct chunk fallback, including corrected temporal facts.
- Fixed `memory_profile_get` summary text so duplicate active fact rows are
  collapsed into canonical human-readable lines without mutating the timeline.
- Patched the Bruno test toolchain against `js-yaml` CPU-exhaustion and
  `csv-parse` prototype-replacement vulnerabilities.

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
