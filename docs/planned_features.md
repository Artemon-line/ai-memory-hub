# Planned Features

This page separates future work from shipped behavior. The roadmap remains the
source of detailed phase planning, while this page gives readers a quick view of
what is still planned or intentionally deferred.

## Near-Term Work

| Area | Planned capability | Notes |
| --- | --- | --- |
| Ingestion quality | Message role normalization and basic topic tagging for non-conforming upstream sources. | Completes the remaining Core MVP normalization tasks. |
| Capture adapters | Gemini Takeout, Claude HTML export, local LLM logs, and optional VS Code Copilot log parsing. | Adapters should emit normalized records and keep storage boundaries stable. |
| Browser extension path | Separate extension repos that parse web UIs and post normalized payloads to `POST /memory/insert`. | The API should not parse raw HTML or DOM snapshots. |
| Observability tracing | Manual spans around MCP tools, ingestion stages, and retrieval stages. | Automatic tracing and metrics already exist; domain spans are the main remaining observability gap. |
| Documentation | Keep GitHub Pages focused on shipped behavior, with implementation plans available as references. | This page is the first pass at that split. |

## Product And Developer Experience

| Area | Planned capability |
| --- | --- |
| UI | Local dashboard, search UI, storage health panel, and timeline-style browsing. |
| SDKs | Python and TypeScript SDKs over the stable HTTP/MCP surfaces. |
| OpenAPI | Published OpenAPI spec and generated client-friendly examples. |
| Packaging | Release, container, plugin, and install flows that are easy to verify locally. |
| Plugin readiness | Codex/opencode plugin setup, health checks, and client-focused docs. |

## Intelligence Layer

These features turn stored conversations into a more organized memory system:

- Per-conversation and per-topic summaries.
- Daily or weekly digest generation.
- Hosted LLM fact extractor implementation with prompt and schema tuning.
- Topic clustering and topic graph generation.
- Timeline reconstruction for project and profile memory.
- Memory consolidation for redundant conversations and stable facts.
- Retrieval quality evaluation workflows that track precision over representative
  queries.

## Advanced Memory And Sync

Later roadmap phases remain optional and should preserve local-first defaults:

- Shared team memory spaces with explicit permission boundaries.
- Memory decay, pinning, confidence, and review workflows.
- Graph relationships between people, projects, tools, and decisions.
- Encrypted backup and multi-device sync.
- Optional cloud deployment paths.

## Where Detailed Plans Live

Use [Documentation map](plans.md) when you need the implementation-level plan
for a specific area. Use [Roadmap](roadmap.md) when you want the full phase model.

