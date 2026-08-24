# Agent Model Footprint Plan

Source date: `24-08-2026`

Status: planned

Priority: P0 before `v0.1.0`

## Goal

Make ai-memory-hub preserve the exact agent and model footprint for every saved
conversation, including mixed-model and subagent turns.

This is core product behavior for an AI memory hub. Future agents should be able
to tell whether a memory came from Hermes, Codex, Gemini CLI, opencode, or
another runtime, and which model produced the content, without guessing from raw
text.

## Source Feedback

Gemini could recover the broad conversation context around Hermes' critique, but
could not reliably answer who put the message in the hub or which model/runtime
produced it. The current schema has conversation-level `source`,
`metadata.agent`, and `metadata.model`, but message objects only allow `role`,
`text`, and `hash`. That blocks precise attribution for subagents and
mixed-model conversations.

The expected saved shape should support cases like:

```json
{
  "source": "hermes",
  "metadata": {
    "agent": "hermes",
    "model": "gemini-3.6-flash",
    "platform": "cli",
    "save_intent_source": "hermes"
  },
  "messages": [
    {
      "role": "assistant",
      "agent": "hermes",
      "model": "gemini-3.6-flash",
      "text": "MCP tools should be designed for an LLM's context..."
    }
  ]
}
```

## Identity Model

Use separate fields for separate provenance questions:

- `source`: canonical runtime/client that captured or owns the saved
  conversation, such as `hermes`, `codex`, `gemini-cli`, `opencode`,
  `claude-code`, `chatgpt`, or `manual`.
- `metadata.agent`: default agent runtime for the conversation.
- `metadata.model`: default model for assistant turns in the conversation.
- `metadata.model_provider`: optional default provider, such as `google`,
  `openai`, `anthropic`, `local`, or `unknown`.
- `metadata.platform`: user-facing surface, such as `cli`, `desktop`,
  `vscode`, `web`, or `api`.
- `metadata.capture_client`: client process or adapter that sent the payload to
  the hub when it differs from `source`.
- `metadata.capture_client_version`: optional client version.
- `metadata.ingestion_method`: transport or importer path, such as `mcp`,
  `api`, `cli`, `json-import`, or `browser-extension`.
- `messages[].agent`: per-message agent or subagent override.
- `messages[].model`: per-message model override.
- `messages[].model_provider`: per-message provider override.
- `messages[].source_message_id`: optional upstream message id when available.

Keep `owner_id`, `project_id`, and token identity as server-side authorization
and audit fields. They answer "which hub principal saved this," not "which agent
or model produced this."

## Schema Changes

Extend `messages[]` with optional attribution fields:

- `agent`
- `model`
- `model_provider`
- `source_message_id`

Keep `role` as the semantic chat role: `user` or `assistant`. Do not replace it
with runtime names.

Continue computing the existing `messages[].hash` from `role` and `text` only,
so old dedupe behavior remains stable. If attribution-sensitive dedupe is needed
later, add a separate `attribution_hash` instead of changing the existing hash
contract.

## Ingestion Behavior

- Normalize top-level `source` into a canonical agent/runtime value when the
  client supplies one.
- Preserve conversation-level `metadata.agent`, `metadata.model`,
  `metadata.model_provider`, `metadata.platform`, `metadata.capture_client`,
  `metadata.capture_client_version`, and `metadata.ingestion_method`.
- Fill missing assistant-message attribution from conversation defaults during
  normalization:
  - `messages[].agent` from `metadata.agent` or `source`
  - `messages[].model` from `metadata.model`
  - `messages[].model_provider` from `metadata.model_provider`
- Allow explicit per-message overrides for subagents and mixed-model turns.
- Validate attribution fields as bounded non-empty strings when present.
- Keep raw message text untouched.

## Fact And Summary Provenance

Facts, generated summaries, and answer evidence should carry enough footprint to
explain where the memory came from:

- Include source conversation id and source message indexes as today.
- Add source `agent`, `model`, and `model_provider` from the source message when
  available, falling back to conversation defaults.
- Include these fields in detailed fact/search/ask provenance.
- Include a compact version in concise output when it helps distinguish agent
  memory quality.

This lets a future answer say, for example, "This came from Hermes using
gemini-3.6-flash" instead of only "This came from an assistant message."

## MCP And Agent Guidance

Update MCP instructions and tool descriptions so agents save conversations with
agent/model footprint:

- Do not use generic `source="mcp"` when a specific runtime is known.
- Use `source="hermes"` for Hermes saves, `source="codex"` for Codex saves, and
  equivalent canonical client names for other runtimes.
- Include `metadata.agent`, `metadata.model`, and
  `metadata.save_intent_source`.
- Include per-message `agent` and `model` when a subagent or different model
  produced that turn.

## Non-Goals

- Do not infer a model id from raw text.
- Do not trust client-supplied attribution for authorization.
- Do not expose raw bearer tokens or secrets in provenance.
- Do not change the existing message hash contract.
- Do not require every client to know a model id before it can save memory;
  unknown-but-explicit is better than blocking ingestion.

## Implementation Sequence

- [ ] Add optional message-level attribution fields to the conversation schema.
- [ ] Add bounded string normalization for conversation-level and message-level
      agent/model footprint fields.
- [ ] Fill assistant-message attribution from conversation defaults when
      message-level fields are omitted.
- [ ] Preserve per-message overrides for subagents and mixed-model turns.
- [ ] Add server-side saved-by audit fields or read-only metadata for owner,
      token id, and project where appropriate, without exposing secrets.
- [ ] Attach source agent/model/provider footprint to extracted facts and
      generated summary provenance.
- [ ] Update MCP initialize instructions and tool descriptions with canonical
      `source`, `metadata.agent`, `metadata.model`, and per-message override
      guidance.
- [ ] Update docs and examples for Hermes, Codex, Gemini CLI, opencode, and
      manual imports.
- [ ] Add unit and integration tests for schema validation, normalization,
      fact provenance, search/ask/profile output, and backwards compatibility.

## Acceptance Criteria

- A saved Hermes conversation can be retrieved later with
  `source="hermes"` and `metadata.model="gemini-3.6-flash"`.
- Mixed-model or subagent conversations preserve per-message `agent` and
  `model` values.
- Existing conversations without the new fields still validate, ingest, search,
  and retrieve.
- Facts and generated summaries retain source agent/model footprint when it is
  available.
- Concise MCP reads can show enough attribution for agents to judge source
  quality without parsing full conversation payloads.
- Detailed MCP/API reads expose the full non-secret footprint for audit.

