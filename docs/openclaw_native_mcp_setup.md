# OpenClaw Native MCP Setup

Status: documented setup, not yet manually validated.

This runbook tests OpenClaw through its native CLI and MCP registry. Do not use
`ollama launch` for this validation path.

## Goal

Verify that a real OpenClaw agent can use ai-memory-hub as a Streamable HTTP MCP
server:

1. Register ai-memory-hub in OpenClaw's native MCP registry.
2. Probe the MCP server from OpenClaw.
3. Ask OpenClaw to save a short memory through ai-memory-hub tools.
4. Verify the saved memory directly through ai-memory-hub.
5. Record issues before marking OpenClaw supported.

## Current Research Notes

- OpenClaw installs as a Node-based CLI. Its README recommends Node 24 or
  Node 22.19+, `npm install -g openclaw@latest`, and
  `openclaw onboard --install-daemon`.
- OpenClaw's native CLI has an `openclaw mcp` command family. `openclaw mcp
  serve` makes OpenClaw act as an MCP server. The other `openclaw mcp`
  subcommands manage OpenClaw-owned outbound MCP server definitions.
- OpenClaw docs say saved MCP definitions live under `mcp.servers`, support
  `transport: "streamable-http"` for HTTP MCP servers, and can be probed with
  `openclaw mcp probe` or `openclaw mcp doctor --probe`.
- OpenClaw exposes configured MCP servers as plugin-owned tools through the
  `bundle-mcp` plugin. Sandbox/tool policy can hide those tools unless
  `bundle-mcp`, `group:plugins`, or specific server tool globs are allowed.

## Prerequisites

- ai-memory-hub running with MCP enabled at `http://127.0.0.1:8000/mcp/`.
- OpenClaw installed and onboarded.
- A working OpenClaw model/provider configuration.
- For first validation, keep ai-memory-hub bound to localhost and use
  unauthenticated local mode. Add bearer-token validation after the basic path
  works.

Install OpenClaw natively:

```bash
npm install -g openclaw@latest
openclaw onboard --install-daemon
openclaw gateway status
```

Start ai-memory-hub from this repo in whichever local mode you are validating.
For example, use the checked-in PGVector stack:

```bash
cd examples/storage_providers/postgres-pgvector
docker compose up --build
```

Confirm ai-memory-hub readiness:

```bash
curl -fsS http://127.0.0.1:8000/ready
```

## Register ai-memory-hub In OpenClaw

Add ai-memory-hub as a Streamable HTTP MCP server:

```bash
openclaw mcp add ai-memory-hub \
  --url http://127.0.0.1:8000/mcp/ \
  --transport streamable-http \
  --timeout 20 \
  --connect-timeout 5 \
  --include 'memory_validate,memory_insert,memory_search,memory_retrieve,memory_ask,memory_fact_search,memory_profile_get'
```

Inspect the saved definition:

```bash
openclaw mcp show ai-memory-hub --json
openclaw mcp status ai-memory-hub --verbose
```

Probe the live server:

```bash
openclaw mcp probe ai-memory-hub --json
openclaw mcp doctor ai-memory-hub --probe
```

Expected probe result:

- OpenClaw connects to `http://127.0.0.1:8000/mcp/`.
- The tool list includes ai-memory-hub memory tools.
- No auth, transport, or tool-filter diagnostics block usage.

## Tool Policy Check

If OpenClaw can probe the server but the agent cannot see or call the tools,
check tool policy and sandbox gates.

For a local first pass, use a tool profile that can expose plugin tools:

```bash
openclaw config get tools.profile
```

If sandboxing is enabled for the session, ensure MCP/plugin tools are allowed in
the sandbox tool policy. OpenClaw docs describe `bundle-mcp`, `group:plugins`,
and server-specific globs as the relevant allowlist entries for configured MCP
servers.

After changing OpenClaw config, run:

```bash
openclaw doctor
openclaw mcp doctor ai-memory-hub --probe
```

## Manual Validation Prompt

Run OpenClaw natively:

```bash
openclaw agent --message "Use the configured ai-memory-hub MCP server. First call memory_validate, then memory_insert, then memory_retrieve. Save a short conversation with source openclaw, title OpenClaw Native MCP Test, and one user message: The OpenClaw native MCP validation phrase is coral-index. After saving, report the memory ID."
```

Expected behavior:

- OpenClaw discovers the configured ai-memory-hub MCP tools.
- OpenClaw validates the conversation before insert.
- OpenClaw inserts one memory with `source: "openclaw"`.
- OpenClaw retrieves the returned memory ID.
- OpenClaw reports the memory ID in the final answer.

If OpenClaw uses server-prefixed tool names, the tools may appear with an
`ai-memory-hub__` prefix. That is acceptable as long as the underlying
ai-memory-hub MCP tools are called.

## Verify Outside OpenClaw

Use ai-memory-hub directly to confirm persistence:

```bash
curl -fsS http://127.0.0.1:8000/memory/search \
  -H 'Content-Type: application/json' \
  -d '{"query":"coral-index","source":"openclaw","top_k":5}'
```

Ask over the saved memory:

```bash
curl -fsS http://127.0.0.1:8000/memory/ask \
  -H 'Content-Type: application/json' \
  -d '{"question":"What is the OpenClaw native MCP validation phrase?","source":"openclaw","top_k":5}'
```

Expected direct verification:

- Search returns at least one result with `conversation.source` set to
  `openclaw`.
- Ask returns an answer containing `coral-index`.
- Citations or provenance point back to the OpenClaw-inserted memory.

## Bearer Auth Follow-Up

After unauthenticated localhost validation works, repeat with ai-memory-hub API
key auth enabled.

Open questions to verify before documenting exact auth syntax:

- Whether OpenClaw stores static HTTP headers literally in `mcp.servers`.
- Whether OpenClaw supports secret references for MCP HTTP headers.
- Whether `openclaw mcp login` is useful only for OAuth servers, or can help
  with ai-memory-hub's bearer/API-key mode.

Until this is verified, do not commit bearer tokens into OpenClaw config. Prefer
local unauthenticated validation bound to `127.0.0.1`.

## Supported Status Checklist

Do not mark OpenClaw supported until these are complete:

- [x] Native setup documented.
- [ ] `openclaw mcp probe ai-memory-hub --json` succeeds.
- [ ] OpenClaw agent can call `memory_validate`.
- [ ] OpenClaw agent can call `memory_insert`.
- [ ] OpenClaw agent can call `memory_retrieve`.
- [ ] Direct ai-memory-hub search finds the OpenClaw-inserted memory.
- [ ] Direct ai-memory-hub ask answers from the OpenClaw-inserted memory.
- [ ] Any ai-memory-hub issues discovered during manual validation are fixed.
- [ ] Bearer-token behavior is tested or explicitly documented as unsupported.
- [ ] A stable headless/scriptable OpenClaw command is confirmed before adding
  automated real-client coverage.

## Known Risks

- OpenClaw has broad local tool access by design. Keep this validation on a
  local test machine and avoid exposing ai-memory-hub beyond localhost until
  auth is verified.
- OpenClaw tool policy can successfully register an MCP server while still
  hiding its tools from an agent turn. Check `bundle-mcp` and sandbox allowlists
  when tools do not appear.
- The first supported claim should come after real manual validation, not after
  a successful config/probe command alone.

## References

- OpenClaw README: https://github.com/openclaw/openclaw
- OpenClaw MCP CLI docs: https://docs.openclaw.ai/cli/mcp
- OpenClaw tool policy docs: https://docs.openclaw.ai/gateway/config-tools
- ai-memory-hub real-client plan: `real_client_mcp_smoke_plan.md`
