# MCP Client Smoke Test Plan

## Goal

Verify that ai-memory-hub behaves correctly for MCP clients with different payload conventions, including Codex, Gemini, VS Code Copilot, Claude, and opencode.

The test strategy has two lanes:

- Contract profile smoke tests: verify MCP protocol behavior and client-shaped payloads without launching every real client.
- Real-client smoke tests: launch actual agent CLIs through a local test LLM gateway where the client has a reliable non-interactive mode, then ask the client to use ai-memory-hub MCP tools.

## Current Status

Implemented:

- [x] E2E test file: `tests/e2e/test_client_smoke_profiles.py`.
- [x] Streamable HTTP MCP transport exercised through `/mcp/`.
- [x] MCP initialize flow with per-profile `clientInfo.name` and `clientInfo.version`.
- [x] `memory_validate` called before insert.
- [x] `memory_insert` accepts profile-shaped payloads and returns a generated ID.
- [x] `memory_search` returns the inserted memory as a structured result.
- [x] `memory_ask` returns citations pointing back to the inserted memory.
- [x] Ollama-backed OpenAI-compatible embeddings are used through `nomic-embed-text`.
- [x] CI pulls `nomic-embed-text` for embedding smoke coverage.
- [x] CI pulls `qwen2.5:0.5b` and sets `AMH_OLLAMA_CHAT_MODEL` for the chat smoke check.
- [x] Client profile: Codex-style payload using top-level `conversation`, message `content`, tags, and saved timestamp metadata.
- [x] Client profile: Gemini-style payload using `messages`, message `content`, and model metadata.
- [x] Client profile: VS Code Copilot-style payload using message `text`, workspace metadata, and tags.
- [x] Client profile: opencode-style payload using local coding-agent source metadata.
- [x] Client profile: Claude-style payload using `source: claude-code`, Anthropic model metadata, message `content`, and tags.
- [x] Explicit `memory_retrieve` call in the E2E profile flow.
- [x] Profile-specific source, tags, metadata, and first-message assertions after retrieval.
- [x] Negative profile cases for malformed client payloads with stable `invalid_input` envelopes.
- [x] Basic Ollama chat-completion smoke check.

Real-client smoke coverage has been extracted to
`real_client_mcp_smoke_plan.md`.

## Test Shape

Each client profile should define:

- `name`: MCP `clientInfo.name`.
- `version`: MCP `clientInfo.version`.
- `payload`: a realistic client-shaped conversation payload.
- `query`: a query expected to retrieve the inserted memory.
- Optional expected metadata/tags/source assertions.

Each profile should run the same MCP flow:

1. Initialize MCP session.
2. Call `memory_validate`.
3. Call `memory_insert`.
4. Call `memory_search`.
5. Call `memory_retrieve`.
6. Call `memory_ask`.
7. Assert inserted ID round-trips through search, retrieve, and ask citations.

## Client Coverage

| Client | Contract profile | Real-client smoke | Notes |
|--------|------------------|-------------------|-------|
| Codex | Done | Planned | Contract profile covers `conversation` array, `content`, top-level tags, and saved timestamp metadata. Codex supports custom OpenAI-compatible model providers and streamable HTTP MCP server config. |
| Gemini | Done | Planned | Contract profile covers Gemini-like source and model metadata with `content` messages. Gemini CLI has MCP config support; model-provider override still needs a current reliable headless path before CI wiring. |
| VS Code Copilot / Copilot CLI | Done | Planned | Contract profile covers Copilot-like source, `text` messages, workspace metadata, and tags. Copilot CLI supports custom OpenAI-compatible provider env vars. |
| opencode | Done | Planned | Contract profile covers local coding-agent source metadata with model hints. opencode supports custom providers with OpenAI-compatible `baseURL`. |
| Claude Code | Done | Planned | Contract profile covers `source: claude-code`, Anthropic model metadata, `content` messages, and tags. Claude Code supports Anthropic API endpoint override through environment variables. |

## Implementation Plan

### Phase 1: Complete Profile Matrix

Status: partial.

- [x] Add Codex profile.
- [x] Add Gemini profile.
- [x] Add VS Code Copilot profile.
- [x] Add opencode profile.
- [x] Add Claude profile.
- [x] Add profile-level assertions for source, tags, and metadata preservation.

### Phase 2: Round-Trip Hardening

Status: implemented for contract profile smoke tests.

- [x] Add `memory_retrieve` after insert and assert retrieved memory ID/source/messages match the profile.
- [x] Assert `memory_search` and `memory_ask` do not leak internal hash fields.
- [x] Assert all profiles work when the input omits `id`.
- [x] Assert each profile returns the stable MCP envelope fields.

### Phase 3: Negative Contract Cases

Status: implemented for the broad payload styles covered by the contract profile matrix.

- [x] Add malformed message shape cases for each broad payload style.
- [x] Add invalid role and empty message cases.
- [x] Add invalid explicit ID case.
- [x] Assert machine-readable `invalid_input` envelopes.

### Phase 4: CI Stability

Status: partial.

- [x] Run E2E tests in CI with Ollama installed.
- [x] Pull required embedding model.
- [x] Pull lightweight chat model for smoke completion.
- [ ] Consider splitting MCP profile smoke tests from slower live-chat smoke tests if CI runtime becomes noisy.
- [ ] Add a documented local command for running only profile smoke tests.

### Phase 5: Real-Client Smoke Lane

Status: extracted.

Use `real_client_mcp_smoke_plan.md` as the source of truth for the weekly
scheduled real-client MCP smoke lane.

## Design Notes

- Keep CI focused on protocol compatibility and payload normalization.
- Do not require vendor credentials for the default pipeline.
- Keep the MCP server as the source of truth for validation, ID generation, hashing, embedding, dedupe, and storage.

## Native Agent Candidate Discovery

Ollama's launcher catalog is useful for discovering popular agent tools, but it
is not the ai-memory-hub support matrix. Use native install, config, and run
paths when evaluating real-client support.

Track Hermes Agent, NVIDIA OpenShell, Codex App, OpenClaw, Droid, and Pi as
native real-client candidates in `real_client_mcp_smoke_plan.md`. The preferred
path is documented native setup, manual validation, ai-memory-hub fixes from
real findings, then supported status. Add automated real-client coverage after
the manual workflow is stable. Add mocked/profile coverage only when real
testing reveals payload or response-shape quirks worth preserving.

OpenClaw native setup is tracked in `openclaw_native_mcp_setup.md`.
