# Import Conversations

Use this guide to move existing conversations into ai-memory-hub. First get the
conversation file from the source. Then give it to an AI agent that can reach
your hub, or upload it through the HTTP API yourself.

!!! warning "Review private data first"
    Conversation exports can contain prompts, answers, file names, and project
    paths. Keep exports private. Close the source app, or copy its session file,
    before importing a file that the app may still be writing.

## Ask your AI agent to import the file

This is the easiest path when Codex, Claude, OpenCode, or another agent can
access both the conversation file and your ai-memory-hub server.

1. Find the source in the table below and note its importer name.
2. Attach the exported file to the agent, or give the agent its local path.
3. Send this request, replacing the values in `<ANGLE_BRACKETS>`:

```text
Import <FILE> into my ai-memory-hub at <HUB_URL>.
Use the <PARSER> importer and POST the file to /memory/import as multipart form data.
Use the hub credentials already configured for this workspace. Do not display or
log the access token or conversation contents. Report the conversation count and
the status and memory ID returned for each conversation. If you cannot access the
file or the HTTP import endpoint, stop and tell me what access is missing.
```

The agent should send the original file to the hub. It should not rewrite the
conversation into a new format or call `memory_insert` once per message; the
registered importer already owns parsing, validation, normalization, hashing,
and deduplication.

!!! note "HTTP access is required"
    Structured file import uses `POST /memory/import`. An agent connected only
    through MCP needs an HTTP-capable tool or local terminal access as well. Its
    bearer token must include `memory:write`.

## Use the API directly

If you prefer to upload the file yourself, use the importer name from the table:

```bash
curl -fsS -X POST "<HUB_URL>/memory/import" \
  -H "Authorization: Bearer $MEMORY_API_TOKEN" \
  -F "parser=<PARSER>" \
  -F "file=@<FILE>;type=application/octet-stream"
```

For a hub running on the same computer, `<HUB_URL>` is commonly
`http://127.0.0.1:8000`. Keep the token in an environment variable; do not paste
it into an AI prompt. A successful response includes `conversation_count` and
a `results` list containing each memory ID, status, and deduplication result.

### Upload limits

- One file per request, with a maximum file size of 20 MB.
- The complete multipart request must be no larger than 20.25 MB.
- One file can produce at most 100 conversations.
- Files must be UTF-8 text. UTF-8 with a byte-order mark is accepted.
- Accepted upload types are JSON, JSONL/NDJSON, CSV, plain text, or generic
  `application/octet-stream`. The selected importer, not the filename, decides
  the required structure.
- ZIP files are not imported directly. Extract a supported file first.
- The `parser` and `file` parts are required. Optional `source` and `title`
  fields can override the stored source or title.

## Quick chooser

| Source | What to get | Importer |
| --- | --- | --- |
| Microsoft Copilot | Activity-history CSV | `copilot-activity-csv` |
| GitHub Copilot CLI | `events.jsonl` | `copilot-cli-events-jsonl` |
| Claude Code | Session `.jsonl` | `claude-code-session-jsonl` |
| Codex CLI or app | Rollout `.jsonl` | `codex-rollout-jsonl` |
| Hermes Agent | `hermes sessions export` JSONL | `hermes-session-jsonl` |
| OpenCode | Session export JSON | `opencode-session-json` |
| DeepSeek web | One conversation's share JSON | `deepseek-share-json` |
| DeepSeek Harness | Extracted root session JSONL | `deepseek-harness-session-jsonl` |
| Gemini CLI | Shared/exported session JSON | `gemini-cli-session-json` |
| Pi or Oh My Pi | Session `.jsonl` | `pi-session-jsonl` |
| OpenClaw | Archived or legacy Pi-family JSONL | `pi-session-jsonl` |
| Qwen Code | Session export JSON or JSONL | `qwen-code-session-export` |
| Factory Droid | Versioned Droid capture JSON | `droid-exec-json` |
| Any readable transcript | Speaker-labelled text | `manual` |

## Prompt examples for supported agents

These prompts tell the agent to transport the source file without reading it
into the model context. Replace the placeholders, and keep the access token in
the agent's configured environment.

### Microsoft Copilot export

```text
Import the Microsoft Copilot activity CSV <FILE> into <HUB_URL>. Upload it
unchanged to POST /memory/import with parser=copilot-activity-csv. Do not read or
repeat the conversations. Report the count and each result's status and ID.
```

### Codex

```text
Import the closed Codex task rollout at <FILE> into <HUB_URL>. Upload the original
file to POST /memory/import with parser=codex-rollout-jsonl. Use the configured
hub credential, do not read the rollout into model context, and report only the
conversation count, result status, memory ID, and deduplicated flag.
```

### Claude Code

```text
Import the copied Claude Code session at <FILE> into <HUB_URL>. Upload it unchanged
to POST /memory/import with parser=claude-code-session-jsonl. Do not print or read
the transcript. Return the conversation count and each result's status and ID.
```

### GitHub Copilot CLI

```text
Import <FILE>, a copied inactive GitHub Copilot CLI events.jsonl, into <HUB_URL>.
Use POST /memory/import with parser=copilot-cli-events-jsonl and the configured
hub credential. Do not inspect the conversation text. Report the import results.
```

### Hermes Agent

```text
Export the Hermes session <SESSION_ID> to <FILE> as JSONL, then upload that file
unchanged to <HUB_URL>/memory/import with parser=hermes-session-jsonl. Do not load
the transcript into model context. Report the returned count, statuses, and IDs.
```

### OpenCode

```text
Export OpenCode session <SESSION_ID> as sanitized JSON to <FILE>, then upload it
unchanged to <HUB_URL>/memory/import with parser=opencode-session-json. Use the
configured hub credential and report the count, statuses, IDs, and duplicates.
```

### DeepSeek Harness

```text
Import the extracted root DeepSeek Harness log <FILE> into <HUB_URL>. Use POST
/memory/import with parser=deepseek-harness-session-jsonl. Do not upload the ZIP,
attachments, or subagent logs, and do not read the log into model context.
```

### DeepSeek web

```text
Import the DeepSeek conversation share JSON <FILE> into <HUB_URL>. Upload the
original file to POST /memory/import with parser=deepseek-share-json. Do not read
or repeat its contents. Report the count, status, memory ID, and duplicates.
```

### Gemini CLI

```text
Import the Gemini CLI session export <FILE> into <HUB_URL>. Upload the original
JSON to POST /memory/import with parser=gemini-cli-session-json. Do not inspect or
repeat its contents. Report the conversation count, status, ID, and duplicates.
```

### Pi

```text
Import the copied inactive Pi session <FILE> into <HUB_URL>. POST it unchanged to
/memory/import with parser=pi-session-jsonl and source=pi. Do not read the session
into model context. Report only the import result.
```

### Oh My Pi

```text
Import the copied inactive Oh My Pi session <FILE> into <HUB_URL>. POST it unchanged
to /memory/import with parser=pi-session-jsonl and source=oh-my-pi. Do not inspect
the transcript. Report the count, status, memory ID, and deduplicated flag.
```

### OpenClaw

```text
Import the archived OpenClaw JSONL session <FILE> into <HUB_URL>. Use POST
/memory/import with parser=pi-session-jsonl and source=openclaw. Do not use or copy
a live SQLite database. Report the returned import result.
```

### Qwen Code

```text
Import the Qwen Code JSON or JSONL export <FILE> into <HUB_URL>. Upload it unchanged
to POST /memory/import with parser=qwen-code-session-export. Do not read it into
model context. Report the count, statuses, memory IDs, and duplicates.
```

### Factory Droid

```text
Import the versioned Factory Droid capture <FILE> into <HUB_URL>. Use POST
/memory/import with parser=droid-exec-json. Do not send raw result-only JSON and
do not print the capture contents. Report the returned import result.
```

### Manual transcript

```text
Import the speaker-labelled transcript <FILE> into <HUB_URL>. Use POST
/memory/import with parser=manual and source=<SOURCE>. Upload it unchanged, do not
repeat private text, and report the count, status, memory ID, and duplicates.
```

Microsoft Copilot and DeepSeek web produce files but cannot normally reach a
local hub themselves. Give their downloaded files and the matching prompt to
Codex, Claude Code, OpenCode, or another HTTP-capable agent.

### Estimated token use

| Method | Estimated additional LLM tokens |
| --- | ---: |
| Direct API call or automatic shell hook | **0** |
| Asking an agent to upload without reading the file | About **200-800** |
| Asking an agent to inspect or summarize the file | Unbounded; can exceed the model context |

The agent estimate covers a short instruction, tool call, and concise result.
It does not include the client's system prompt or earlier conversation. Actual
usage varies by agent and model. Uploading raw bytes through HTTP does not add
the file to model context; reading the file first does. As a rough warning,
1 MB of ordinary text can represent hundreds of thousands of tokens.

## Token-free automatic import

The import itself does not need an LLM. A shell script called by an agent hook,
file watcher, or manual command can upload the file directly for **zero model
tokens**:

```bash
#!/usr/bin/env sh
set -eu

: "${MEMORY_HUB_URL:?Set MEMORY_HUB_URL}"
: "${MEMORY_API_TOKEN:?Set MEMORY_API_TOKEN}"

parser=${1:?Usage: import-to-memory.sh PARSER FILE [SOURCE]}
file=${2:?Usage: import-to-memory.sh PARSER FILE [SOURCE]}
source=${3:-}

if [ ! -f "$file" ]; then
  echo "File not found: $file" >&2
  exit 1
fi

if [ -n "$source" ]; then
  curl -fsS -X POST "${MEMORY_HUB_URL%/}/memory/import" \
    -H "Authorization: Bearer $MEMORY_API_TOKEN" \
    -F "parser=$parser" \
    -F "source=$source" \
    -F "file=@$file;type=application/octet-stream"
else
  curl -fsS -X POST "${MEMORY_HUB_URL%/}/memory/import" \
    -H "Authorization: Bearer $MEMORY_API_TOKEN" \
    -F "parser=$parser" \
    -F "file=@$file;type=application/octet-stream"
fi
```

Save it as `import-to-memory.sh`, make it executable, and configure the agent's
hook to call it with the appropriate importer and exported file path. For
example:

```bash
./import-to-memory.sh codex-rollout-jsonl "$CODEX_ROLLOUT_FILE"
./import-to-memory.sh pi-session-jsonl "$PI_SESSION_FILE" pi
```

Only configure automatic hooks for files you intend to retain. Keep the token
in the hook environment, restrict script permissions, and never echo it. Hook
configuration differs between agents, but the script and API contract stay the
same.

## Microsoft Copilot

This works for a personal Microsoft account. Work or school accounts may show
different controls.

1. Sign in to the [Microsoft privacy dashboard](https://account.microsoft.com/privacy).
2. Open **Privacy > Empower your productivity > Copilot > Your Copilot app
   activity history**.
3. Choose **Copilot apps** for free Copilot, or **Copilot in Microsoft 365
   apps** for the paid Microsoft 365 experience.
4. Select **Export all activity history**. Your browser downloads a CSV,
   normally named `copilot-activity-history.csv`.
5. Give the CSV to your agent and use the `copilot-activity-csv` importer.

The CSV must contain the columns `Conversation`, `Time`, `Author`, and
`Message`. Microsoft documents the dashboard steps in
[Manage your Copilot activity history](https://support.microsoft.com/en-us/privacy/manage-your-copilot-activity-history-in-the-privacy-dashboard).

### GitHub Copilot CLI

GitHub Copilot CLI is separate from the Microsoft Copilot web export above.
Exit Copilot CLI first, then give the chosen session's `events.jsonl` to your
agent and use the `copilot-cli-events-jsonl` importer.

Use `/session` inside Copilot CLI to see the current session ID, or `/resume`
to browse sessions. If `COPILOT_HOME` is set, replace `.copilot` with that
directory.

## Claude Code

Claude Code saves one JSONL transcript per session. Exit the session, find the
project folder under `projects`, then give the session file to your agent and
use the `claude-code-session-jsonl` importer.

If `CLAUDE_CONFIG_DIR` is set, look in `$CLAUDE_CONFIG_DIR/projects/` instead.
Subagent files are stored in a `subagents` folder; choose the top-level session
file for the main conversation. This structured importer is for Claude Code,
not claude.ai web chats; use the [manual transcript](#manual-transcripts-and-factory-droid)
fallback for a web transcript.

## Codex CLI and Codex app

Codex stores session rollouts by date. Close the task, then give its
`rollout-*.jsonl` file to your agent and use the `codex-rollout-jsonl` importer.

If `CODEX_HOME` is set, use `$CODEX_HOME/sessions/` instead. Archived rollouts
may be under `$CODEX_HOME/archived_sessions/`. Ephemeral sessions do not create
a rollout file.

## Hermes Agent

Hermes keeps current conversations in a database, so do not copy `state.db`
into the importer. Ask Hermes to create a JSONL export:

```bash
hermes sessions export hermes-backup.jsonl
```

Give `hermes-backup.jsonl` to your agent and use the `hermes-session-jsonl`
importer. To export one session, add `--session-id <SESSION_ID>`. Add `--redact`
if you want Hermes to remove recognized credentials from the exported copy. The
[Hermes session guide](https://github.com/NousResearch/hermes-agent/blob/main/website/docs/user-guide/sessions.md)
also documents filters for bulk exports.

## OpenCode

Use OpenCode's export command instead of copying its internal data directory:

```bash
opencode session list
opencode session export <SESSION_ID> --sanitize > opencode-session.json
```

On older OpenCode releases, the export command is
`opencode export <SESSION_ID>`. Omit `--sanitize` only when you want the hub to
receive the unredacted conversation. Give the JSON file to your agent and use
the `opencode-session-json` importer. See the current
[OpenCode CLI commands](https://opencode.ai/v2/docs/cli/commands/).

## DeepSeek

DeepSeek web conversations are handled one at a time; the hub does not scrape
your account or browser.

If DeepSeek shows **Export as JSON** for the open conversation, download that
file and check that it contains `data.biz_data`. Give it to your agent and use
the `deepseek-share-json` importer.

If the downloaded JSON has a different shape, use the supported share method:

1. Open the conversation in DeepSeek.
2. Create a share link for the messages you want to import.
3. Copy the final part of the link: this is the `<SHARE_ID>`.
4. Download the conversation JSON:

```bash
curl -fsS \
  "https://chat.deepseek.com/api/v0/share/content?share_id=<SHARE_ID>" \
  -o deepseek-share.json
```

Delete or disable the share link afterwards if you no longer need it. This
importer expects DeepSeek's share response, whose JSON contains
`data.biz_data`; unrelated browser-extension JSON formats are not interchangeable.

### DeepSeek Harness

In the Harness web interface, open the session and choose **Download session
log**, or enter `/export`. Extract the downloaded
`dsh-session-<SESSION_ID>.zip`, then import the root `session.jsonl` or
`session.vN.jsonl` file. Give that root file to your agent and use the
`deepseek-harness-session-jsonl` importer.

Do not pass the ZIP itself. Files below `subagents/` are separate conversations
and can be imported separately.

## Gemini CLI

Gemini's automatically saved files use an internal format. Resume the session
you want, then create a supported JSON export inside Gemini CLI:

```text
/chat share gemini-session.json
```

Give the new file to your agent and use the `gemini-cli-session-json` importer.

Older Gemini CLI releases may call the command
`/export-session gemini-session.json`. Both exported JSON shapes are supported.

## Pi and Oh My Pi

Pi and Oh My Pi (OMP) save sessions as JSONL trees. Close the session or copy
the file before importing it.

Give the selected file to your agent and use the `pi-session-jsonl` importer.
Use source `pi` for Pi or `oh-my-pi` for Oh My Pi.

The importer reconstructs the active branch of a session tree. Do not use the
HTML export because it does not preserve that tree.

### OpenClaw

The same importer accepts archived or legacy OpenClaw JSONL session trees. Give
the file to your agent, use `pi-session-jsonl`, and set source to `openclaw`.

Current OpenClaw sessions use SQLite. Do not copy a live database file: a copy
can be incomplete while its write-ahead log is active. Use a coordinated
OpenClaw export or backup, or an older JSONL artifact.

## Qwen Code

In the Qwen Code session, export a self-contained machine-readable copy:

```text
/export json
```

You can use `/export jsonl` instead. Give the file Qwen creates to your agent
and use the `qwen-code-session-export` importer.

`/export md` and `/export html` are readable presentation formats; use JSON or
JSONL for this importer.

## Where local sessions are stored

These are the default locations. `<you>` is your operating-system user name.
An export command is still preferred for Hermes, OpenCode, Gemini CLI, and Qwen
Code because their internal storage can change or use a different format.

| Source | Linux | macOS | Windows |
| --- | --- | --- | --- |
| Claude Code | `/home/<you>/.claude/projects/<project>/*.jsonl` | `/Users/<you>/.claude/projects/<project>/*.jsonl` | `C:\Users\<you>\.claude\projects\<project>\*.jsonl` |
| Codex | `/home/<you>/.codex/sessions/YYYY/MM/DD/` | `/Users/<you>/.codex/sessions/YYYY/MM/DD/` | `C:\Users\<you>\.codex\sessions\YYYY\MM\DD\` |
| GitHub Copilot CLI | `/home/<you>/.copilot/session-state/<id>/events.jsonl` | `/Users/<you>/.copilot/session-state/<id>/events.jsonl` | `C:\Users\<you>\.copilot\session-state\<id>\events.jsonl` |
| Hermes Agent | `/home/<you>/.hermes/state.db` | `/Users/<you>/.hermes/state.db` | `C:\Users\<you>\.hermes\state.db` |
| OpenCode data | `/home/<you>/.local/share/opencode/` | `/Users/<you>/.local/share/opencode/` | `C:\Users\<you>\.local\share\opencode\` |
| Gemini CLI | `/home/<you>/.gemini/tmp/<project_hash>/chats/` | `/Users/<you>/.gemini/tmp/<project_hash>/chats/` | `C:\Users\<you>\.gemini\tmp\<project_hash>\chats\` |
| Pi | `/home/<you>/.pi/agent/sessions/` | `/Users/<you>/.pi/agent/sessions/` | `C:\Users\<you>\.pi\agent\sessions\` |
| Oh My Pi | `/home/<you>/.omp/agent/sessions/` | `/Users/<you>/.omp/agent/sessions/` | `C:\Users\<you>\.omp\agent\sessions\` |
| Qwen Code | `/home/<you>/.qwen/projects/<project>/chats/` | `/Users/<you>/.qwen/projects/<project>/chats/` | `C:\Users\<you>\.qwen\projects\<project>\chats\` |

OpenCode can print the exact database path used by your installation with
`opencode db path`. Pi and Oh My Pi can be relocated with
`PI_CODING_AGENT_DIR`; Codex uses `CODEX_HOME`; Qwen Code can use `QWEN_HOME`.

## Manual transcripts and Factory Droid

For a source without a structured importer, make a text file with speaker
labels:

```text
User: What did we decide?
Assistant: We decided to keep the local-first design.
```

Give the text file to your agent and use the `manual` importer. You can also
provide a short source name, such as `claude-web` or `other-assistant`.

Factory Droid has no portable historical-session export. The supported
`droid-exec-json` format is a versioned capture made while running
`droid exec`; see [Droid capture details](overview.md#cli)
before using it.

## Check the result with your agent

A successful API response has status `ok`, a `conversation_count`, and one
result for each stored conversation. A Microsoft Copilot or Hermes bulk file
can import more than one conversation.

Ask the agent to confirm the import and test that it is searchable:

```text
Tell me how many conversations were imported and whether any were deduplicated.
Then search my memory for "<A PHRASE FROM THE CONVERSATION>" and show the matching
memory title and ID. Do not repeat private conversation text unnecessarily.
```

Importing the same source file again is safe: normal hub hashing and
deduplication rules still apply.

## Local command-line fallback

If an agent cannot call the HTTP endpoint but you are working inside the
ai-memory-hub repository, the local CLI uses the same registered importers:

```bash
uv run python -m memory.cli import <PARSER> "<FILE>" --json
```

Use `--source <SOURCE>` when the source is not implied by the importer, such as
Pi, Oh My Pi, OpenClaw, or a manual transcript. This fallback writes through
the same validation, hashing, deduplication, and storage pipeline as the API.
