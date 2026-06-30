# Explicit Memory Save Intent Plan

## Goal

Prevent MCP clients from silently persisting personal facts or conversations unless the user explicitly asked to save them, confirmed the save, or intentionally enabled client auto-save.

This comes from real opencode MCP use: the user said they had a yellow Squier Tele with a black pickguard and Nocaster pickups. opencode proactively called `memory_insert` even though the user had not asked it to save the fact. ai-memory-hub did not scrape the chat; it accepted the tool call the client sent. The server needs a policy option for users who want stronger consent boundaries.

## Problem

`memory_insert` currently trusts the calling client. That is simple and compatible, but it means an agent can decide that ordinary conversation text is memory-worthy and persist it without a clear user action.

Risks:

- Personal facts can be saved when the user expected transient chat only.
- Different clients may have different implicit memory behavior.
- The server cannot distinguish explicit user intent from client auto-save.
- Users cannot enforce a consent policy centrally across Codex, opencode, Claude, Copilot, or other MCP clients.

## Current Behavior

- `memory_insert` accepts API and MCP inserts that pass validation.
- The server stamps ownership and project scope, but it does not require a save-intent marker.
- Fact extraction may then turn the inserted conversation into durable facts.
- This is backward compatible but permissive.

## Proposed Config

Add an insert policy setting:

```yaml
memory:
  insert_policy: permissive
```

Allowed values:

- `permissive`: current behavior. No save-intent marker required.
- `require_save_intent`: reject inserts unless the client sends an accepted save-intent marker.
- `review_pending`: store inserts without an accepted save-intent marker as pending review and exclude them from default reads until approval.

Implemented values are `permissive`, `require_save_intent`, and `review_pending`.

Default: `permissive`, to avoid breaking existing clients. Recommended mode for personal memory deployments: `require_save_intent`.

## Save-Intent Contract

Clients that intentionally save memory should include:

```json
{
  "metadata": {
    "save_intent": "explicit_user_request"
  }
}
```

Accepted values:

- `explicit_user_request`: user said to save, remember, store, or equivalent.
- `user_confirmed`: client asked for confirmation before saving.
- `client_auto_save`: user enabled an explicit client auto-save mode.

Optional supporting metadata:

```json
{
  "metadata": {
    "save_intent": "user_confirmed",
    "save_intent_source": "opencode",
    "save_intent_evidence": "User confirmed: yes, remember that."
  }
}
```

Do not require clients to send raw prompt history as evidence. Evidence should be short and non-sensitive.

## P0: Server-Side Policy Enforcement

- [x] Add a typed config model for insert policy.
- [x] Validate `metadata.save_intent` on API insert and MCP `memory_insert`.
- [x] Keep `permissive` fully backward compatible.
- [x] In `require_save_intent`, return a deterministic validation error when the marker is missing or unknown.
- [x] Ensure errors do not echo sensitive conversation text.
- [x] In `review_pending`, store the conversation with pending status and exclude it from default reads.

Suggested error shape:

```json
{
  "status": "error",
  "error_code": "save_intent_required",
  "error_message": "memory_insert requires metadata.save_intent when memory.insert_policy is require_save_intent"
}
```

## P1: Pending Review Workflow

- [x] Add pending memory metadata status.
- [x] Add read filters for pending, active, and rejected memories.
- [x] Add API and MCP operations to approve or reject pending inserts.
- [x] Prevent pending inserts from creating active facts until approved.
- [x] Preserve audit metadata showing when the pending memory was received and approved.

## P1: Documentation And Client Guidance

- [x] Document the client contract in MCP setup docs.
- [x] Tell clients not to call `memory_insert` for ordinary user statements unless auto-save is explicitly enabled.
- [ ] Add suggested prompts/config notes for opencode, Codex, Claude, and other clients.
- [x] Document `require_save_intent` as the recommended mode for personal deployments.
- [x] Document `permissive` as a compatibility mode for trusted local workflows.

## P2: Fact-Layer Integration

- [ ] Attach save-intent metadata to extracted facts.
- [ ] Allow fact/profile queries to filter by save-intent source.
- [ ] Include save-intent provenance in `memory_ask` compact provenance when facts are used.
- [ ] Consider lower confidence for facts derived from `client_auto_save` than `explicit_user_request`.

## Testing

- [x] API insert without `metadata.save_intent` succeeds under `permissive`.
- [x] MCP insert without `metadata.save_intent` succeeds under `permissive`.
- [x] API insert without `metadata.save_intent` is rejected under `require_save_intent`.
- [x] MCP insert without `metadata.save_intent` is rejected under `require_save_intent`.
- [x] API and MCP inserts with `explicit_user_request`, `user_confirmed`, or `client_auto_save` succeed under `require_save_intent`.
- [x] Unknown save-intent values are rejected with stable errors.
- [x] `review_pending` stores pending data but excludes it from default search, ask, fact search, profile reads, and default retrieve.
- [x] Pending approval activates search and fact/profile visibility.
- [x] Validation errors do not leak payload text, auth tokens, or provider credentials.

## Done When

- Users can centrally prevent unconfirmed client memory writes.
- Existing permissive local workflows remain compatible.
- MCP and API surfaces enforce the same policy.
- Docs explain how clients should signal explicit memory-saving intent.
- Regression tests cover opencode-style proactive inserts without intent markers.
