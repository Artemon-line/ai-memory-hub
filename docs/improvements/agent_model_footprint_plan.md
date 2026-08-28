# Agent Model Footprint Plan

Source date: `24-08-2026`

Status: partially implemented

Priority: P0 before `v0.1.0` for the remaining message-level attribution work.
The current codebase already ships conversation-level source/model metadata,
source filtering, save-intent provenance, client compatibility profiles, and
server-side owner/project audit context.

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

## Current Shipped State

The current implementation covers the conversation-level footprint and several
supporting provenance paths:

- Conversation payloads support top-level `source` and metadata fields such as
  `metadata.agent`, `metadata.model`, `metadata.platform`, and
  `metadata.ingestion_method`.
- Conversation metadata remains open for adapter-specific non-secret fields, so
  clients can already send values such as provider, workspace, session, and
  capture metadata.
- Ingestion normalizes top-level `source`, `content` message fields,
  top-level tags, `saved_at` to `imported_at`, server-generated message hashes,
  and server-generated conversation hashes.
- Existing message hashes still use `role` and `text` only, preserving the
  duplicate-detection contract.
- The API/MCP path stamps server-side `owner_id` and `project_id` into stored
  metadata and records audit events for memory, fact, auth, and project
  operations without exposing secrets.
- Facts and answer evidence already include source conversation IDs, source
  message indexes, source quality, confidence reasons, and
  `save_intent`/`save_intent_source` provenance.
- Concise MCP result formatting keeps compact citations and source-quality
  fields while detailed reads preserve the full non-secret stored payload.
- Client compatibility coverage includes Codex, Gemini CLI, VS Code Copilot,
  Claude Code, and opencode payload shapes.

The core gap remains message-level agent/model attribution. The current
conversation schema still restricts message objects to `role`, `text`, and
server-computed `hash`, so `messages[].agent`, `messages[].model`,
`messages[].model_provider`, and `messages[].source_message_id` are not shipped
yet. Fact and generated-summary provenance therefore cannot yet carry per-message
agent/model/provider footprint.

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
- [x] Preserve conversation-level `source` and existing metadata footprint fields
      such as `metadata.agent`, `metadata.model`, `metadata.platform`, and
      `metadata.ingestion_method`.
- [x] Preserve adapter-specific non-secret metadata fields through ingestion and
      retrieval.
- [x] Keep existing `messages[].hash` and conversation hash behavior stable by
      hashing only role/text-derived message hashes.
- [x] Add server-side saved-by audit fields or read-only metadata for owner,
      token id, and project where appropriate, without exposing secrets.
- [x] Carry source conversation IDs, source message indexes, source quality,
      confidence reasons, and save-intent provenance into facts, citations, ask
      evidence, and profile summaries.
- [ ] Attach source agent/model/provider footprint to extracted facts and
      generated summary provenance once message-level attribution lands.
- [ ] Update MCP initialize instructions and tool descriptions with canonical
      `source`, `metadata.agent`, `metadata.model`, and per-message override
      guidance.
- [x] Update docs and compatibility smoke coverage for Codex, Gemini CLI, VS
      Code Copilot, Claude Code, and opencode payload shapes.
- [ ] Add Hermes-specific and manual-import examples for the full footprint
      model.
- [x] Add tests for source preservation, source filters, content-to-text
      normalization, server-generated hash compatibility, save-intent
      provenance, and client compatibility profiles.
- [ ] Add unit and integration tests for message-level attribution schema
      validation, default filling, per-message overrides, fact provenance,
      search/ask/profile output, and backwards compatibility.

## Acceptance Criteria

- [ ] A saved Hermes conversation can be retrieved later with
  `source="hermes"` and `metadata.model="gemini-3.6-flash"`.
- [ ] Mixed-model or subagent conversations preserve per-message `agent` and
  `model` values.
- [x] Existing conversations without the new fields still validate, ingest, search,
  and retrieve.
- [ ] Facts and generated summaries retain source agent/model footprint when it is
  available.
- [x] Concise MCP reads can show enough source, citation, confidence,
  source-quality, and save-intent information for agents to judge memory quality
  without parsing full conversation payloads.
- [x] Detailed MCP/API reads expose the full currently stored non-secret
  conversation footprint for audit.

