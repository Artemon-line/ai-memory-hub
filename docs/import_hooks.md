# Opt-in zero-token import hooks

These recipes upload completed conversation artifacts without asking a model to
read, parse, or summarize them. The HTTP transport itself therefore consumes
**zero additional model tokens**. The surrounding agent session still has its
normal token use.

Nothing is enabled by default. Start with the manual command, then install only
the lifecycle hooks you want. The recipes were checked on 2026-10-29 with Codex
CLI 0.152.0, Claude Code 2.1.288, OpenCode 1.19.0, and Gemini CLI 0.57.0. Hook
formats and export commands are upstream compatibility contracts; recheck this
page after upgrading a client.

## Shared uploader

All four recipes call `tools/import_to_memory.py`. Set the hub connection in the
shell or service environment that starts the client; do not put the bearer token
in a hook command or a repository file:

```bash
export MEMORY_HUB_URL=http://127.0.0.1:8000
export MEMORY_API_TOKEN='<memory:write bearer token>'
```

Run a manual smoke check with a completed export:

```bash
python3 /absolute/path/to/ai-memory-hub/tools/import_to_memory.py \
  <PARSER> /absolute/path/to/completed-export.json
```

The uploader (including client-specific adapters that delegate to it):

- accepts only regular, non-empty files up to 20 MB;
- normally refuses files modified in the last five seconds;
- copies to a private temporary snapshot and refuses the upload if the source
  changes while copied;
- deletes the snapshot after success or failure;
- never prints the token, request body, conversation text, or full server
  response; and
- prints at most 100 bounded result receipts. Repeated runs use the hub's normal
  hash deduplication and report `deduplicated: true` where applicable.

Missing and ephemeral sessions fail locally because no file exists. An active or
still-changing file is refused. `--min-age-seconds 0` is reserved for synchronous
client end hooks that provide a closed transcript path; it still uses and checks
the snapshot.

## Codex CLI and app

Codex `SessionEnd` supplies `transcript_path` on standard input and runs
synchronously after the main task ends. Add this entry to the user-level
`~/.codex/hooks.json` file, merging it with any existing `hooks` object:

```json
{
  "hooks": {
    "SessionEnd": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "python3 /absolute/path/to/ai-memory-hub/tools/import_to_memory.py codex-rollout-jsonl --hook-json-field transcript_path --min-age-seconds 0",
            "timeout": 30
          }
        ]
      }
    ]
  }
}
```

This imports the path provided by Codex; it does not search for the newest file.
Ephemeral tasks without a persisted rollout are skipped with a bounded error.
Remove the `SessionEnd` entry (or the whole file if it contains nothing else) to
disable the automation. Codex requires review/trust before running a new
non-managed hook. See the upstream [Codex hooks reference](https://developers.openai.com/codex/hooks).

## Claude Code

Claude Code `SessionEnd` supplies the closed session's `transcript_path` on
standard input. Add this entry to `~/.claude/settings.json`, merging it with any
existing settings and hooks:

```json
{
  "hooks": {
    "SessionEnd": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "python3 /absolute/path/to/ai-memory-hub/tools/import_to_memory.py claude-code-session-jsonl --hook-json-field transcript_path --min-age-seconds 0",
            "timeout": 30
          }
        ]
      }
    ]
  }
}
```

Only top-level session transcripts should be imported; do not point the command
at files under a `subagents` directory. Remove this `SessionEnd` entry to disable
it. The hook is intentionally installed at user scope so an untrusted repository
cannot replace the command. See the upstream [Claude Code hooks reference](https://code.claude.com/docs/en/hooks).

## OpenCode

Do not upload on `session.idle`: an idle session can be resumed, so its export is
not a closed-session boundary. Use this explicit post-session command after
leaving the session. It creates a sanitized, self-contained export and uploads
that snapshot:

```bash
import-opencode-session() (
  session_id=${1:?Usage: import-opencode-session SESSION_ID}
  artifact=$(mktemp "${TMPDIR:-/tmp}/opencode-session.XXXXXX")
  trap 'rm -f "$artifact"' EXIT HUP INT TERM
  opencode session export "$session_id" --sanitize >"$artifact"
  python3 /absolute/path/to/ai-memory-hub/tools/import_to_memory.py \
    opencode-session-json "$artifact" --min-age-seconds 0
)
```

Find the ID with `opencode session list`, exit that session, then run
`import-opencode-session <SESSION_ID>`. Remove the shell function from your
profile to disable it. No background watcher or project plugin is installed.
See the upstream [OpenCode session commands](https://opencode.ai/v2/docs/cli/commands/).

## Gemini CLI

Gemini's `SessionEnd` hook is best-effort and does not provide an exported
artifact path, so use an explicit export and upload. Inside the completed Gemini
session, create a supported export:

```text
/chat share /absolute/path/to/gemini-session.json
```

After leaving the session, upload that immutable export:

```bash
python3 /absolute/path/to/ai-memory-hub/tools/import_to_memory.py \
  gemini-cli-session-json /absolute/path/to/gemini-session.json --min-age-seconds 0
```

Older Gemini releases may use `/export-session` instead of `/chat share`. Delete
the exported file when it is no longer needed. There is no persistent automation
to disable in this recipe. See the upstream [Gemini CLI hooks reference](https://geminicli.com/docs/hooks/reference/).

## Hermes Agent

This explicit export-and-upload recipe was checked with Hermes Agent 0.22.5.
Hermes stores current sessions in `~/.hermes/state.db`; never copy that database,
its `-wal` file, or its `-shm` file. Exit or otherwise end the intended session,
find its ID with `hermes sessions list`, then run:

```bash
python3 /absolute/path/to/ai-memory-hub/tools/import_hermes_session.py \
  --session-id <SESSION_ID>
```

Add `--redact` to ask Hermes to scrub recognized API keys, tokens, and
credentials from the exported copy before upload. For an explicit batch of
ended sessions, use an upstream ended-session age filter instead of an
unfiltered database export:

```bash
python3 /absolute/path/to/ai-memory-hub/tools/import_hermes_session.py \
  --ended-older-than 2d --redact
```

The adapter creates a private temporary directory, installs cleanup before it
runs `hermes sessions export`, and deletes the JSONL after success or failure.
It invokes only the supported Hermes export command, suppresses its output, and
then delegates the immutable artifact to the shared uploader with parser
`hermes-session-jsonl` and `--min-age-seconds 0`. The existing importer retains
single- and multi-session boundaries. Missing or invalid IDs, failed or empty
exports, files over 20 MB, invalid envelopes, and network failures are refused
without printing export content or credentials. Safe retries use normal hub
deduplication and emit only the uploader's bounded receipt.

There is nothing persistent to disable or remove: both forms are opt-in manual
commands. Keep `MEMORY_HUB_URL` and `MEMORY_API_TOKEN` only in the command
environment. See the upstream [Hermes session guide](https://github.com/NousResearch/hermes-agent/blob/v0.22.5/website/docs/user-guide/sessions.md).

## Pi

This explicit recipe was checked with Pi coding agent 0.73.0 and its version 3
JSONL session format. Pi does not provide a verified end event that supplies a
closed session path, so this recipe deliberately does not install a watcher or
guess which file is newest.

Before leaving the intended Pi session, run `/session` and copy the exact session
file path that Pi reports. Then exit Pi, wait at least five seconds so the shared
uploader can reject a file that is still active, and run:

```bash
python3 /absolute/path/to/ai-memory-hub/tools/import_pi_session.py \
  /absolute/path/reported-by-pi/completed-session.jsonl
```

The adapter accepts only an explicit absolute `.jsonl` path and delegates it to
the shared uploader with parser `pi-session-jsonl` and source `pi`. It does not
read or reconstruct the conversation. The existing hub importer validates the
version 3 tree and reconstructs its active branch. Missing and ephemeral
sessions, recent or changing files, files over 20 MB, and invalid session trees
are refused by the adapter, uploader, or importer. Retrying the same completed
session is safe because normal hub ingestion deduplicates it.

Do not run the command while Pi still has the session open, use it for Oh My Pi
or OpenClaw artifacts, or replace the reported path with a newest-file search.
There is nothing persistent to disable or remove: this is an opt-in manual
command. Keep `MEMORY_HUB_URL` and `MEMORY_API_TOKEN` only in the command
environment, as described above.

See Pi's upstream [session documentation](https://github.com/pi0/pi-mono/blob/main/packages/coding-agent/docs/sessions.md)
and [versioned session format](https://github.com/pi0/pi-mono/blob/main/packages/coding-agent/docs/session-format.md).

## Failure handling

The command exits nonzero for missing credentials, missing or unsafe files,
network failures, authentication errors, unsupported parser input, and partial
or invalid server responses. Hook failures do not expose conversation content or
the bearer token. A failed upload can be retried after closing/exporting again;
successful retries are safe because hub ingestion is deduplicated.
