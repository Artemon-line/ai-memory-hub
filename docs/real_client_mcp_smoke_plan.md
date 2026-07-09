# Real-Client MCP Smoke Plan

## Goal

Run a CI smoke lane that launches real agent CLIs against ai-memory-hub MCP and
verifies they can complete a minimal memory workflow.

This is separate from default contract smoke tests. Contract tests prove MCP
protocol and payload compatibility quickly in PR CI. Real-client tests prove
actual client binaries can still connect, call tools, and preserve the expected
memory behavior as those clients change over time.

## Priority

P0.

MCP is the primary agent integration path. Real-client breakage should be found
regularly. The workflow now runs on pull requests and pushes, while individual
client slots remain skip-safe when a command template or executable is not
available.

## CI Policy

- [x] Add a separate workflow for real-client MCP smoke tests.
- [x] Run it weekly on a scheduled trigger.
- [x] Run it on pull requests and pushes to `main`.
- [x] Allow manual `workflow_dispatch` runs for debugging and release checks.
- [x] Promote the workflow into default PR gating.
- [x] Capture ai-memory-hub logs, client stdout, client stderr, and gateway logs
  as artifacts on failure.
- [x] Skip unavailable clients with explicit diagnostics during rollout.
- [ ] Promote individual client slots from skip-safe to strict only after they
  are stable and do not require vendor credentials.

Suggested schedule:

```yaml
on:
  pull_request:
  push:
    branches: [main]
  schedule:
    - cron: "17 4 * * 1"
  workflow_dispatch:
```

## Harness Requirements

- [x] Start ai-memory-hub with MCP enabled on a known local endpoint.
- [x] Start a deterministic local test LLM gateway.
- [x] Keep ai-memory-hub embeddings separate from client model completions.
- [x] Provide each real client an MCP server config pointing at ai-memory-hub.
- [x] Provide each real client a model-provider config pointing at the local
  gateway where supported.
- [x] Run a deterministic prompt that asks the client to call
  `memory_validate`, `memory_insert`, `memory_search`, `memory_retrieve`, and
  `memory_ask`.
- [x] Verify success by querying ai-memory-hub directly after the client exits.
- [x] Enforce per-client timeouts with actionable failure messages.
- [x] Redact secrets from logs and artifacts.

## Deterministic Gateway

Preferred direction: replace Ollama in the real-client chain with a tiny local
test gateway. Ollama is useful for local model serving, but it is not the system
under test.

Gateway requirements:

- [x] Implement OpenAI-compatible `/v1/chat/completions` for clients that can
  use OpenAI-compatible providers.
- [x] Implement OpenAI Responses API if required by Codex or Copilot in the
  selected mode.
- [x] Implement Anthropic-compatible `/v1/messages` for Claude Code.
- [x] Implement `/v1/models` or equivalent model discovery where clients require
  it.
- [x] Support the minimum non-streaming tool-call shape needed by OpenAI-compatible and Anthropic-compatible clients.
- [x] Return deterministic assistant messages that encourage tool use or satisfy
  the client after tool calls.
- [x] Log every model request so tests can assert the real client contacted the
  gateway.

## Client Order

Start with clients that expose clear base-URL or provider overrides and a
non-interactive mode.

- [x] Claude Code: set `ANTHROPIC_BASE_URL`, auth/model env vars, and run a
  non-interactive prompt.
- [x] Copilot CLI: set `COPILOT_PROVIDER_BASE_URL`,
  `COPILOT_PROVIDER_TYPE`, `COPILOT_PROVIDER_API_KEY`, and `COPILOT_MODEL`,
  then run a non-interactive prompt.
- [x] Codex CLI: write a temporary `CODEX_HOME` config with a custom
  OpenAI-compatible model provider, model, and MCP server URL. Keep the slot
  skipped until a reliable non-interactive command template is configured.
- [x] opencode: write a temporary opencode config with a custom provider
  `baseURL`, model, and MCP server config. Keep the slot skipped until a
  reliable non-interactive command template is configured.
- [x] Gemini CLI: track the client in the weekly lane and skip it explicitly
  until local-gateway, no-vendor-credential support is confirmed.

## Native Agent Candidate Triage

Ollama's launcher list is useful as a discovery source for popular agents, but
ai-memory-hub should test those agents through their native install, config, and
run paths. Do not use `ollama launch` as the compatibility path for this smoke
lane.

Promote agents one by one after verifying that they can use ai-memory-hub
through MCP or an equivalent native tool bridge. The primary deliverable is a
well-documented native setup that a user can run manually. Mark an agent
supported only after manual validation has exposed and resolved the integration
issues.

Promotion path:

- [ ] Research native MCP/tool support, install path, config files, auth/header
  behavior, and model/provider requirements.
- [ ] Add a documented native setup example for the agent, including
  ai-memory-hub MCP URL, auth notes, and a minimal save/search/ask prompt.
- [ ] Manually validate the documented setup against a real ai-memory-hub
  instance.
- [ ] Fix ai-memory-hub issues found during manual validation before marking the
  agent supported.
- [ ] Mark the agent supported only after the manual workflow works end to end.
- [ ] Add automated native real-client coverage on top when the agent has a
  stable non-interactive or scriptable command.
- [ ] Add mocked/profile coverage only for payload or response-shape quirks
  discovered during real testing.
- [ ] Keep any automated CI slot skip-safe until the native command is stable
  across releases.

Candidate order:

| Agent | Native support status | Manual validation | Automated coverage | Next research question |
|-------|-----------------------|-------------------|--------------------|------------------------|
| Hermes Agent | Candidate | Not started | Not started | Determine whether Hermes supports MCP tools or another native tool protocol that can call ai-memory-hub. |
| NVIDIA OpenShell | Candidate | Not started | Not started | Determine whether OpenShell supports MCP tools or another native tool protocol that can call ai-memory-hub. |
| Claude Code | Tracked | Planned | Weekly slot implemented when configured | Keep validating the native CLI command and MCP config path. |
| Codex CLI | Tracked | Partially validated through prior local use | Weekly slot implemented when configured | Confirm the native CLI command template can use temporary `CODEX_HOME` with MCP and local provider config. |
| Codex App | Candidate | Not started | Not started | Determine whether it exposes MCP config and scriptable delegation suitable for repeatable smoke. |
| Copilot CLI | Tracked | Planned | Weekly slot implemented when configured | Keep validating the native command template and provider override path. |
| OpenCode | Tracked | Partially validated through prior local use | Weekly slot implemented when configured | Confirm the native config path can reliably provide MCP and provider settings. |
| Gemini CLI | Tracked | Not started | Skipped by default pending provider support | Confirm a native no-vendor-credential local-provider path. |
| OpenClaw | Setup documented | Not started | Not started | Run `openclaw mcp probe ai-memory-hub --json`, then manually validate an agent turn using `openclaw_native_mcp_setup.md`. |
| Droid | Candidate | Not started | Not started | Determine whether Droid has headless execution and external MCP/tool configuration. |
| Pi | Candidate | Not started | Not started | Determine whether Pi plugins can call streamable HTTP MCP tools and run non-interactively. |

Do not advertise a candidate as supported until the documented native setup has
been manually validated. Add automated coverage only after manual validation has
made the integration behavior clear.

## Minimal Smoke Prompt

Each client should receive a prompt equivalent to:

```text
Use the ai-memory-hub MCP server. Validate and insert a short conversation about
the real-client smoke test. Then search for it, retrieve it by ID, and
ask what the conversation was about. Report the inserted ID.
```

The harness should verify the result through ai-memory-hub directly, not only by
trusting client stdout.

## Acceptance Criteria

- [x] PR/push/weekly scheduled workflow exists and can be manually dispatched.
- [x] ai-memory-hub starts with MCP enabled in the workflow.
- [x] Local deterministic gateway starts in the workflow.
- [x] At least Claude Code runs end-to-end or skips with a clear unavailable
  diagnostic.
- [x] At least Copilot CLI runs end-to-end or skips with a clear unavailable
  diagnostic.
- [x] Successful client runs produce a memory that direct ai-memory-hub queries
  can search, retrieve, and ask over.
- [x] Failure artifacts include enough logs to identify whether the failure is
  ai-memory-hub, the gateway, client install/config, or client behavior.

## Done When

- [x] Claude Code and Copilot CLI run through the PR/push/scheduled lane when
  their command templates and executables are available.
- [x] Codex CLI, opencode, and Gemini have documented status: implemented,
  skipped with reason, or blocked by missing reliable headless/local-provider
  support.
- [x] The plan documents the command/config used for every implemented client.
- [x] Default PR CI includes the skip-safe real-client smoke harness.

## Implemented Harness

The CI lane is implemented by `.github/workflows/real-client-mcp-smoke.yml` and
`memory.tools.real_client_smoke`.

Default behavior:

- Start ai-memory-hub with local deterministic embeddings and MCP/API enabled.
- Start the deterministic test gateway on localhost.
- Run all tracked client slots.
- Skip a client with a clear diagnostic when its command template is not
  configured or its executable is unavailable.
- Verify successful client runs directly through `/memory/search`,
  `/memory/retrieve`, and `/memory/ask`.
- Upload `summary.json`, ai-memory-hub logs, gateway logs, and per-client
  stdout/stderr artifacts.

Client command templates are configured with environment variables:

- `AMH_REAL_CLIENT_CLAUDE_COMMAND`
- `AMH_REAL_CLIENT_COPILOT_COMMAND`
- `AMH_REAL_CLIENT_CODEX_COMMAND`
- `AMH_REAL_CLIENT_OPENCODE_COMMAND`
- `AMH_REAL_CLIENT_GEMINI_COMMAND`

Templates may use `{prompt_file}`, `{prompt}`, and `{artifact_dir}` placeholders.
The harness also exports `AMH_MCP_URL`, `AMH_SMOKE_PROMPT`, and
`AMH_SMOKE_MARKER` for client commands.

Current client status:

- Claude Code: CI slot implemented; runs when
  `AMH_REAL_CLIENT_CLAUDE_COMMAND` and the `claude` executable are available,
  otherwise skips explicitly.
- Copilot CLI: CI slot implemented; runs when
  `AMH_REAL_CLIENT_COPILOT_COMMAND` and the `copilot` executable are available,
  otherwise skips explicitly.
- Codex CLI: temporary `CODEX_HOME` config generation is implemented; the slot
  remains skipped unless `AMH_REAL_CLIENT_CODEX_COMMAND` is provided.
- opencode: temporary config generation is implemented; the slot remains
  skipped unless `AMH_REAL_CLIENT_OPENCODE_COMMAND` is provided.
- Gemini CLI: tracked and skipped by default pending confirmed local-gateway
  command/provider support.

## Run Locally

Run the skip-safe harness from the repo root:

```bash
uv run python -m memory.tools.real_client_smoke \
  --artifact-dir /tmp/amh-real-client-smoke-local \
  --startup-timeout 30 \
  --client-timeout 30
```

Expected result on a machine without real client CLIs configured:

- ai-memory-hub starts locally with API and MCP enabled.
- The deterministic gateway starts locally.
- Each client reports `status: "skipped"` with a reason such as
  `AMH_REAL_CLIENT_CLAUDE_COMMAND is not set`.
- The overall harness status is `ok`, because unavailable clients are skipped
  during rollout.

Inspect the artifacts:

```bash
cat /tmp/amh-real-client-smoke-local/summary.json
ls -la /tmp/amh-real-client-smoke-local
```

Run only one client slot:

```bash
uv run python -m memory.tools.real_client_smoke \
  --client claude \
  --artifact-dir /tmp/amh-real-client-smoke-claude
```

Enable a client by installing its CLI and setting its command template. The
template may use `{prompt_file}`, `{prompt}`, and `{artifact_dir}` placeholders.
The harness also exports `AMH_MCP_URL`, `AMH_SMOKE_PROMPT`, and
`AMH_SMOKE_MARKER`.

Example shape for Claude Code after installing a `claude` executable:

```bash
export AMH_REAL_CLIENT_CLAUDE_COMMAND='claude --print "{prompt}"'
uv run python -m memory.tools.real_client_smoke \
  --client claude \
  --artifact-dir /tmp/amh-real-client-smoke-claude \
  --client-timeout 120
```

Example shape for a GitHub CLI based Copilot command after installing `gh` and
the Copilot extension:

```bash
export AMH_REAL_CLIENT_COPILOT_COMMAND='gh copilot suggest "{prompt}"'
uv run python -m memory.tools.real_client_smoke \
  --client copilot \
  --artifact-dir /tmp/amh-real-client-smoke-copilot \
  --client-timeout 120
```

The exact client command is intentionally externalized because real client CLIs
change their non-interactive syntax. A configured run passes only when the client
exits successfully and the harness can directly verify the inserted memory by
calling ai-memory-hub.

Force a configured client to be required:

```bash
uv run python -m memory.tools.real_client_smoke \
  --client claude \
  --require-configured \
  --require-success-for claude \
  --artifact-dir /tmp/amh-real-client-smoke-required
```

Use this mode once the local command template is known-good. It fails if the
command template is missing, the executable is unavailable, the client exits
non-zero, or direct ai-memory-hub verification fails.

Use already-running services when debugging:

```bash
uv run python -m memory.tools.real_client_smoke \
  --hub-url http://127.0.0.1:8000 \
  --gateway-url http://127.0.0.1:9000 \
  --client claude \
  --artifact-dir /tmp/amh-real-client-smoke-existing-services
```

When `--hub-url` or `--gateway-url` is provided, the harness does not start that
service and only polls it before running clients.
