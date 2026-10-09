# Technical Overview

This page keeps the technical detail that used to live in the README. The README
is intentionally short; use this page when you need implementation, operations,
or integration details.

## Installation

Clone the repository and install the development environment with `uv`:

```bash
git clone https://github.com/Artemon-line/ai-memory-hub.git
cd ai-memory-hub
uv sync --dev
```

The project requires Python 3.14 or newer.

Optional extras:

```bash
uv sync --dev --extra postgres
uv sync --dev --extra tokenizer
uv sync --dev --all-extras
```

Use provider extras when selecting optional storage SDKs such as Qdrant,
Milvus, Weaviate, MongoDB, Elasticsearch, OpenSearch, Redis, Vespa,
Typesense, Pinecone, Turbopuffer, Postgres, or PGVector. Use the tokenizer extra for exact
OpenAI-compatible token counting through `tiktoken`. Use `--all-extras` for
container builds or provider compatibility checks.

Provider config blocks do not install dependencies. Dependencies are installed
only by the `uv sync` extras you choose, or by container images that explicitly
install extras. The checked-in root `Containerfile` is the quickstart image and
installs only the default SQLite/LanceDB runtime dependencies. The free local
Compose examples for PostgreSQL/PGVector, MongoDB, Redis, and SQLite/LanceDB
use provider-local Containerfiles so they install only the extras needed by
that example.

ChromaDB is temporarily unavailable in `v0.1.0` because the upstream
`chromadb` package has an unresolved critical advisory with no patched release.
The adapter code remains in the repository for future re-enable after upstream
publishes a safe version.

## How It Works

```text
Captured Conversation JSON
  -> Schema Validation
  -> Chunk Messages or Token Windows
  -> Embed Chunks
  -> Store Metadata + Vectors
  -> Search / Retrieve / Ask
```

The hub expects structured conversation JSON. Capture/import adapters convert
supported external formats into that shared schema before using the same hub
ingestion path. Manual speaker-labelled transcripts are supported;
export-specific capture/import adapters are tracked in the roadmap. Browser
extension capture is supported as a separate extension-repo workflow that posts
normalized web chat payloads to the existing insert API; see the
[browser extension capture plan](browser_extension_capture_plan.md).

## API Endpoints

| Method | Path | Purpose |
| --- | --- | --- |
| `POST` | `/memory/insert` | Validate and store a conversation |
| `POST` | `/memory/import` | Parse and store an uploaded external conversation export |
| `POST` | `/memory/search` | Return ranked semantic matches |
| `POST` | `/memory/retrieve` | Retrieve a stored conversation by ID |
| `POST` | `/memory/ask` | Build an answer from retrieved memory or facts |
| `POST` | `/memory/facts/search` | Search normalized facts |
| `POST` | `/memory/profile/get` | Return profile facts and a compact fact-based summary for a subject |
| `POST` | `/memory/facts/supersede` | Mark a fact as superseded |
| `GET` | `/health` | Liveness endpoint with redacted runtime health |
| `GET` | `/ready` | Readiness endpoint for container orchestration |

## Insert Payload

If backend-generated IDs are enabled, omit `id` and let the server assign the
canonical UUID. When the backend requires caller-supplied IDs, include an `id`:

```json
{
  "id": "11111111-2222-4333-8444-555555555555",
  "source": "codex",
  "timestamp": "2026-05-17T00:00:00Z",
  "messages": [
    {"role": "user", "text": "remember this"},
    {"role": "assistant", "text": "stored"}
  ],
  "metadata": {
    "imported_at": "2026-05-17T00:00:00Z",
    "summary": "User asked the assistant to remember a preference.",
    "tags": ["preferences"]
  }
}
```

Messages may use `text` or `content`; `content` is normalized to `text`.
`metadata.summary` is optional. Use it as a short factual retrieval hint only;
raw `messages` and normalized facts remain the source of truth for answers and
citations. The server also writes deterministic generated summaries for each
conversation, its topics, and its project. Generated summaries are persisted
separately from raw messages and facts; conversation summaries are exposed back
under `metadata.generated_summary` for search, retrieve, and CLI display.
The server also writes deterministic `metadata.auto_tags` and
`metadata.tag_sources` from source, topics, entities, and fact predicates.
Manual `metadata.tags` remain authoritative and are not overwritten.
Search and ask `tags` filters match only explicit `metadata.tags`; generated
`auto_tags` support ranking and inspection, but are not filter values.
For continuing source threads, clients may send `metadata.upstream_thread_id`
or `metadata.thread_id`. The server preserves upstream IDs, derives
`metadata.thread_id` when needed, and accepts optional
`metadata.parent_conversation_id` plus `metadata.related_conversation_ids` as
conversation UUID references.
`metadata.save_intent` declares why a client is saving a conversation. Accepted
values are `explicit_user_request`, `user_confirmed`, and `client_auto_save`.
When `memory.insert_policy: require_save_intent` is configured, API and MCP
inserts without an accepted marker are rejected before storage, vector indexing,
or fact extraction. When `memory.insert_policy: review_pending` is configured,
unmarked inserts are stored with `metadata.memory_status: pending_review` and
excluded from default retrieve, search, ask, fact, and profile reads until
approved through `/memory/pending/approve` or the `memory_pending_approve` MCP
tool. `/memory/pending/reject` and `memory_pending_reject` mark pending inserts
as rejected.

Conversation read operations accept `memory_status` to intentionally inspect
review states. The default is `active`. Use `pending_review`, `rejected`, or
`all` with `/memory/search`, `/memory/retrieve`, `/memory/ask`,
`memory_search`, `memory_retrieve`, or `memory_ask` when reviewing held or
rejected inserts.

Store one complete conversation per insert. Do not split one thread into
multiple batch items. If an importer has many independent source conversations,
it should call the existing single insert path once per source conversation while
preserving each original thread/session boundary.

## Search Result Shape

`/memory/search` returns ranked chunk matches in `results`. Each result includes:

- `id`: canonical conversation ID
- `score`: vector distance, where lower is better
- `chunk_index`, `role`, and `text`: the matched chunk
- `conversation`: the stored conversation payload, including
  `metadata.generated_summary` when a server-generated conversation summary is
  available, and `metadata.auto_tags` when deterministic tags were generated
- `conversation_score`: best score for that conversation among retrieved chunks
- `conversation_match_count`: number of retrieved chunks from that conversation

Search applies conservative conversation grouping before trimming to `top_k`, so
closely matched chunks from the same conversation can surface together without
hiding strong unrelated matches.
Use `result_mode=threads` to group matching conversations by `metadata.thread_id`.

To save unfinished work for later, insert it as a normal memory with an RFC 3339
`metadata.handoff_at` timestamp. Search or ask with `handoff_only=true` to limit
retrieval to these handoff-marked memories. Handoffs therefore use the same
storage, indexing, permissions, and retrieval path as every other memory.

MCP `memory_search` adds a `response_format` enum. The default,
`response_format="concise"`, returns each row as matched chunk fields plus a
small `citation` object with conversation ID, source, title, timestamp,
thread ID, and generated summary text when available:

```json
{
  "id": "11111111-2222-4333-8444-555555555555",
  "score": 0.0,
  "chunk_index": 0,
  "role": "user",
  "text": "Remember that I prefer local-first tools.",
  "matching_chunks": 1,
  "citation": {
    "id": "11111111-2222-4333-8444-555555555555",
    "source": "codex",
    "timestamp": "2026-06-08T12:00:00Z",
    "thread_id": "codex:session-42",
    "summary": "User said they prefer local-first tools."
  }
}
```

Use `response_format="detailed"` when an MCP client needs the full
conversation envelope, generated-summary metadata, auto-tag provenance, and
chunk manifests for audit or diagnostics.

MCP `memory_retrieve` also accepts `response_format`. The default
`response_format="concise"` returns a compact `memory` object with public recall
fields, memory status, message count, and a small list of role/text messages.
Use `response_format="detailed"` for the full redacted stored record. HTTP
`/memory/retrieve` keeps the detailed administrative shape.

## Ask Result Shape

`/memory/ask` returns:

- `answer`: human-readable answer text. For fact-backed answers this uses
  normalized display values while preserving raw source text in stored memory and
  fact evidence.
- `results`: chunk-shaped retrieval hits. Fact-only answers can legitimately
  return an empty `results` list because the answer came from normalized facts,
  not retrieved chunks.
- `citations`: compact provenance for chunks or facts used in the answer.
- `provenance`: grouped source-conversation provenance.
- `confidence` and `confidence_reason`: confidence label plus the reason, such
  as a direct user statement, assistant statement, inferred recurring topic, or
  conflict.
- `answer_basis`: one of `direct_memory`, `fact_layer`, `mixed`, `conflict`, or
  `not_found`.
- `evidence`: stable evidence entries used by the answer. Chunk-backed answers
  return `type: "chunk"` entries; fact-backed answers return `type: "fact"`
  entries.
- `structured_evidence`: split evidence for clients that do not want to inspect
  mixed lists. `structured_evidence.facts` contains normalized fact evidence and
  `structured_evidence.results` contains chunk-shaped retrieval hits.
- `facts`: active normalized facts used by fact-backed answers, including
  `source_quality`, `confidence_reason`, `created_at`, `updated_at`,
  `last_confirmed_at`, `superseded_at`, `object_raw`, `object_normalized`,
  and save-intent provenance when the source memory included it. Facts derived
  from `metadata.save_intent: client_auto_save` use lower confidence than
  explicit user-requested saves.

Pass `max_context_tokens` to `/memory/ask` to build the answer from only the
chunks that fit the requested context budget. When budgeting is active, the
response can include `context_tokens_used`, `chunks_selected`, `chunks_dropped`,
and `tokenizer_used`.

MCP `memory_retrieve`, `memory_ask`, `memory_fact_search`, and
`memory_profile_get` support the same `response_format` enum as search.
Concise ask responses are
answer-centric: they keep `answer`, `confidence`, `confidence_reason`,
`answer_basis`, and small result/fact/citation counts, but omit full
`citations`, `evidence`, `structured_evidence`, `provenance`, chunk result
arrays, and token-budget diagnostics. Concise fact/profile reads deduplicate
and limit fact rows to 10 by default, drop qualifiers, source message indexes,
owner/project fields, and summary provenance while keeping fact display values,
freshness, source quality, confidence, and supersession status. Use
`response_format="detailed"` for the full evidence-rich answer and fact
provenance.

## CLI

Use `python -m memory.cli` during development, or the packaged `aim` console
script after installation.

```bash
python -m memory.cli ingest conversation.json --json
python -m memory.cli reindex --json
python -m memory.cli import manual copilot-chat.txt --source vscode-copilot --json
python -m memory.cli import copilot-activity-csv copilot-activity-history.csv --json
python -m memory.cli import copilot-cli-events-jsonl "$HOME/.copilot/session-state/<SESSION_ID>/events.jsonl" --json
python -m memory.cli import deepseek-harness-session-jsonl dsh-session/session.v4.jsonl --json
python -m memory.cli import droid-exec-json droid-capture.json --json
python -m memory.cli import hermes-session-jsonl hermes-backup.jsonl --json
python -m memory.cli import deepseek-share-json deepseek-share.json --json
python -m memory.cli import claude-code-session-jsonl "$HOME/.claude/projects/<PROJECT>/<SESSION_ID>.jsonl" --json
python -m memory.cli import codex-rollout-jsonl "$CODEX_HOME/sessions/2026/04/12/rollout-<SESSION_ID>.jsonl" --json
python -m memory.cli import gemini-cli-session-json gemini-session.json --json
opencode export <SESSION_ID> | python -m memory.cli import opencode-session-json - --json
python -m memory.cli import pi-session-jsonl "$HOME/.pi/agent/sessions/--work-project--/<SESSION_ID>.jsonl" --json
python -m memory.cli import pi-session-jsonl "$HOME/.omp/agent/sessions/--work-project--/<SESSION_ID>.jsonl" --json
python -m memory.cli import pi-session-jsonl openclaw-session.jsonl --source openclaw --json
python -m memory.cli import qwen-code-session-export qwen-session.json --json
python -m memory.cli import qwen-code-session-export qwen-session.jsonl --json
python -m memory.cli search "local-first tools" --top-k 5 --json
python -m memory.cli retrieve <MEMORY_ID> --json
python -m memory.cli ask "What did I store about local-first tools?" --top-k 5 --json
python -m memory.cli serve --host 127.0.0.1 --port 8000
```

Manual imports accept multiline messages labelled with common speaker names such
as `User:`, `You:`, `Human:`, `Assistant:`, `Copilot:`, `Claude:`, or `Gemini:`.
Use `-` as the file name to read the transcript from stdin.

Codex CLI and the Codex app persist complete session rollouts as JSONL under
`$CODEX_HOME/sessions/YYYY/MM/DD/`. Import those files with
`codex-rollout-jsonl`; the importer keeps only canonical user and assistant text
messages and omits reasoning, system/developer instructions, tool calls and
results, approvals, usage, injected startup context, and duplicate presentation
events. Sessions run with
`--ephemeral` do not create rollout files. `codex exec --json` is an output
protocol and is not guaranteed to contain the submitted prompt, so use the
persisted rollout unless the prompt was captured separately in a versioned
envelope. A result-only capture is rejected instead of creating a one-sided
conversation.

### Codex rollout compatibility contract

The importer contract was verified against **Codex CLI 0.152.0** on
2026-10-02. Both `codex --version` and the sampled rollout's
`session_meta.payload.cli_version` reported `0.152.0`; the rollout identified
itself with `source: "cli"` and `originator: "codex-tui"`. This is an observed
compatibility baseline, not a claim that Codex's local rollout format is a
stable public API. Validate a minimized fixture before declaring support for a
newer Codex version.

`codex-rollout-jsonl` consumes this bounded subset of the observed contract:

| JSONL record | Fields consumed | Behavior |
| --- | --- | --- |
| `session_meta` | `payload.id`, `timestamp`, `cwd`, `source`, `originator`, `cli_version`, `model_provider`, and optional `git.branch`/`git.commit_hash` | Maps safe session provenance into canonical metadata. Account identifiers, base instructions, and unrecognized fields are not copied. |
| `response_item` message | `payload.role`, textual `input_text`/`output_text` content blocks, and optional `internal_chat_message_metadata_passthrough.turn_id` | Keeps user `input_text` and assistant `output_text` only. When several user-role records share a turn ID, only the last is kept so injected startup context is excluded. |
| Every other record | None | Ignores event copies, developer/system instructions, reasoning, tools, approvals, usage, turn context, and unknown future record types. |

Malformed JSON fails with its line number. A result-only stream without both a
user message and an assistant message is rejected. Records without a turn ID
retain file order for compatibility with older or simplified rollouts.

The regression suite also includes a salted, fully synthetic Codex 0.152.0
rollout shaped like a resumed tool-using session. It verifies that five user
turns, five visible assistant progress messages, and five final answers survive
as 15 ordered conversation messages, while 14 reasoning records, 14 tool calls,
14 tool outputs, developer instructions, and injected environment context do
not enter canonical memory.

Microsoft Copilot activity exports must use the columns `Conversation`, `Time`,
`Author`, and `Message`. The importer groups rows by conversation and restores
the chronological user/assistant order used by the hub.

GitHub Copilot CLI stores resumable sessions under
`$COPILOT_HOME/session-state/<session-id>/events.jsonl`, with
`~/.copilot/session-state/` as the default location. Import a copied, inactive
event log with `copilot-cli-events-jsonl`. This is separate from the Microsoft
Copilot activity-history CSV importer above. The JSONL importer keeps visible,
top-level `user.message` and `assistant.message` text in event order and
deduplicates stable message or event IDs. It omits system and synthetic user
prompts, reasoning, sub-agent messages, tool calls and results, permissions,
compaction data, errors, usage, telemetry, and unknown event types.

The bundled Draft 2020-12 importer schema covers both initial `session.start`
and resumed-session metadata envelopes plus the observed minimal and current
message envelopes. Safe provenance includes the session ID, start time, working
directory, repository, Git root and branch, selected model, CLI version, and
title when those fields are present. Recognized malformed records and invalid
timestamps fail with their physical line number; unknown event types remain
forward-compatible. A malformed JSONL file, including a historical tool result
split across physical lines by an unescaped newline, fails instead of being
silently repaired into a partial conversation.

For a reviewable manual fallback, run `/share file <PATH>` (or its `/export`
alias) in Copilot CLI and import the resulting Markdown with `manual`. Review
either artifact for sensitive prompts, paths, and output before copying or
retaining it. Multipart HTTP upload remains outside the importer contract.

Factory Droid does not currently document a portable export for historical
interactive CLI sessions. The `droid-exec-json` importer therefore accepts only
the versioned `ai-memory-hub.droid-exec-capture` envelope for headless
`droid exec` runs. It rejects raw result-only JSON because that output omits the
submitted prompt.

For a one-shot run, record the exact submitted prompt and Droid's JSON result in
one capture. This example uses `jq` only to assemble the local capture file:

```bash
prompt='Explain this repository'
droid exec "$prompt" --output-format json > droid-result.json
jq -n --arg prompt "$prompt" --slurpfile result droid-result.json \
  '{
    format: "ai-memory-hub.droid-exec-capture",
    version: 1,
    kind: "one-shot",
    prompt: $prompt,
    result: $result[0],
    metadata: {cwd: "/workspace/project", model: "configured-model"}
  }' > droid-capture.json
python -m memory.cli import droid-exec-json droid-capture.json --json
```

Only a successful, non-empty one-shot result becomes an assistant message. The
importer preserves safe session ID, duration, reported turn count, model, cwd,
title, and timestamp metadata when supplied. Error results are rejected, and
unknown result fields are ignored.

For a multi-turn integration, launch the documented bidirectional protocol:

```bash
droid exec \
  --input-format stream-jsonrpc \
  --output-format stream-jsonrpc \
  --auto low
```

The recorder must wrap each JSON-RPC object with its direction rather than
concatenating stdin and stdout into an ambiguous log:

```json
{
  "format": "ai-memory-hub.droid-exec-capture",
  "version": 1,
  "kind": "stream-jsonrpc",
  "frames": [
    {
      "direction": "client-to-droid",
      "message": {
        "jsonrpc": "2.0",
        "id": "turn-1",
        "method": "droid.add_user_message",
        "params": {"text": "Explain this repository"}
      }
    },
    {
      "direction": "droid-to-client",
      "message": {
        "jsonrpc": "2.0",
        "method": "droid.session_notification",
        "params": {
          "notification": {
            "type": "assistant_text_delta",
            "messageId": "assistant-1",
            "blockIndex": 0,
            "textDelta": "This repository..."
          }
        }
      }
    },
    {
      "direction": "droid-to-client",
      "message": {
        "jsonrpc": "2.0",
        "method": "droid.session_notification",
        "params": {
          "notification": {"type": "agent_turn_completed", "reason": "completed"}
        }
      }
    }
  ]
}
```

The importer pairs `droid.add_user_message` requests with successfully completed
turns and coalesces `assistant_text_delta` values by message and block. It skips
system prompts, thinking, tool calls and results, permissions, errors, usage,
control traffic, incomplete turns, and unknown notifications. This contract
does not scrape the Droid TUI, follow organization `/share` links, or import
arbitrary historical Droid sessions. Raw-content OpenTelemetry export is not a
normal capture path: it is opt-in, can expose sensitive content, and can
truncate attributes. Multipart HTTP upload remains tracked separately.

DeepSeek Harness can download a session tree as
`dsh-session-<SESSION_ID>.zip` from its `/export` browser page. Only extract an
archive you generated or otherwise trust: inspect its entries first, reject
absolute paths or `..` traversal entries, and extract it into a new empty
directory rather than over existing files. The importer deliberately does not
open ZIP archives or attachments. Pass the extracted root `session.jsonl` or
`session.vN.jsonl` file to `deepseek-harness-session-jsonl`:

```bash
unzip -l dsh-session-<SESSION_ID>.zip
mkdir -p dsh-session
unzip -q dsh-session-<SESSION_ID>.zip -d dsh-session
python -m memory.cli import deepseek-harness-session-jsonl dsh-session/session.v4.jsonl --json
```

Choose the root file actually present in the archive; unversioned version 0
logs use `session.jsonl`, while later formats use `session.vN.jsonl`. Descendant
agent logs under `subagents/<SESSION_ID>/` are independent conversations and
may be imported separately with the same command. The importer records their
parent session ID and descendant relationship but does not merge the tree.

The bundled Draft 2020-12 schema supports canonical Harness session versions
0 through 4. It keeps visible user and assistant text, while omitting system
and request context, internal user messages, reasoning, commands, tools,
compaction content, usage, attachments, duplicate message updates, and unknown
event types. Exported logs with `seq` and `time` coordinates must contain a
complete dense sequence; raw canonical files without coordinates retain their
physical order. Mixed or non-monotonic ordering and malformed recognized
records fail with their physical line number. Multipart archive upload remains
outside this importer contract.

This format is distinct from `deepseek-share-json`, which consumes the public
DeepSeek chat share-content response. A future DeepSeek account-history CSV
importer is tracked separately; neither format should be passed to the Harness
session importer.

For a public DeepSeek share link, save the response from its share-content API
as JSON, then pass that file to `deepseek-share-json`. The importer keeps request
and response text, represents file-only turns by their attachment names, and
does not copy search-result snippets into memory. For example:

```bash
curl -fsS \
  'https://chat.deepseek.com/api/v0/share/content?share_id=<SHARE_ID>' \
  -o deepseek-share.json
python -m memory.cli import deepseek-share-json deepseek-share.json --json
```

OpenCode session exports can be streamed directly from `opencode export` or
read from a saved JSON file. The importer keeps user and assistant text in
export order, skips internal reasoning, tool-only turns, and synthetic or
ignored text parts, and records the OpenCode session ID, title, working
directory, and model as provenance when present. Sanitized exports are also
accepted; their redacted text remains redacted.

Pi and Oh My Pi store machine-readable sessions as version 3 JSONL trees under
`~/.pi/agent/sessions/` and `~/.omp/agent/sessions/`, respectively. Import a
copied, inactive session with `pi-session-jsonl`. The importer follows the last
entry's `parentId` ancestry to reconstruct the active branch, so abandoned fork
branches are not flattened into the conversation. It keeps user and assistant
text blocks in ancestry order and omits system/developer messages, thinking,
tool calls and results, custom/bootstrap records, compaction bodies, usage, and
provider-private data. Oh My Pi title/header markers and OpenClaw-namespaced
records are used for variant detection; use `--source openclaw` for older
OpenClaw archives without a distinguishing marker.

Malformed JSON reports its line number. Duplicate entry IDs, missing parents,
cycles, and unsupported session versions are rejected instead of guessing at a
partial conversation. Orphaned branches therefore fail closed even when they
are not the last branch. The importer accepts the optional fixed-width Oh My Pi
title record before the logical session header and preserves only bounded safe
provenance such as the session ID, working directory, model, title, and Git
branch or commit. Its bundled Draft 2020-12 importer schema validates the title
record, version 3 session header, entry-tree fields, and recognized message
envelopes before typed parsing. Schema failures report the source line and
field path when available.

Pi and Oh My Pi HTML exports are presentation-only and do not preserve the
session tree, so import their JSONL source instead. Current OpenClaw runtime
sessions live in a SQLite database; do not copy or read a live database file
through this importer because a main-file-only copy can omit write-ahead-log
data. Use an archived/legacy JSONL artifact or a coordinated OpenClaw export or
backup. The importer does not open paths found inside an export.

Claude Code persists resumable session transcripts as JSONL under
`~/.claude/projects/<project>/<session-id>.jsonl`. Import a copied, inactive
transcript with `claude-code-session-jsonl`. The importer retains non-empty user
and assistant text in file order, deduplicates repeated visible records by UUID,
and records the session ID, working directory, Git branch, and model when
present. Its bundled Draft 2020-12 importer schema defines the accepted message
envelope, role matching, content shapes, and compaction-summary exclusion. It
omits system prompts, thinking, tool calls and results, progress, usage,
compaction summaries, and file snapshots. Malformed JSON and recognized message
records that do not match the importer schema report their line number. Claude
Code's `/export transcript.txt` output is suitable for the `manual` importer
when a readable transcript is preferred, but it does not preserve the same
structured provenance. Review the transcript for sensitive content before
copying or importing it.

Gemini CLI sessions can be exported with `/export-session gemini-session.json`
and imported with `gemini-cli-session-json`. JSON written by
`/chat share gemini-session.json` is also accepted, as are legacy local saved-chat
JSON objects with the same session envelope. Current Gemini CLI autosaved session
files may use an internal JSONL format; use `/export-session` rather than passing
those files to this JSON importer. The importer maps `user` and `gemini`/`model`
roles to canonical messages, keeps text blocks in export order, and records the
session ID, project hash, workspace directories, start time, and first visible
model name when available. It omits injected session context, thoughts, tool
calls and results, token counts, diagnostics, and unknown fields.

Gemini's export formats may contain prompts, responses, workspace paths, and
other sensitive context. Review the JSON before sharing or retaining it outside
the CLI's local state. The bundled importer schema accepts only the documented
session-object and shared-history envelopes; malformed recognized messages fail
with their message index.

Qwen Code supports self-contained native transcript exports through
`/export json` and `/export jsonl`. Import either format with
`qwen-code-session-export`. The JSON form contains one session object; the
canonical JSONL form begins with a `session_metadata` record followed by the
same normalized message objects. A bounded message-only JSONL stream is also
accepted when metadata was captured separately. The importer keeps textual
`message.content` or `message.parts[].text` from user and assistant records,
deduplicates repeated message UUIDs, and records the session ID, start time,
working directory, Git repository and branch, model, and channel when present.

The bundled Draft 2020-12 importer schema validates the JSON envelope, JSONL
metadata, role matching, stable UUIDs, timestamps, and recognized message
content before typed parsing. System records, goal-state audit data, tool calls
and raw input/output, usage, aggregate file lists, and unknown future message
types are not copied into canonical memory. `qwen sessions list --json` is a
session metadata listing, not a transcript export; use `/export json` or
`/export jsonl` for conversation content. HTML and Markdown exports are
presentation-oriented and can be reviewed and imported with `manual` when a
human-readable fallback is preferable.

Review imported output before retaining sensitive conversations. The repository
fixtures are synthetic and contain no text copied from personal exports.

Hermes Agent's `sessions export` command writes one complete session object per
JSONL line. Import one session directly from standard input, or save and import
a multi-session backup:

```bash
hermes sessions export - --session-id <SESSION_ID> --redact \
  | python -m memory.cli import hermes-session-jsonl - --json

hermes sessions export hermes-backup.jsonl --redact
python -m memory.cli import hermes-session-jsonl hermes-backup.jsonl --json
```

Use `--redact` whenever an export may be shared or retained outside Hermes'
local state. The importer preserves each exported session as a separate hub
conversation and keeps safe provenance such as its session ID, Hermes source,
title, model, working directory, Git root and branch, parent session ID, and
UTC start time. It imports non-empty user and assistant text that remains in
Hermes' visible history, including compaction-archived turns, while excluding
rewound/edited-away rows and synthetic compressed context. System prompts,
tool calls and results, reasoning, timings, billing and token data, user/chat
identifiers, and unknown fields are not copied into canonical memory. Hermes
Markdown exports are presentation formats; use the documented JSONL backup for
structured import.

### Multipart HTTP imports

Authenticated API clients can use the same registered parsers as the CLI with
`POST /memory/import`. The request must be `multipart/form-data` with an
explicit `parser` text field and one uploaded `file` part. The server never
guesses a parser from the filename or media type, and the uploaded filename is
metadata only: it is never opened as a server path or treated as a URL.

JSON, JSONL, CSV, and speaker-labelled plain-text examples:

```bash
curl -fsS -X POST http://127.0.0.1:8000/memory/import \
  -H "Authorization: Bearer $MEMORY_API_TOKEN" \
  -F "parser=opencode-session-json" \
  -F "file=@opencode-session.json;type=application/json"

curl -fsS -X POST http://127.0.0.1:8000/memory/import \
  -H "Authorization: Bearer $MEMORY_API_TOKEN" \
  -F "parser=hermes-session-jsonl" \
  -F "file=@hermes-backup.jsonl;type=application/x-ndjson"

curl -fsS -X POST http://127.0.0.1:8000/memory/import \
  -H "Authorization: Bearer $MEMORY_API_TOKEN" \
  -F "parser=copilot-activity-csv" \
  -F "file=@copilot-activity.csv;type=text/csv"

curl -fsS -X POST http://127.0.0.1:8000/memory/import \
  -H "Authorization: Bearer $MEMORY_API_TOKEN" \
  -F "parser=manual" \
  -F "file=@transcript.txt;type=text/plain"
```

Optional `source` and `title` text fields have the same meaning as their CLI
options. An optional `schema` JSON part is accepted only when the selected
parser publishes a bounded override contract. Hermes currently supports
version 1 top-level field aliases, role aliases, and explicit ISO 8601, Unix
seconds, or Unix milliseconds timestamps:

```json
{
  "version": 1,
  "message_container": "turns",
  "session_id_field": "session_key",
  "role_field": "speaker",
  "content_field": "body",
  "role_map": {
    "human": "user",
    "ai": "assistant"
  },
  "timestamp": {
    "field": "created_at",
    "format": "iso8601"
  }
}
```

```bash
curl -fsS -X POST http://127.0.0.1:8000/memory/import \
  -H "Authorization: Bearer $MEMORY_API_TOKEN" \
  -F "parser=hermes-session-jsonl" \
  -F "file=@custom-hermes.jsonl;type=application/x-ndjson" \
  -F "schema=@hermes-parser-schema.json;type=application/json"

python -m memory.cli import hermes-session-jsonl custom-hermes.jsonl \
  --schema hermes-parser-schema.json --json
```

Overrides cannot contain remote or filesystem references, code, templates,
queries, or regular expressions, and cannot alter authentication, canonical
validation, save-intent policy, sensitive-content handling, hashing, storage,
or project ownership. The endpoint requires `memory:write`, accepts UTF-8 input
only, limits files to 20,000,000 bytes, limits override schemas to 65,536 bytes,
and returns bounded per-conversation IDs and statuses without echoing source
text or schema contents. All sessions are parsed before insertion begins; if a
later independent insertion fails, its index receives a redacted error receipt.

Shared options include `--config <path>`, `--json`, `--quiet`, and `--verbose`.
`search` also supports `--source`, `--date-from`, `--date-to`, repeated `--tags`,
`--thread-id`, and `--result-mode chunks|compact|conversations|threads`.

Diagnostics:

```bash
python -m memory.cli tokenizer-check --json
python -m memory.cli health --json
python -m memory.cli config-show --json
python -m memory.cli storage-check --json
```

Authenticated clients can inspect visible project workspaces through `GET /memory/projects`, `GET /memory/projects/default`, `GET /memory/projects/{project_id}`, and the matching MCP project helper tools.

Local bearer-token and project administration is CLI-first. Token creation prints
the raw bearer token once; token list and revoke commands only expose stable
token ids and non-secret prefixes.

```bash
python -m memory.cli admin user create jane --display-name "Jane" --json
python -m memory.cli admin user list --json
python -m memory.cli admin token create --user jane --display-name laptop --json
python -m memory.cli admin token list --user jane --json
python -m memory.cli admin token revoke <TOKEN_ID_OR_PREFIX> --json

python -m memory.cli admin project create shared-321 --owner jane --name "Shared 321" --json
python -m memory.cli admin project list --user jane --json
python -m memory.cli admin project member add shared-321 --user carl --role writer --json
python -m memory.cli admin project member list shared-321 --json
```

Fact review helpers:

```bash
python -m memory.cli fact-search --subject user --json
python -m memory.cli fact-search --source codex --status superseded --json
python -m memory.cli profile-get --subject user --predicate owns_item --source-quality corrected_by_user --save-intent-source codex --json
python -m memory.cli fact-supersede <OLD_FACT_ID> <NEW_FACT_ID> --json
```

## MCP Interface

When `interfaces.mcp` is enabled, the streamable HTTP MCP endpoint is mounted at:

```text
http://127.0.0.1:8000/mcp/
```

For user-facing MCP setup, run with `api.auth: oauth_resource_server` and open
`/connect`. The Connect UI shows the configured MCP resource URL, enabled
passport providers, sign-in status, hub-issued token workflow, and client setup
snippets. Google is the current live provider; `meta` and `x` are disabled
provider slots until their provider-specific flows are implemented. Client
snippets remain marked `Unverified` until checked against current client
releases. See the [Connect UI and OAuth setup guide](connect_ui.md) for
packages, Docker setup, provider status, and client verification notes.

For an external OpenID Connect provider such as Keycloak, use
`api.auth: oidc_resource_server`. This mode validates asymmetric access tokens
through provider discovery/JWKS and advertises the external issuer to MCP
clients; see [External OIDC and Keycloak](external_oidc.md).

Core tools:

- `memory_validate(conversation_json)`
- `memory_insert(conversation_json)`
- `memory_search(query, top_k=5, limit, cursor, result_mode="chunks", response_format="concise", source, date_from, date_to, tags, thread_id, handoff_only=false)`
- `memory_retrieve(id, response_format="concise", project_id, memory_status)`
- `memory_ask(question, top_k=5, max_context_tokens=None, result_mode="chunks", response_format="concise", source, date_from, date_to, tags, thread_id, handoff_only=false, project_id)`
- `memory_fact_search(query=None, subject=None, predicate=None, include_superseded=False, response_format="concise", limit=None, source, date_from, date_to, confidence, status, source_quality, save_intent, save_intent_source, freshness_from, freshness_to, project_id)`
- `memory_profile_get(subject="user", predicate, response_format="concise", limit=None, source, date_from, date_to, confidence, status, source_quality, save_intent, save_intent_source, freshness_from, freshness_to, project_id)`
- `memory_fact_supersede(fact_id, superseded_by)`
- `memory_project_list()`
- `memory_project_default_get()`
- `memory_project_get(project_id)`

`memory_profile_get` returns `facts` plus a `summary` object. Its default
concise projection keeps canonical direct-user and user-correction facts; use
an explicit predicate or `source_quality` filter to include assistant statements
or inferred topics. Detailed reads retain the complete timestamped fact history.
The summary includes freshness, source-quality counts, filters, save-intent
filters, and compact fact provenance. Insert also generates conversation, topic,
and project summaries from stored message text. Generated summaries are stored
separately from raw chunks and normalized facts; the conversation summary is
returned as `metadata.generated_summary` on search and retrieve responses.

`memory_insert` accepts one complete conversation object. There is intentionally
no bulk MCP insert tool. Clients may include a short `metadata.summary`, but
must still send the complete `messages` list.

Resources:

- `memory://conversation/example`

Templates:

- `memory://conversation/{id}`
- `memory://search/{query}`
- `memory://timeline/{day}`
- `memory://health`

Prompts:

- `save_conversation`
- `search_memory`
- `ask_memory`
- `summarize_conversation`

## MCP Session Flow

Initialize:

```bash
curl -i -X POST http://127.0.0.1:8000/mcp/ \
  -H "Content-Type: application/json" \
  -H "Accept: application/json, text/event-stream" \
  -d '{
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
      "protocolVersion": "2025-06-18",
      "capabilities": {},
      "clientInfo": {"name": "example-client", "version": "0.1.0"}
    }
  }'
```

Use the returned `Mcp-Session-Id` for later MCP calls:

```bash
curl -X POST http://127.0.0.1:8000/mcp/ \
  -H "Content-Type: application/json" \
  -H "Accept: application/json, text/event-stream" \
  -H "Mcp-Session-Id: <SESSION_ID>" \
  -d '{"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}'
```

## Native Clients

Register `ai-memory-hub` as an MCP server in clients such as Codex, opencode,
Claude, Copilot, VS Code, or Cursor, then point the client at:

```text
http://127.0.0.1:8000/mcp/
```

The same memory operations remain available through HTTP API endpoints, so agent
clients and direct service clients can share one backend.

## Storage Backends

The runtime uses one metadata provider and one vector provider. Select them with
`providers.metadata_db` and `providers.vector_db`. The blocks under
`storage.metadata_providers` and `storage.vector_providers` only hold settings
for possible providers; they are not active unless selected.

## Multilingual Retrieval

ai-memory-hub is not English-only. It stores Unicode text and uses the
configured embedding model for semantic retrieval. Multilingual retrieval works
when the configured embedding model supports the languages in the stored
conversation and the user's query.

The embedding provider, model, dimension, and relevant options form one vector
space. Use the same embedding configuration for ingestion and query-time
retrieval. If a persistent vector index was built with a different embedding
model or provider, reindex it or use a separate vector namespace/index. Dimension
checks catch many unsafe swaps, but same-dimension model changes can still
corrupt ranking if mixed silently.

### Changing Embedding Models For Existing Data

Use this runbook when you already have a metadata database containing your
conversations and you want to switch to another embedding model.

Do not point a new embedding model at the old vector table, collection, index, or
namespace. The vectors already stored there were produced in the old model's
embedding space. Mixing them with new vectors corrupts ranking, and
same-dimension models are not interchangeable.

1. Stop `aim serve` and any API, MCP, or CLI clients that can insert memory.
2. Back up metadata and vectors.
   - Default local metadata: back up `data/metadata.sqlite3`.
   - Default local vectors: back up `data/lancedb`.
   - PGVector: back up the configured `storage.vector_providers.pgvector.table_name`.
   - Hosted vector stores: take the provider's normal snapshot/export before
     changing collections, indexes, namespaces, or tables.
3. Choose the new embedding settings and set all of them together:

```yaml
providers:
  embeddings: http
  embedding_model: nomic-embed-text
  embedding_dimension: 768

embeddings:
  endpoint: http://127.0.0.1:11434/v1
```

4. Point the new config at an empty vector destination.
   - SQLite + LanceDB: keep `paths.data_dir` unchanged if you want the same
     `data/metadata.sqlite3`, but back up and replace only the `data/lancedb`
     vector directory. Use a new `paths.data_dir` only when you want a separate
     metadata database too.
   - PGVector: use a new `storage.vector_providers.pgvector.table_name`, such
     as `memory_vectors_nomic_768`.
   - Qdrant, Milvus, Weaviate, Redis, Typesense, MongoDB Atlas, Elasticsearch,
     OpenSearch, Pinecone, Turbopuffer, and Vespa: use a new collection, index,
     namespace, table, or schema name that contains no old-model vectors.
5. Keep the same metadata database unless you intentionally want a completely
   separate memory store. Keeping metadata preserves conversations, facts,
   generated summaries, projects, users, and tokens while the vector store is
   rebuilt.
6. Recalculate embeddings from the stored metadata:

```bash
uv run aim reindex --config new-embedding-config.yaml --json
```

`aim reindex` reads the existing conversation payloads from metadata, rebuilds
chunks, embeds them with the active embedding config, and writes vectors to the
active vector store with replacement semantics. It does not require original
transcript files or hand-written JSON files.

Useful options:

- `--project-id <id>` reindexes one project workspace.
- `--limit <n>` reindexes only the first `n` stored conversations, useful for a
  smoke test before a full run.
- `--include-inactive` also recalculates vectors for `pending_review` or
  `rejected` conversations. The default only reindexes active memory.

7. Verify the new index before removing the old one:

```bash
uv run aim storage-check --config new-embedding-config.yaml --json
uv run aim search "project memory smoke" --config new-embedding-config.yaml --top-k 5 --json
uv run aim ask "What do you remember about this project?" --config new-embedding-config.yaml --top-k 5 --json
```

Rollback is simple if you kept the backups: restore the old config that points
at the old embedding model and old vector destination. The metadata database can
stay in place when the migration only replayed duplicate conversation payloads.

```yaml
providers:
  metadata_db: postgres
  vector_db: pgvector

storage:
  metadata_providers:
    postgres:
      url: postgresql://user:password@127.0.0.1:5432/memory
  vector_providers:
    pgvector:
      url: postgresql://user:password@127.0.0.1:5432/memory
      table_name: memory_vectors
```

### SQLite + LanceDB

The default local setup stores metadata in SQLite and vectors in LanceDB:

```yaml
providers:
  metadata_db: sqlite
  vector_db: lancedb

paths:
  data_dir: ./data
```

### SQLite + Qdrant

Use Qdrant as a local Docker or Qdrant Cloud vector backend:

```yaml
providers:
  metadata_db: sqlite
  vector_db: qdrant

storage:
  vector:
    allow_fallback: false
    distance: cosine
  vector_providers:
    qdrant:
      url: http://127.0.0.1:6333
      api_key: ""
      collection: memory_vectors
```

Install the optional dependency with `uv sync --extra qdrant`.

### SQLite + Milvus

Use Milvus or Zilliz for larger vector deployments:

```yaml
providers:
  metadata_db: sqlite
  vector_db: milvus

storage:
  vector:
    allow_fallback: false
    distance: cosine
  vector_providers:
    milvus:
      uri: http://127.0.0.1:19530
      token: ""
      collection: memory_vectors
```

Install the optional dependency with `uv sync --extra milvus`.

### SQLite + Weaviate

Use Weaviate for schema-rich vector deployments:

```yaml
providers:
  metadata_db: sqlite
  vector_db: weaviate

storage:
  vector:
    allow_fallback: false
  vector_providers:
    weaviate:
      url: http://127.0.0.1:8080
      api_key: ""
      collection: MemoryVector
```

Install the optional dependency with `uv sync --extra weaviate`.

### SQLite + Elasticsearch

Use Elasticsearch when vector storage should live in an existing Elastic cluster:

```yaml
providers:
  metadata_db: sqlite
  vector_db: elasticsearch

storage:
  vector:
    allow_fallback: false
    distance: cosine
  vector_providers:
    elasticsearch:
      url: http://127.0.0.1:9200
      username: ""
      password: ""
      index: memory_vectors
```

Install the optional dependency with `uv sync --extra elasticsearch`.

### SQLite + OpenSearch

Use OpenSearch when vector storage should live in an existing OpenSearch cluster:

```yaml
providers:
  metadata_db: sqlite
  vector_db: opensearch

storage:
  vector:
    allow_fallback: false
    distance: cosine
  vector_providers:
    opensearch:
      url: http://127.0.0.1:9200
      username: ""
      password: ""
      index: memory_vectors
```

Install the optional dependency with `uv sync --extra opensearch`.

### SQLite + Redis/RediSearch

Use Redis when Redis Stack already owns operational infrastructure and
RediSearch should host vectors:

```yaml
providers:
  metadata_db: sqlite
  vector_db: redis

storage:
  vector:
    allow_fallback: false
    distance: cosine
  vector_providers:
    redis:
      url: redis://127.0.0.1:6379/0
      index: memory_vectors
      key_prefix: "memory_vectors:"
```

Install the optional dependency with `uv sync --extra redis`.

### SQLite + Vespa

Use Vespa when a deployed Vespa application already owns large-scale retrieval
infrastructure and ai-memory-hub should feed hub-owned embeddings into it:

```yaml
providers:
  metadata_db: sqlite
  vector_db: vespa

storage:
  vector:
    allow_fallback: false
    distance: cosine
  vector_providers:
    vespa:
      url: http://127.0.0.1:8080
      token: ""
      namespace: memory
      schema: memory_vector
      rank_profile: vector_similarity
```

Install the optional dependency with `uv sync --extra vespa`. Deploy the Vespa
application package and schema before starting ai-memory-hub.

### SQLite + Typesense

Use Typesense when vector storage should live in a lightweight search engine
with first-class filtering and faceting:

```yaml
providers:
  metadata_db: sqlite
  vector_db: typesense

storage:
  vector:
    allow_fallback: false
    distance: cosine
  vector_providers:
    typesense:
      url: http://127.0.0.1:8108
      api_key: ""
      collection: memory_vectors
```

Install the optional dependency with `uv sync --extra typesense`.

### SQLite + Pinecone

Use Pinecone when vector storage should live in hosted managed/serverless
infrastructure:

```yaml
providers:
  metadata_db: sqlite
  vector_db: pinecone

storage:
  vector:
    allow_fallback: false
    distance: cosine
  vector_providers:
    pinecone:
      api_key: ""
      index: memory-vectors
      namespace: default
      cloud: aws
      region: us-east-1
      create_index: false
```

Install the optional dependency with `uv sync --extra pinecone`.

### SQLite + Turbopuffer

Use Turbopuffer when vector storage should live in hosted object-storage-backed
search infrastructure:

```yaml
providers:
  metadata_db: sqlite
  vector_db: turbopuffer

storage:
  vector:
    allow_fallback: false
    distance: cosine
  vector_providers:
    turbopuffer:
      api_key: ""
      namespace: memory-vectors
      region: gcp-us-central1
```

Install the optional dependency with `uv sync --extra turbopuffer`.

### MongoDB Metadata And Atlas Vectors

Use MongoDB for metadata when Mongo already owns application persistence, and
use MongoDB Atlas Vector Search when Atlas should also own vectors:

```yaml
providers:
  metadata_db: mongodb
  vector_db: mongodb_atlas

storage:
  metadata_providers:
    mongodb:
      uri: mongodb://127.0.0.1:27017
      database: ai_memory_hub
      conversations_collection: conversations
  vector_providers:
    mongodb_atlas:
      uri: mongodb+srv://example.mongodb.net/app
      database: ai_memory_hub
      collection: memory_vectors
      index: memory_vector_index
```

Install the optional dependency with `uv sync --extra mongodb`.

### Postgres Metadata

Use Postgres for conversation metadata:

```yaml
providers:
  metadata_db: postgres
  vector_db: lancedb

storage:
  metadata_providers:
    postgres:
      url: postgresql://user:password@127.0.0.1:5432/memory
```

### PGVector

Use PGVector for vector storage:

```yaml
providers:
  metadata_db: postgres
  vector_db: pgvector

storage:
  metadata_providers:
    postgres:
      url: postgresql://user:password@127.0.0.1:5432/memory
  vector_providers:
    pgvector:
      url: postgresql://user:password@127.0.0.1:5432/memory
      table_name: memory_vectors
```

### In-Memory Vectors

Use the in-memory vector backend for tests and short-lived local experiments:

```yaml
providers:
  vector_db: memory
```

## Configuration

Configuration is loaded from `config.yaml` by default. A fuller example is
available in `example.config.yaml`.

```yaml
providers:
  embeddings: local
  embedding_model: local-hash
  embedding_dimension: 32
  metadata_db: sqlite
  vector_db: lancedb

storage:
  dry_run: false
  allow_trusted_appends: false
  # Metadata schema compatibility allow-list. Version 1 is current.
  metadata_schema_versions: [1]
  vector:
    allow_fallback: true
    distance: cosine
  vector_providers:
    qdrant:
      url: http://127.0.0.1:6333
      api_key: ""
      collection: memory_vectors
    milvus:
      uri: http://127.0.0.1:19530
      token: ""
      collection: memory_vectors
    weaviate:
      url: http://127.0.0.1:8080
      api_key: ""
      collection: MemoryVector
    mongodb_atlas:
      uri: ""
      database: ai_memory_hub
      collection: memory_vectors
      index: memory_vector_index
    elasticsearch:
      url: http://127.0.0.1:9200
      username: ""
      password: ""
      index: memory_vectors
    opensearch:
      url: http://127.0.0.1:9200
      username: ""
      password: ""
      index: memory_vectors
    redis:
      url: redis://127.0.0.1:6379/0
      index: memory_vectors
      key_prefix: "memory_vectors:"
    pinecone:
      api_key: ""
      index: memory-vectors
      namespace: default
      cloud: aws
      region: us-east-1
      create_index: false
    turbopuffer:
      api_key: ""
      namespace: memory-vectors
      region: gcp-us-central1
    vespa:
      url: http://127.0.0.1:8080
      token: ""
      namespace: memory
      schema: memory
      rank_profile: vector_similarity
    typesense:
      url: http://127.0.0.1:8108
      api_key: ""
      collection: memory_vectors
  metadata_providers:
    mongodb:
      uri: ""
      database: ai_memory_hub
      conversations_collection: conversations

tokenizer:
  enabled: false
  encoding: cl100k_base

ask:
  max_context_tokens: 2000

retrieval:
  vector_score_threshold: 7.5
  keyword_enabled: true
  keyword_candidate_limit: 50
  keyword_weight: 0.25
  metadata_weight: 0.15
  candidate_multiplier: 3

memory:
  insert_policy: permissive

chunking:
  strategy: message
  max_tokens: 800
  overlap_tokens: 80

schema:
  file: ./memory/schema/conversation.schema.json

interfaces:
  mcp: true
  api: true

paths:
  data_dir: ./data
  logs_dir: ./logs

embedding_endpoint:
  base_url: http://localhost:11434/v1
  api_key: dummy_key
```

`metadata_schema_versions` means "this app build supports these metadata store
schema versions." It is an array so future rolling migrations can temporarily
support old and new database layouts, for example `[1, 2]`. Version `1` is the
first and current metadata schema version. Startup fails if the active metadata
store reports a version outside this list.

The default `chunking.strategy: message` keeps one chunk per normalized message.
Set `chunking.strategy: token` to split long messages into token windows with
`chunking.max_tokens` and `chunking.overlap_tokens`. Token chunking is opt-in and
uses `tiktoken` when available, with a deterministic local heuristic fallback.

Retrieval first gathers vector candidates, filters low-confidence vector matches
with `retrieval.vector_score_threshold`, then applies deterministic keyword and
metadata reranking. `retrieval.keyword_enabled` also allows exact keyword matches
from metadata storage to supplement vector candidates when the active metadata
store supports text lookup.

`tokenizer.encoding` is an encoding name such as `cl100k_base`, not a model file
path. ai-memory-hub does not download tokenizer files itself. When the optional
`tiktoken` extra is installed, `tiktoken` resolves and caches the encoding data.
For persistent or offline deployments, set `TIKTOKEN_CACHE_DIR` to a writable
directory and prewarm the cache during setup:

```bash
TIKTOKEN_CACHE_DIR=./data/tiktoken-cache uv run python -c "import tiktoken; tiktoken.get_encoding('cl100k_base')"
```

Check which tokenizer path will be used:

```bash
uv run python -m memory.cli tokenizer-check --json
```

## Containers

Build the local image:

```bash
docker build -t ai-memory-hub:local -f Containerfile .
```

Run the default image locally:

```bash
docker run --rm -p 127.0.0.1:8000:8000 ai-memory-hub:local
```

The image exposes the API and MCP service on port `8000` and starts with:

```bash
/app/.venv/bin/aim serve --host 0.0.0.0 --port 8000
```

The built-in container configuration uses deterministic local embeddings, SQLite
metadata, and LanceDB vectors so the image starts without external model or
database services while keeping local vector state persistent under `/app/data`.

Container images run as a non-root user by default and are built so OpenShift can
override the runtime UID while keeping root-group write access to `/app/data`,
`/app/logs`, and `/app/.uv-cache`. Use `/ready` for readiness probes and
`/health` for liveness probes.

Kubernetes probe example:

```yaml
readinessProbe:
  httpGet:
    path: /ready
    port: 8000
livenessProbe:
  httpGet:
    path: /health
    port: 8000
```

For persistent local container data, mount `/app/data` and optionally `/app/logs`.
For custom configuration, mount a config file and start with:

```bash
uv run aim serve --config <path>
```

Keep secrets in a mounted config file or injected environment variables, not in
committed images or repository files.

For a reusable Docker/Podman Compose setup that runs ai-memory-hub with Postgres
metadata and PGVector vectors:

```bash
cd examples/local-stack
docker compose up --build
```

The example binds ai-memory-hub on host port `8000` for LAN clients. Use
`http://<HOST_LAN_IP>:8000/mcp/` from another PC on the same network. It also
enables tokenizer budgeting with the optional `tiktoken` extra. For remote
Ollama embeddings, see `examples/local-stack/config.oauth-public.yaml`.

Additional checked-in provider examples are under `examples/storage_providers`.
They cover SQLite/LanceDB, in-memory vectors, Qdrant, MongoDB metadata,
MongoDB Atlas Vector Search, Milvus, Weaviate, Elasticsearch, OpenSearch,
Redis/RediSearch, Vespa, Typesense, Pinecone, and Turbopuffer.

## Testing

Run the full local suite:

```bash
uv run pytest
```

Run storage tests only:

```bash
uv run pytest tests/integration/test_storage_features.py
```

Default local runs do not require external storage services. Live provider tests
are skipped unless their `AMH_TEST_*` environment variables are set. GitHub
Actions runs fake-client provider contracts in the normal CI suite and service-
backed live provider checks in `.github/workflows/storage-providers.yml`.

Run live Postgres and PGVector tests locally:

```bash
docker run --name aim-pgvector-test \
  -e POSTGRES_USER=test \
  -e POSTGRES_PASSWORD=test \
  -e POSTGRES_DB=memory \
  -p 5432:5432 \
  -d pgvector/pgvector:pg16

export AMH_TEST_POSTGRES_DSN="postgresql://test:test@127.0.0.1:5432/memory"
uv run pytest -q tests/integration/test_storage_features.py -k "postgres_live_integration_when_dsn_provided or postgres_schema_version or pgvector_live_integration_when_dsn_provided or runtime_postgres_pgvector_live_integration_when_dsn_provided"

docker rm -f aim-pgvector-test
```

Benchmarks and evaluation helpers:

```bash
uv run python -m memory.benchmarks.token_budget --iterations 200
uv run python -m memory.benchmarks.retrieval_quality
uv run python -m memory.benchmarks.cache_candidates --iterations 1000
```

Container CI verifies Hadolint, image build, packaged `aim serve` startup,
`/ready`, MCP initialize at `/mcp/`, arbitrary non-root UID behavior, and
writable runtime paths.

A Bruno integration layer provides black-box local and CI smoke tests for the
live API/MCP surface against a running server and configured metadata/vector
stores. The Bruno lane uploads HTML and JUnit artifacts and publishes JUnit
results through GitHub Actions test summaries. Pytest CI jobs also emit JUnit
XML artifacts for unit/integration, E2E, and storage lanes. See the
[Bruno integration test plan](bruno_integration_test_plan.md).

## Project Structure

```text
memory/
  api/          FastAPI application and HTTP routes
  backend/      metadata stores, vector stores, redaction, dry-run wrappers
  ingestion/    schema validation and ingestion agents
  interfaces/   MCP server implementation
  schema/       conversation JSON schema
docs/           architecture notes, plans, and improvement documents
tests/          unit, integration, and end-to-end tests
```

## Security Considerations

- Store real API keys outside committed configuration files.
- Use secure database connection strings and network access controls.
- Treat stored conversations as sensitive application data.
- Review retention, deletion, and redaction requirements before production use.
- Add authentication before exposing the API or MCP endpoint beyond localhost.

## More Documentation

- [Architecture](architecture.md)
- [Agent integration](agents.md)
- [Roadmap](roadmap.md)
- [Release, container, and docs publishing plan](release_container_docs_plan.md)
- [First release readiness plan](first_release_readiness_plan.md)
- [Project promotion plan](project_promotion_plan.md)
- [Bruno integration test plan](bruno_integration_test_plan.md)
- [Browser extension capture plan](browser_extension_capture_plan.md)
- [Observability, logging, and telemetry plan](observability_logging_telemetry_plan.md)
- [Recurring codebase cleanup plan](recurring_codebase_cleanup_plan.md)
- [Bearer/API-key auth plan](bearer_api_key_auth_plan.md)
- [Project workspace collaboration plan](project_workspace_collaboration_plan.md)
- [Improvement plans](improvements.md)
