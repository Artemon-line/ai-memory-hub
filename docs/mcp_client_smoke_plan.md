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
- [x] Basic Ollama chat-completion smoke check.

Not implemented yet:

- [ ] Client profile: Claude-style payload.
- [ ] Negative profile cases for malformed client payloads.
- [ ] Profile-specific metadata assertions after retrieval.
- [ ] Explicit `memory_retrieve` call in the E2E profile flow.
- [ ] Real-client smoke test harness for launching agent CLIs through Ollama.
- [ ] Real-client smoke tests for clients with documented or discoverable headless modes.
- [ ] Research-backed path to remove Ollama from the real-client smoke chain.

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
| Claude Code | Planned | Planned | Add a Claude profile with `source: claude`, model metadata, and either `messages` or export-shaped normalized payload. Claude Code supports Anthropic API endpoint override through environment variables. |

## Implementation Plan

### Phase 1: Complete Profile Matrix

Status: partial.

- [x] Add Codex profile.
- [x] Add Gemini profile.
- [x] Add VS Code Copilot profile.
- [x] Add opencode profile.
- [ ] Add Claude profile.
- [ ] Add profile-level assertions for source, tags, and metadata preservation.

### Phase 2: Round-Trip Hardening

Status: planned.

- [ ] Add `memory_retrieve` after insert and assert retrieved memory ID/source/messages match the profile.
- [ ] Assert `memory_search` and `memory_ask` do not leak internal hash fields.
- [ ] Assert all profiles work when the input omits `id`.
- [ ] Assert each profile returns the stable MCP envelope fields.

### Phase 3: Negative Contract Cases

Status: planned.

- [ ] Add malformed message shape cases for each broad payload style.
- [ ] Add invalid role and empty message cases.
- [ ] Add invalid explicit ID case.
- [ ] Assert machine-readable `invalid_input` envelopes.

### Phase 4: CI Stability

Status: partial.

- [x] Run E2E tests in CI with Ollama installed.
- [x] Pull required embedding model.
- [x] Pull lightweight chat model for smoke completion.
- [ ] Consider splitting MCP profile smoke tests from slower live-chat smoke tests if CI runtime becomes noisy.
- [ ] Add a documented local command for running only profile smoke tests.

### Phase 5: Real Client Smoke Harness

Status: planned.

Real-client smoke tests should prove that actual agent CLIs can connect to the ai-memory-hub MCP server and complete a minimal memory workflow.

Harness requirements:

- [ ] Start a local deterministic test LLM gateway.
- [ ] Start ai-memory-hub with MCP enabled on a known local endpoint.
- [ ] Provide each real client an MCP server config pointing at ai-memory-hub.
- [ ] Run a deterministic prompt that instructs the client to call `memory_validate`, `memory_insert`, `memory_search`, and `memory_ask`.
- [ ] Verify success by querying ai-memory-hub directly after the client exits.
- [ ] Capture client stdout/stderr and server logs as CI artifacts when a real-client smoke test fails.

Per-client work:

- [ ] Claude Code: set `ANTHROPIC_BASE_URL`, auth/model env vars, and pass a non-interactive prompt.
- [ ] Copilot CLI: set `COPILOT_PROVIDER_BASE_URL`, provider type, API key, and model env vars, then pass a non-interactive prompt.
- [ ] Codex CLI: write a temporary `CODEX_HOME` config with custom `model_providers.<name>.base_url`, model, and MCP server URL, then run non-interactive exec mode.
- [ ] opencode: write a temporary opencode config with a custom OpenAI-compatible provider `options.baseURL`, model, and MCP server config, then run non-interactive mode if available.
- [ ] Gemini CLI: identify whether the current CLI can use a local model gateway directly; if not, keep Gemini real-client smoke gated behind a real Gemini API key or skip it from the no-Ollama lane.

### Phase 5.1: Remove Ollama From The Real-Client Chain

Status: planned.

Ollama is not what this project needs to test. It is useful as a local model server, but real-client MCP smoke tests should isolate ai-memory-hub and the client integration. The preferred replacement is a deterministic local test gateway.

Recommended gateway design:

- [ ] Implement a tiny local OpenAI-compatible endpoint for clients that use OpenAI-compatible APIs.
- [ ] Implement a tiny local Anthropic-compatible endpoint for Claude Code.
- [ ] Return deterministic assistant messages that encourage tool use or satisfy the client after tool calls.
- [ ] Log every model request so tests can assert the real client contacted the local gateway.
- [ ] Keep embeddings for ai-memory-hub separate from client model completions; use the existing local deterministic embedding provider where possible.
- [ ] Add timeout/failure diagnostics for cases where a client refuses the fake gateway because of missing streaming/tool-call support.

Minimum endpoint surface to investigate:

- [ ] OpenAI-compatible `/v1/chat/completions` with streaming and tool/function-call support.
- [ ] OpenAI Responses API if required by Codex or Copilot in the selected mode.
- [ ] Anthropic-compatible `/v1/messages` with streaming and tool-use blocks for Claude Code.
- [ ] `/v1/models` or model-discovery endpoints where clients require them.

Feasibility by client:

- Claude Code: feasible without Ollama if the gateway implements enough Anthropic-compatible `/v1/messages`; Claude Code supports `ANTHROPIC_BASE_URL`.
- Copilot CLI: feasible without Ollama if the gateway implements an OpenAI-compatible endpoint with streaming and tool calling; Copilot CLI supports custom provider env vars.
- Codex CLI: feasible without Ollama through custom model provider config, but likely needs Responses API support depending on `wire_api`.
- opencode: feasible without Ollama through custom provider config using `@ai-sdk/openai-compatible`.
- Gemini CLI: uncertain for no-Ollama/no-Google mode; MCP config is documented, but a direct local model-provider override needs separate validation.

### Phase 6: CI Gating Policy

Status: planned.

- [ ] Keep contract profile smoke tests in default CI.
- [ ] Put real-client smoke tests in a separate job or workflow because they install external CLIs and are more failure-prone.
- [ ] Run real-client smoke tests on schedule, manual dispatch, and optionally before release.
- [ ] Promote a real-client test into default PR CI only after it is stable and does not require vendor credentials.
- [ ] Skip unavailable real clients with clear diagnostics rather than failing unrelated PRs during early rollout.

## Design Notes

- Keep CI focused on protocol compatibility and payload normalization.
- Do not require vendor credentials for the default pipeline.
- Prefer a deterministic local test gateway over Ollama for real-client MCP smoke tests.
- Treat real-client tests as higher-fidelity smoke checks with their own CI lane because client CLIs and auth flows change outside this project.
- Keep the MCP server as the source of truth for validation, ID generation, hashing, embedding, dedupe, and storage.

## External Integration References

- Ollama Claude Code integration documents `ollama launch claude` and a non-interactive `--yes -- -p` mode.
- Ollama Copilot CLI integration documents `ollama launch copilot` and a non-interactive `--yes -- -p` mode.
- Ollama Codex CLI integration documents `ollama launch codex`, `--config`, `--restore`, and profile-based setup.
- Ollama OpenCode integration documents `ollama launch opencode` and `--config`.
