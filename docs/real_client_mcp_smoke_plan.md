# Real-Client MCP Smoke Plan

## Goal

Run a weekly scheduled smoke lane that launches real agent CLIs against
ai-memory-hub MCP and verifies they can complete a minimal memory workflow.

This is separate from default contract smoke tests. Contract tests prove MCP
protocol and payload compatibility quickly in PR CI. Real-client tests prove
actual client binaries can still connect, call tools, and preserve the expected
memory behavior as those clients change over time.

## Priority

P0.

MCP is the primary agent integration path. Real-client breakage should be found
regularly, but these tests should not block ordinary PRs until they are stable,
credential-free, and cheap to run.

## CI Policy

- [ ] Add a separate workflow for real-client MCP smoke tests.
- [ ] Run it weekly on a scheduled trigger.
- [ ] Allow manual `workflow_dispatch` runs for debugging and release checks.
- [ ] Keep it out of default PR gating at first.
- [ ] Capture ai-memory-hub logs, client stdout, client stderr, and gateway logs
  as artifacts on failure.
- [ ] Skip unavailable clients with explicit diagnostics during rollout.
- [ ] Promote individual client jobs to stricter gating only after they are
  stable and do not require vendor credentials.

Suggested schedule:

```yaml
on:
  schedule:
    - cron: "17 4 * * 1"
  workflow_dispatch:
```

## Harness Requirements

- [ ] Start ai-memory-hub with MCP enabled on a known local endpoint.
- [ ] Start a deterministic local test LLM gateway.
- [ ] Keep ai-memory-hub embeddings separate from client model completions.
- [ ] Provide each real client an MCP server config pointing at ai-memory-hub.
- [ ] Provide each real client a model-provider config pointing at the local
  gateway where supported.
- [ ] Run a deterministic prompt that asks the client to call
  `memory_validate`, `memory_insert`, `memory_search`, `memory_retrieve`, and
  `memory_ask`.
- [ ] Verify success by querying ai-memory-hub directly after the client exits.
- [ ] Enforce per-client timeouts with actionable failure messages.
- [ ] Redact secrets from logs and artifacts.

## Deterministic Gateway

Preferred direction: replace Ollama in the real-client chain with a tiny local
test gateway. Ollama is useful for local model serving, but it is not the system
under test.

Gateway requirements:

- [ ] Implement OpenAI-compatible `/v1/chat/completions` for clients that can
  use OpenAI-compatible providers.
- [ ] Implement OpenAI Responses API if required by Codex or Copilot in the
  selected mode.
- [ ] Implement Anthropic-compatible `/v1/messages` for Claude Code.
- [ ] Implement `/v1/models` or equivalent model discovery where clients require
  it.
- [ ] Support the minimum streaming/tool-call shape needed by each client.
- [ ] Return deterministic assistant messages that encourage tool use or satisfy
  the client after tool calls.
- [ ] Log every model request so tests can assert the real client contacted the
  gateway.

## Client Order

Start with clients that expose clear base-URL or provider overrides and a
non-interactive mode.

- [ ] Claude Code: set `ANTHROPIC_BASE_URL`, auth/model env vars, and run a
  non-interactive prompt.
- [ ] Copilot CLI: set `COPILOT_PROVIDER_BASE_URL`,
  `COPILOT_PROVIDER_TYPE`, `COPILOT_PROVIDER_API_KEY`, and `COPILOT_MODEL`,
  then run a non-interactive prompt.
- [ ] Codex CLI: write a temporary `CODEX_HOME` config with a custom
  OpenAI-compatible model provider, model, and MCP server URL, then run
  non-interactive exec mode.
- [ ] opencode: write a temporary opencode config with a custom provider
  `baseURL`, model, and MCP server config, then validate the reliable
  non-interactive command.
- [ ] Gemini CLI: identify whether the current CLI can use a local model gateway
  directly. If not, keep Gemini gated behind explicit credentials or skip it
  from the no-vendor-credential lane.

## Minimal Smoke Prompt

Each client should receive a prompt equivalent to:

```text
Use the ai-memory-hub MCP server. Validate and insert a short conversation about
the weekly real-client smoke test. Then search for it, retrieve it by ID, and
ask what the conversation was about. Report the inserted ID.
```

The harness should verify the result through ai-memory-hub directly, not only by
trusting client stdout.

## Acceptance Criteria

- [ ] Weekly scheduled workflow exists and can be manually dispatched.
- [ ] ai-memory-hub starts with MCP enabled in the workflow.
- [ ] Local deterministic gateway starts in the workflow.
- [ ] At least Claude Code runs end-to-end or skips with a clear unavailable
  diagnostic.
- [ ] At least Copilot CLI runs end-to-end or skips with a clear unavailable
  diagnostic.
- [ ] Successful client runs produce a memory that direct ai-memory-hub queries
  can search, retrieve, and ask over.
- [ ] Failure artifacts include enough logs to identify whether the failure is
  ai-memory-hub, the gateway, client install/config, or client behavior.

## Done When

- [ ] Claude Code and Copilot CLI run weekly through the scheduled lane.
- [ ] Codex CLI, opencode, and Gemini have documented status: implemented,
  skipped with reason, or blocked by missing reliable headless/local-provider
  support.
- [ ] The plan documents the command/config used for every implemented client.
- [ ] Default PR CI remains focused on deterministic contract tests.
