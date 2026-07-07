# Browser Extension Capture Plan

## Purpose

Use separate browser extension repositories to collect AI chat history from web
chat products such as ChatGPT, Microsoft Copilot, Claude, Gemini, and similar
platforms. The extensions parse each platform's page, normalize the visible
conversation into the shared ai-memory-hub conversation schema, and post it to
the existing `POST /memory/insert` API.

This is a suitable optional capture path because the browser has access to the
rendered chat UI when an official export or history API is missing. The extension
captures and normalizes selected content; ai-memory-hub still owns ingestion
after the payload reaches `POST /memory/insert`. Browser capture should not
replace API-first, export-based, or MCP-native capture where those integrations
are available.

## Boundary

ai-memory-hub should not parse raw platform HTML or DOM snapshots in the API
layer. Platform web pages change frequently, and server-side HTML parsing would
couple the memory API to unstable product markup.

The boundary is:

- Browser extension repos own platform-specific DOM parsing.
- Browser extension repos own capture and browser-side context injection.
- ai-memory-hub owns the stable HTTP API contract, validation, auth, dedupe,
  trusted append behavior, ingestion, retrieval, permissions, memory quality,
  indexing, and storage.
- Extensions send normalized conversation JSON only.
- Raw HTML, DOM snapshots, screenshots, and selector evidence stay out of
  ai-memory-hub storage by default.

## Implemented Hub Contract

The hub-side contract is implemented in ai-memory-hub:

- Browser extension implementations stay in separate repositories.
- Extensions post normalized conversation JSON to `POST /memory/insert`.
- `api.cors_allow_origins` explicitly allows extension origins such as
  `chrome-extension://<id>` and local development origins such as
  `http://127.0.0.1:5173`.
- `POST /memory/insert` uses a typed insert request model and still validates the
  final payload through the shared conversation schema.
- Extension-shaped API contract tests cover ChatGPT, Microsoft Copilot, Claude,
  and Gemini source payloads.
- Raw browser artifacts are rejected by key when posted as structured fields:
  `raw_html`, `html_snapshot`, `dom_snapshot`, `screenshot`,
  `screenshot_data`, and `selector_evidence`.
- Repeated captures can append to an existing stored thread when
  `storage.allow_trusted_appends: true` and the payload keeps the same `source`
  plus `metadata.upstream_thread_id`.

## Expected Payload

Extensions should post one complete source thread to `POST /memory/insert`:

```json
{
  "source": "chatgpt",
  "title": "Optional chat title",
  "messages": [
    {"role": "user", "text": "What did we decide about the memory API?"},
    {"role": "assistant", "text": "Use normalized JSON and the existing insert endpoint."}
  ],
  "metadata": {
    "platform": "chatgpt",
    "capture_client": "browser_extension",
    "capture_client_version": "0.1.0",
    "upstream_thread_id": "platform-thread-id-if-available",
    "save_intent": "user_confirmed",
    "save_intent_source": "browser_extension",
    "model": "optional model name",
    "tags": ["browser-extension"],
    "timezone": "Europe/Dublin",
    "summary": "Optional short factual retrieval hint."
  }
}
```

Recommended `source` values:

- `chatgpt`
- `microsoft-copilot`
- `claude`
- `gemini`

Extensions should omit server-owned fields unless they have a specific
compatibility reason to include them:

- `id`
- `messages[].hash`
- `metadata.conversation_hash`
- `metadata.imported_at`
- `metadata.updated_at`

The server normalizes and validates the final payload before storage.

Use the same shape for each platform. The extension owns the platform-specific
DOM parsing and maps visible user/assistant turns into `messages`; the hub does
not receive selector logs, screenshots, or raw HTML.

## CORS And Auth

Configure browser-extension origins explicitly:

```yaml
api:
  auth: bearer_token
  cors_allow_origins:
    - chrome-extension://abcdefghijklmnopabcdefghijklmnop
    - http://127.0.0.1:5173
```

Extensions should send a bearer token with `memory:write` scope:

```bash
curl -X POST http://127.0.0.1:8000/memory/insert \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d @normalized-chat.json
```

Use `memory:write` for capture-only extensions; add `memory:read` only when the
extension also searches or retrieves memory.

## Trusted Repeated Captures

When a user captures the same visible web thread multiple times, use:

- the same `source`
- the same `metadata.upstream_thread_id`
- the full visible message list, or an append-only continuation that preserves
  prior messages

With `storage.allow_trusted_appends: true`, the hub can append newly observed
messages to the existing conversation instead of creating duplicate memories.

## Future API Stabilization

When browser-extension work starts, stabilize this integration without adding a
new hub ingestion path:

- [x] Keep `POST /memory/insert` as the extension-facing contract.
- [x] Add typed insert request models around the current API route while preserving
  the existing JSON schema validation.
- [x] Add extension-shaped contract tests for ChatGPT, Microsoft Copilot, Claude,
  and Gemini payloads.
- [x] Add explicit CORS allowlist configuration for extension origins such as
  `chrome-extension://<id>` and local development origins.
- [x] Document bearer-token setup for extensions using
  `Authorization: Bearer <token>` with `memory:write` scope.
- [x] Document trusted appends for repeated captures of the same thread using
  `source` plus `metadata.upstream_thread_id`.

## Notes For Extension Repos

Extension implementations should be conservative:

- Capture only user and assistant messages.
- Avoid sending tool logs, hidden prompts, debug state, or unrelated page text.
- Debounce sync while an assistant response is streaming.
- Re-post the whole visible thread or append-only delta according to the API
  contract available at the time.
- Treat conversations as sensitive data and require explicit user opt-in before
  sending them to ai-memory-hub.
