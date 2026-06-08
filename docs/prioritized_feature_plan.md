# Prioritized Feature Plan

This plan captures unimplemented or partial features found while reconciling `docs/` with the current codebase.

## Priority Order

| Priority | Feature | Status | Existing plan |
|----------|---------|--------|---------------|
| P0 | Token-budgeted `memory_ask` and token-aware ingestion chunking | Implemented | `token_budget_plan.md`, `improvements/context_building_plan.md` |
| P0 | Retrieval precision: threshold, hybrid search, metadata rerank | Planned | `improvements/retrieval_precision_plan.md` |
| P0 | MCP client smoke coverage for Codex, Gemini, Copilot, Claude, opencode | Partial | `mcp_client_smoke_plan.md` |
| P1 | CLI commands | Partial | `cli_implementation_plan.md` |
| P1 | Platform-specific importers | Planned here | This doc and `roadmap.md` |
| P2 | Summaries, digests, consolidation | Planned here | This doc and `roadmap.md` |
| P2 | UI and developer experience | Planned here | This doc and `roadmap.md` |
| P3 | Graph memory, decay, shared memory, plugins | Planned here | This doc and `roadmap.md` |
| P4 | Optional encrypted cloud sync | Planned here | This doc and `roadmap.md` |

## P0: Token-Budgeted Ask And Token Chunking

Use the existing token plans as the source of truth:

- `token_budget_plan.md`
- `improvements/context_building_plan.md`

Implemented:

- [x] Add tokenizer config defaults and lazy tokenizer adapter with deterministic fallback.
- [x] Add optional `max_context_tokens` to API and MCP `memory_ask`.
- [x] Select, skip, or truncate retrieved chunks inside the budget.
- [x] Return non-breaking diagnostics such as selected/dropped chunks.
- [x] Add opt-in `chunking.strategy: token` ingestion windows with overlap.
- [x] Add unit, API, MCP, and regression tests.

## P0: Retrieval Precision

Use `improvements/retrieval_precision_plan.md` as the source of truth.

Implementation sequence:

- [ ] Add a conservative similarity threshold.
- [ ] Add keyword candidate matching alongside vector search.
- [ ] Merge vector and keyword scores deterministically.
- [ ] Add metadata-aware reranking for tags, source, and topics.
- [ ] Tune the combined pipeline against fixed fixtures.

## P0: MCP Client Smoke Coverage

Use `mcp_client_smoke_plan.md` as the source of truth.

MCP remains the primary agent integration path. Framework-specific examples should
not be tracked as a separate priority unless they prove a concrete MCP/API gap.

Implementation sequence:

- [x] Keep CI smoke tests focused on MCP protocol compatibility and client-shaped payloads.
- [x] Preserve the existing Codex, Gemini, VS Code Copilot, and opencode profile coverage.
- [ ] Add the missing Claude profile.
- [ ] Add explicit retrieve round-trip checks and metadata assertions.
- [ ] Add malformed payload negative cases with stable `invalid_input` envelopes.
- [ ] Add a separate real-client smoke harness for agent CLIs.
- [x] Prefer a deterministic local OpenAI/Anthropic-compatible test gateway over Ollama, because Ollama is not the system under test.
- [ ] Start with Claude Code and Copilot CLI because they expose clear base-URL/provider environment variables.
- [ ] Validate Codex, opencode, and Gemini headless commands before wiring them into CI.
- [ ] Keep real-client smoke tests in a separate scheduled/manual CI lane until they are stable enough for default PR gating.

## P1: CLI Commands

Use `cli_implementation_plan.md` as the source of truth.

Current status:

- [x] A basic `memory.cli` argparse entrypoint exists.
- [x] `python -m memory.cli tokenizer-check` is implemented for tokenizer diagnostics.
- [ ] Roadmap items `aim ingest <file>` and `aim search "<query>"` are not implemented.
- [ ] The distributable console script name is not finalized.

Implementation sequence:

- [ ] Stabilize the command namespace, console script name, global flags, and JSON/text output contract.
- [x] Keep `tokenizer-check` as the first diagnostics command and align future diagnostics with the same output shape.
- [ ] Implement `ingest <file>` using the same normalization, validation, hashing, dedupe, and storage path as API/MCP.
- [ ] Implement `search "<query>"` using the existing runtime search function and deterministic result shape.
- [ ] Add `retrieve <id>` and `ask "<question>"` only after ingest/search behavior is stable.
- [ ] Add `--config`, `--json`, `--top-k`, and clear exit-code behavior across commands.
- [ ] Add unit tests for parser/output behavior and smoke tests for success, invalid input, and storage round trips.

## P1: Platform-Specific Importers

Roadmap importers are not implemented. The current code normalizes common payload variants, but it does not parse external exports.

Implementation sequence:

- [ ] Define an importer interface that returns unified conversation payloads.
- [ ] Add manual paste importer first because it is lowest risk and useful across clients.
- [ ] Add Gemini Takeout parser.
- [ ] Add Claude HTML export parser.
- [ ] Add optional local log importers for VS Code Copilot, Ollama, LM Studio, and Llama Stack.
- [x] Keep storage and ingestion unchanged by feeding all importers through the existing normalized schema path.

## P2: Summaries And Consolidation

Implementation sequence:

- [ ] Add per-conversation summaries as metadata or a separate summary table.
- [ ] Add topic-level and daily/weekly digest generation.
- [ ] Add duplicate/redundant memory consolidation tooling.
- [ ] Add stable-fact extraction only after summary storage and provenance are reliable.

## P2: UI And Developer Experience

Implementation sequence:

- [x] Expose OpenAPI docs and examples for current HTTP contracts.
- [ ] Add a storage health and diagnostics view.
- [ ] Add search and conversation viewer flows.
- [ ] Add import/upload workflows after importers exist.
- [ ] Add Python SDK first, then TypeScript SDK.
- [ ] Add packaging only after CLI and SDK contracts are stable.

## P3: Advanced Memory

Implementation sequence:

- [ ] Add entity extraction and relationship storage behind an optional module.
- [ ] Add graph-aware retrieval only after entity quality is testable.
- [ ] Add relevance decay and importance scoring.
- [ ] Add shared memory spaces and agent-specific filters.
- [ ] Add plugin extension points once importer/provider boundaries are stable.

## P4: Optional Cloud Sync

Implementation sequence:

- [ ] Define encrypted backup/export format.
- [ ] Add local backup and restore before network sync.
- [ ] Add opt-in encrypted sync for SQLite metadata and vector bundles.
- [ ] Add multi-device conflict handling.

Cloud sync must stay opt-in and must not change the local-first default.
