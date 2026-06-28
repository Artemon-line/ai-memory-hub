# CLI Implementation Plan

## Goal

Make ai-memory-hub usable from the command line for local ingestion, retrieval, search, ask-over-memory, and diagnostics without requiring users to run HTTP or MCP clients directly.

## Current Status

Implemented:

- [x] Basic argparse entrypoint in `memory/cli.py`.
- [x] `python -m memory.cli tokenizer-check`.
- [x] `tokenizer-check --config`.
- [x] `tokenizer-check --encoding`.
- [x] `tokenizer-check --json`.
- [x] Unit tests for tokenizer diagnostic CLI output.

Implemented since the initial tokenizer diagnostic baseline:

- [x] Distributable console script name.
- [x] Global CLI options shared by all commands.
- [x] `ingest <file>`.
- [x] `search "<query>"`.
- [x] `retrieve <id>`.
- [x] `ask "<question>"`.
- [x] `serve`.
- [x] Storage/runtime initialization that matches API/MCP behavior.
- [x] End-to-end CLI smoke tests with temporary storage.

## Command Namespace

Use `python -m memory.cli` as the guaranteed development entrypoint.

The packaged console script name is:

- `aim`

Historical fallback if the name ever becomes unavailable or too ambiguous:

- `ai-memory-hub`

Packaging metadata exposes `aim`; `python -m memory.cli` remains the guaranteed
development entrypoint.

## Global Options

All production commands should support:

- `--config <path>` to load a non-default config file.
- `--json` for machine-readable output.
- `--quiet` to suppress non-essential text output.
- `--verbose` for diagnostics.

Command failures should use stable exit codes:

- `0`: success.
- `1`: expected command failure, such as validation failure or not found.
- `2`: argument parsing or usage error.
- `3`: runtime initialization failure.

JSON output should keep the same envelope style as API/MCP where practical:

- `status`
- `id`
- `results`
- `error_code`
- `error_message`

## Phase 1: CLI Foundation

- [x] Add basic argument parsing in `memory/cli.py`.
- [x] Add a diagnostic command that does not require storage initialization.
- [x] Add shared helpers for loading config and formatting text/JSON output.
- [x] Add stable error handling for `ValueError`, schema validation errors, and runtime initialization failures.
- [x] Add packaging metadata for the selected console script name.

Acceptance criteria:

- [x] `python -m memory.cli --help` shows all supported commands.
- [x] The selected console script runs the same parser as `python -m memory.cli`.
- [x] JSON output is deterministic and testable.
- [x] Error responses have predictable exit codes.

## Phase 2: Ingest Command

Command shape:

```bash
python -m memory.cli ingest conversation.json --json
```

Scope:

- [x] Read a conversation payload from a JSON file.
- [x] Support `-` for stdin.
- [x] Normalize through the same ingestion path used by API/MCP.
- [x] Validate against the configured schema before storage.
- [x] Store metadata and vector chunks using configured providers.
- [x] Return the inserted `id`, chunk count, and status.

Acceptance criteria:

- [x] Valid conversation JSON is stored and returns `status=ok`.
- [x] Invalid JSON returns a stable validation error.
- [x] Invalid schema returns a stable validation error.
- [x] Duplicate/conflicting IDs follow existing ingestion semantics.
- [x] `--json` output is suitable for scripts.

## Phase 3: Search Command

Command shape:

```bash
python -m memory.cli search "gpu plan" --top-k 5 --json
```

Scope:

- [x] Use the same runtime search path as API/MCP.
- [x] Support `--top-k`.
- [x] Support `--source`, `--date-from`, `--date-to`, and `--tags` only if the CLI can preserve the same semantics as MCP search.
- [x] Print compact text output by default.
- [x] Return full structured results with `--json`.

Acceptance criteria:

- [x] Search returns deterministic result fields.
- [x] Empty search results return `status=ok` and `results=[]`.
- [x] Invalid `top_k` returns a usage or validation error consistently.
- [x] Text output is readable without losing IDs.

## Phase 4: Retrieve And Ask Commands

Command shapes:

```bash
python -m memory.cli retrieve <memory_id> --json
python -m memory.cli ask "what did I store about GPUs?" --top-k 5 --json
```

Scope:

- [x] Retrieve stored conversations by ID.
- [x] Ask questions using the same `memory_ask` pipeline as API/MCP.
- [x] Support `--max-context-tokens` on `ask`.
- [x] Return citations in JSON output.
- [x] Keep text output concise and cite memory IDs.

Acceptance criteria:

- [x] Missing IDs return a stable not-found result.
- [x] `ask` citations reflect included context.
- [x] `ask --max-context-tokens` respects the configured budget behavior.
- [x] Existing API/MCP ask tests remain compatible.

## Phase 5: Diagnostics

Implemented diagnostics:

- [x] `tokenizer-check`

- [x] `health` for storage/provider initialization.
- [x] `config-show` for normalized configuration with secrets redacted.
- [x] `storage-check` for metadata/vector store capability checks.

Acceptance criteria:

- [x] Diagnostics avoid writing to storage unless explicitly documented.
- [x] Diagnostics redact secrets and sensitive hashes.
- [x] Diagnostics return JSON that can be consumed in CI.

## Phase 6: Serve Command

Command shape:

```bash
python -m memory.cli serve --host 127.0.0.1 --port 8000
```

Packaged command shape:

```bash
aim serve --host 127.0.0.1 --port 8000
```

Scope:

- [x] Start the same FastAPI/MCP application currently launched with `uvicorn memory.api.server:app`.
- [x] Support `--host`.
- [x] Support `--port`.
- [x] Support `--config <path>`.
- [x] Preserve the same API and MCP routes.
- [x] Use stable exit code `3` for runtime initialization failures.
- [x] Redact secrets in startup diagnostics.
- [x] Become the preferred `Containerfile` command after the console script is packaged.

Acceptance criteria:

- [x] `python -m memory.cli serve --host 127.0.0.1 --port 8000` starts the service.
- [x] `aim serve --host 127.0.0.1 --port 8000` works after packaging metadata is added.
- [x] Container smoke tests use the CLI entrypoint instead of invoking `uvicorn` directly.
- [x] README documents the CLI serve command once implemented.

## Testing

Unit tests:

- [x] `tokenizer-check` JSON output.
- [x] `tokenizer-check` encoding override.
- [x] Parser coverage for every command and global option.
- [x] Text and JSON formatting coverage.
- [x] Exit-code behavior for validation, not-found, and runtime errors.

Integration tests:

- [x] `ingest` stores a temporary conversation.
- [x] `search` finds the ingested conversation.
- [x] `retrieve` returns the ingested conversation.
- [x] `ask` returns citations for the ingested conversation.
- [x] `serve` starts the API/MCP app in test coverage.
- [x] Temporary storage paths prevent writes to user data during tests.

Regression tests:

- [x] CLI and API/MCP result envelopes stay aligned where they expose the same operation.
- [x] CLI commands do not leak internal hash fields in user-facing output.

## Done When

- [x] `python -m memory.cli ingest <file>` stores valid conversations.
- [x] `python -m memory.cli search "<query>"` returns structured results.
- [x] `python -m memory.cli retrieve <id>` returns stored conversations.
- [x] `python -m memory.cli ask "<question>"` returns answer and citations.
- [x] `python -m memory.cli serve` starts the API/MCP app.
- [x] CLI output is script-friendly with `--json`.
- [x] CLI commands have stable exit codes.
- [x] README documents the supported CLI commands without assuming unimplemented console scripts.
