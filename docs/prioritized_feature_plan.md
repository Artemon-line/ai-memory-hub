# Prioritized Feature Plan

This plan captures unimplemented or partial features found while reconciling `docs/` with the current codebase.

## Priority Order

| Priority | Feature | Status | Existing plan |
|----------|---------|--------|---------------|
| P0 | Token-budgeted `memory_ask` | Planned | `token_budget_plan.md`, `improvements/context_building_plan.md` |
| P0 | Retrieval precision: threshold, hybrid search, metadata rerank | Planned | `improvements/retrieval_precision_plan.md` |
| P0 | MCP client smoke coverage for Codex, Gemini, Copilot, Claude, opencode | Partial | `mcp_client_smoke_plan.md` |
| P1 | CLI ingest/search commands | Planned here | This doc |
| P1 | Platform-specific importers | Planned here | This doc and `roadmap.md` |
| P2 | Summaries, digests, consolidation | Planned here | This doc and `roadmap.md` |
| P2 | UI and developer experience | Planned here | This doc and `roadmap.md` |
| P3 | Framework-specific agent integrations | Planned here | This doc and `agents.md` |
| P4 | Graph memory, decay, shared memory, plugins | Planned here | This doc and `roadmap.md` |
| P5 | Optional encrypted cloud sync | Planned here | This doc and `roadmap.md` |

## P0: Token-Budgeted Ask

Use the existing token plans as the source of truth:

- `token_budget_plan.md`
- `improvements/context_building_plan.md`

Implementation sequence:

1. Add tokenizer config defaults and lazy tokenizer adapter with deterministic fallback.
2. Add optional `max_context_tokens` to API and MCP `memory_ask`.
3. Select, skip, or truncate retrieved chunks inside the budget.
4. Return non-breaking diagnostics such as selected/dropped chunks.
5. Add unit, API, MCP, and regression tests.

## P0: Retrieval Precision

Use `improvements/retrieval_precision_plan.md` as the source of truth.

Implementation sequence:

1. Add a conservative similarity threshold.
2. Add keyword candidate matching alongside vector search.
3. Merge vector and keyword scores deterministically.
4. Add metadata-aware reranking for tags, source, and topics.
5. Tune the combined pipeline against fixed fixtures.

## P0: MCP Client Smoke Coverage

Use `mcp_client_smoke_plan.md` as the source of truth.

Implementation sequence:

1. Keep CI smoke tests focused on MCP protocol compatibility and client-shaped payloads.
2. Preserve the existing Codex, Gemini, VS Code Copilot, and opencode profile coverage.
3. Add the missing Claude profile.
4. Add explicit retrieve round-trip checks and metadata assertions.
5. Add malformed payload negative cases with stable `invalid_input` envelopes.
6. Add a separate real-client smoke harness for agent CLIs.
7. Prefer a deterministic local OpenAI/Anthropic-compatible test gateway over Ollama, because Ollama is not the system under test.
8. Start with Claude Code and Copilot CLI because they expose clear base-URL/provider environment variables.
9. Validate Codex, opencode, and Gemini headless commands before wiring them into CI.
10. Keep real-client smoke tests in a separate scheduled/manual CI lane until they are stable enough for default PR gating.

## P1: CLI Commands

Current roadmap items `aimh ingest <file>` and `aimh search "<query>"` are not implemented.

Implementation sequence:

1. Add CLI argument parsing in `memory/cli.py`.
2. Implement `aimh ingest <file>` using the same normalization and validation path as API/MCP.
3. Implement `aimh search "<query>"` using the existing runtime search function.
4. Add `--config`, `--top-k`, and machine-readable JSON output options.
5. Add smoke tests for success and invalid-input behavior.

## P1: Platform-Specific Importers

Roadmap importers are not implemented. The current code normalizes common payload variants, but it does not parse external exports.

Implementation sequence:

1. Define an importer interface that returns unified conversation payloads.
2. Add manual paste importer first because it is lowest risk and useful across clients.
3. Add Gemini Takeout parser.
4. Add Claude HTML export parser.
5. Add optional local log importers for VS Code Copilot, Ollama, LM Studio, and Llama Stack.
6. Keep storage and ingestion unchanged by feeding all importers through the existing normalized schema path.

## P2: Summaries And Consolidation

Implementation sequence:

1. Add per-conversation summaries as metadata or a separate summary table.
2. Add topic-level and daily/weekly digest generation.
3. Add duplicate/redundant memory consolidation tooling.
4. Add stable-fact extraction only after summary storage and provenance are reliable.

## P2: UI And Developer Experience

Implementation sequence:

1. Expose OpenAPI docs and examples for current HTTP contracts.
2. Add a storage health and diagnostics view.
3. Add search and conversation viewer flows.
4. Add import/upload workflows after importers exist.
5. Add Python SDK first, then TypeScript SDK.
6. Add packaging only after CLI and SDK contracts are stable.

## P3: Agent Integrations

Implementation sequence:

1. Keep MCP as the primary generic agent interface.
2. Add LangGraph tool examples for search, retrieve, and ask.
3. Add Llama Stack memory-provider wiring.
4. Add OpenAI function-calling examples that call the HTTP API.

## P4: Advanced Memory

Implementation sequence:

1. Add entity extraction and relationship storage behind an optional module.
2. Add graph-aware retrieval only after entity quality is testable.
3. Add relevance decay and importance scoring.
4. Add shared memory spaces and agent-specific filters.
5. Add plugin extension points once importer/provider boundaries are stable.

## P5: Optional Cloud Sync

Implementation sequence:

1. Define encrypted backup/export format.
2. Add local backup and restore before network sync.
3. Add opt-in encrypted sync for SQLite metadata and vector bundles.
4. Add multi-device conflict handling.

Cloud sync must stay opt-in and must not change the local-first default.
