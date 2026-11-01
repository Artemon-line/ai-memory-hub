# Planned Features

GitHub issues own the implementation backlog. This page summarizes the product
outcomes that remain after reconciling old plans against code, tests, workflows,
and issue history. See [Project status](project_status.md) for the evidence and
classification.

## Active Programs

- Multi-profile efficiency and operational hardening is tracked in
  [#344](https://github.com/Artemon-line/ai-memory-hub/issues/344).
- Release governance [#345](https://github.com/Artemon-line/ai-memory-hub/issues/345),
  provenance [#346](https://github.com/Artemon-line/ai-memory-hub/issues/346),
  security hardening [#347](https://github.com/Artemon-line/ai-memory-hub/issues/347),
  encryption [#348](https://github.com/Artemon-line/ai-memory-hub/issues/348),
  capture/import coverage [#349](https://github.com/Artemon-line/ai-memory-hub/issues/349),
  plugin/client experience [#350](https://github.com/Artemon-line/ai-memory-hub/issues/350),
  UI/SDKs [#351](https://github.com/Artemon-line/ai-memory-hub/issues/351),
  advanced memory [#352](https://github.com/Artemon-line/ai-memory-hub/issues/352),
  and graph/provider expansion [#353](https://github.com/Artemon-line/ai-memory-hub/issues/353)
  are tracked as GitHub epics.

## Near-Term Outcomes

- Enforce the intended release checks and prove a release candidate end to end.
- Preserve per-message agent/model provenance without changing existing hashes.
- Finish provider-login PKCE, rate bounds, SQL identifier guardrails, and safe
  network-exposure defaults.
- Package the existing MCP service as a secure, local-first Codex plugin.
- Validate remaining import formats against real, versioned export contracts.

## Later Outcomes

- Opt-in app-level payload encryption with migration, rotation, and backup
  operations.
- A memory workspace for search, browsing, review, timelines, and diagnostics,
  plus supported Python and TypeScript SDKs.
- Cited digests, calibrated model-backed extraction, consolidation, lifecycle
  controls, and other advanced memory capabilities over immutable source data.
- Optional Neo4j graph storage and graph-aware retrieval after provider,
  isolation, quality, and latency gates pass.
- Optional encrypted backup and multi-device sync after local backup/restore and
  conflict semantics are proven.

Local-first behavior, explicit authorization, immutable evidence, redaction,
provider-neutral contracts, and deterministic fallbacks remain constraints for
all future work.
