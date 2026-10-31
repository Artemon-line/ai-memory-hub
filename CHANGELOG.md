# Changelog

ai-memory-hub uses this changelog as the committed release notes source. GitHub
releases may additionally use generated notes, but the release summary here
should describe the supported public behavior for each tagged release.

## Unreleased

- Standardized benchmark latency distributions and separated cold first-call
  measurements from warm p50/p95/p99 samples while preserving existing report
  fields.
- Added a shared request-work ledger to deterministic efficiency reports for
  comparable storage, embedding, candidate, reranker, audit, and context-work
  totals and per-request amplification.
- Added a scheduled and manually dispatched efficiency report suite that uploads
  machine-readable benchmark manifests and before/after reports without adding
  load profiles to normal push or pull-request CI.
- Added credential-free local concurrency profiles for insert, search, and ask,
  reporting completion, throughput, and latency percentiles at configurable
  worker levels against versioned synthetic corpora.
- Added deterministic small, medium, and large synthetic efficiency corpora with
  broad and selective filter cases, streamable JSONL output, and fingerprinted
  machine-readable manifests.
- Added a distinct Connect UI matrix for implemented and planned zero-token
  import hooks, with pinned versions, parser metadata, and safe documentation links.
- Added a bounded zero-token DeepSeek Harness ZIP import command with safe
  extraction, explicit descendant selection, and formats 0 through 4 support.
- Added an opt-in zero-token Hermes Agent 0.22.5 export adapter with private
  temporary files, optional redaction, ended-session batches, and cleanup.
- Added an opt-in OpenClaw compatibility adapter for closed legacy version 3
  JSONL trees, with explicit refusal of current SQLite and sidecar files.
- Added an opt-in, explicit zero-token import adapter for closed Pi 0.73.0
  version 3 sessions without newest-file guessing or background watchers.
- Added a shared zero-token conversation uploader with closed-file snapshots,
  bounded safe receipts, and opt-in Codex, Claude Code, OpenCode, and Gemini CLI
  import recipes.
- Bounded remote embedding batches by configured input count and UTF-8 byte
  size, preserving deterministic chunk mapping and all-or-nothing vector writes.
- Reused the already verified, project-scoped aggregate conversation identity
  for complete-history trusted appends, while retaining safe recomputation for
  sibling continuations whose payload omits part of the stored history.
- Limited trusted-append graph extraction to newly appended messages while
  preserving absolute message-index provenance and conflict supersession.
- Removed exact repeated token-window overlap from budgeted ask and handoff
  context when adjacent chunks are proven to come from the same source message.
- Skipped semantic reranker projection and invocation when zero or one
  candidate can be reranked, with an explicit diagnostic mode and deterministic
  call-amplification coverage.
- Adapted unfiltered lexical and fact candidate limits to the requested
  `top_k` and configured candidate multiplier, while preserving configured
  maxima for filtered searches to protect post-filter recall.
- Deferred generated-summary work until after trusted-append detection, so an
  append writes summaries once for the combined conversation instead of first
  writing transient summaries for the incoming transcript.
- Reused assembled source-conversation search text within fact ranking and
  question narrowing requests, with a deterministic work-amplification
  benchmark and no cross-request payload cache.
- Replaced repeated fact-source conversation reads during filtering, ranking,
  projection, candidate construction, and provenance assembly with bounded,
  deduplicated metadata batch hydration and a machine-readable work benchmark.
- Short-circuited fully indexed exact-duplicate ingestion before derived-record,
  embedding, metadata, and vector work, while retaining incomplete-index repair
  and adding a machine-readable ingestion work ledger.
- Reused bounded, file-identity-aware compiled conversation validators across
  ingestion requests, with explicit invalidation and isolated multi-schema
  runtime behavior.
- Improved performance benchmark fidelity with exception-free in-memory audit
  accounting, first-call and warm latency distributions, p50/p95/p99 reporting,
  and per-request candidate/context amplification metrics.
- Added a versioned synthetic retrieval and extraction quality corpus with a
  credential-free CI gate, comparable provider/model reports, auditable
  thresholds, ranking/filter/fact/temporal metrics, latency percentiles, and
  reranker fallback coverage.
- Added fact/direct-memory evidence arbitration for `memory_ask`, including
  assistant-claim corroboration, cross-layer conflict provenance, corrected-fact
  precedence, and a high-confidence fact fast path.
- Added source-span validation and calibrated confidence for extracted facts,
  including assertion/temporal classification, bounded external extractor
  output, assistant corroboration semantics, and legacy fact compatibility.
- Added provider-scale-independent reciprocal rank fusion for dense, lexical,
  fact, and graph candidates, exact-match prioritization, and an optional
  bounded redaction-safe semantic reranker with deterministic fallback.
- Added shared typed query planning for search and ask, including deterministic
  source, date, tag, thread, handoff, intent, and temporal parsing; safe optional
  model-assisted planning; explicit-filter merge rules; and opt-in diagnostics.
- Added capability-aware filtered vector search with trusted owner/project
  pushdown, bounded metadata-validated fallback for unsupported constraints,
  filter diagnostics, and a documented persistent-index reindex path.
- Added safe `aim import latest <source>` discovery for Codex, Claude Code,
  GitHub Copilot CLI, Pi, and Oh My Pi, including documented environment
  overrides, active-file and ambiguity refusal, interactive preview,
  non-interactive JSON output, provider-matrix tests, and generated report
  evidence.
- Added a generic external OIDC resource-server mode with discovery/JWKS
  caching, asymmetric access-token validation, issuer-qualified identities,
  Keycloak role-to-scope mapping, allowlists, MCP authorization metadata, and
  cached provider availability in readiness and Connect UI diagnostics. Valid
  external identities now synchronize configurable username, email, and
  display-name claims into stable local user profiles.
- Added authenticated multipart `POST /memory/import` support for every
  registered conversation parser, with explicit parser selection, bounded
  uploads, safe indexed receipts, shared insertion policy enforcement, and a
  declarative versioned schema-override contract for Hermes exports.
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
