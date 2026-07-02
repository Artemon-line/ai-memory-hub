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
    "platform": "web",
    "ingestion_method": "browser-extension",
    "upstream_thread_id": "platform-thread-id-if-available",
    "model": "optional model name",
    "tags": ["browser-extension"],
    "timezone": "Europe/Dublin",
    "summary": "Optional short factual retrieval hint."
  }
}
```

Recommended `source` values:

- `chatgpt`
- `ms-copilot`
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

## Future API Stabilization

When browser-extension work starts, stabilize this integration without adding a
new hub ingestion path:

- Keep `POST /memory/insert` as the extension-facing contract.
- Add typed insert request models around the current API route while preserving
  the existing JSON schema validation.
- Add extension-shaped contract tests for ChatGPT, Microsoft Copilot, Claude,
  and Gemini payloads.
- Add explicit CORS allowlist configuration for extension origins such as
  `chrome-extension://<id>` and local development origins.
- Document bearer-token setup for extensions using
  `Authorization: Bearer <token>` with `memory:write` scope.
- Document trusted appends for repeated captures of the same thread using
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
