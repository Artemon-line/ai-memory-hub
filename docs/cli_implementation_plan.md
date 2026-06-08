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

Not implemented yet:

- [ ] Distributable console script name.
- [ ] Global CLI options shared by all commands.
- [ ] `ingest <file>`.
- [ ] `search "<query>"`.
- [ ] `retrieve <id>`.
- [ ] `ask "<question>"`.
- [ ] Storage/runtime initialization that matches API/MCP behavior.
- [ ] End-to-end CLI smoke tests with temporary storage.

## Command Namespace

Use `python -m memory.cli` as the guaranteed development entrypoint.

Add a console script only after the command contract stabilizes. Preferred final command name:

- `aim`

Fallback if the name is unavailable or too ambiguous:

- `ai-memory-hub`

Do not document a console script as the primary path until packaging metadata exposes it.

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
- [ ] Add shared helpers for loading config and formatting text/JSON output.
- [ ] Add stable error handling for `ValueError`, schema validation errors, and runtime initialization failures.
- [ ] Add packaging metadata for the selected console script name.

Acceptance criteria:

- [ ] `python -m memory.cli --help` shows all supported commands.
- [ ] The selected console script runs the same parser as `python -m memory.cli`.
- [ ] JSON output is deterministic and testable.
- [ ] Error responses have predictable exit codes.

## Phase 2: Ingest Command

Command shape:

```bash
python -m memory.cli ingest conversation.json --json
```

Scope:

- [ ] Read a conversation payload from a JSON file.
- [ ] Support `-` for stdin.
- [ ] Normalize through the same ingestion path used by API/MCP.
- [ ] Validate against the configured schema before storage.
- [ ] Store metadata and vector chunks using configured providers.
- [ ] Return the inserted `id`, chunk count, and status.

Acceptance criteria:

- [ ] Valid conversation JSON is stored and returns `status=ok`.
- [ ] Invalid JSON returns a stable validation error.
- [ ] Invalid schema returns a stable validation error.
- [ ] Duplicate/conflicting IDs follow existing ingestion semantics.
- [ ] `--json` output is suitable for scripts.

## Phase 3: Search Command

Command shape:

```bash
python -m memory.cli search "gpu plan" --top-k 5 --json
```

Scope:

- [ ] Use the same runtime search path as API/MCP.
- [ ] Support `--top-k`.
- [ ] Support `--source`, `--date-from`, `--date-to`, and `--tags` only if the CLI can preserve the same semantics as MCP search.
- [ ] Print compact text output by default.
- [ ] Return full structured results with `--json`.

Acceptance criteria:

- [ ] Search returns deterministic result fields.
- [ ] Empty search results return `status=ok` and `results=[]`.
- [ ] Invalid `top_k` returns a usage or validation error consistently.
- [ ] Text output is readable without losing IDs.

## Phase 4: Retrieve And Ask Commands

Command shapes:

```bash
python -m memory.cli retrieve <memory_id> --json
python -m memory.cli ask "what did I store about GPUs?" --top-k 5 --json
```

Scope:

- [ ] Retrieve stored conversations by ID.
- [ ] Ask questions using the same `memory_ask` pipeline as API/MCP.
- [ ] Support `--max-context-tokens` on `ask`.
- [ ] Return citations in JSON output.
- [ ] Keep text output concise and cite memory IDs.

Acceptance criteria:

- [ ] Missing IDs return a stable not-found result.
- [ ] `ask` citations reflect included context.
- [ ] `ask --max-context-tokens` respects the configured budget behavior.
- [ ] Existing API/MCP ask tests remain compatible.

## Phase 5: Diagnostics

Implemented diagnostics:

- [x] `tokenizer-check`

Planned diagnostics:

- [ ] `health` for storage/provider initialization.
- [ ] `config-show` for normalized configuration with secrets redacted.
- [ ] `storage-check` for metadata/vector store capability checks.

Acceptance criteria:

- [ ] Diagnostics avoid writing to storage unless explicitly documented.
- [ ] Diagnostics redact secrets and sensitive hashes.
- [ ] Diagnostics return JSON that can be consumed in CI.

## Testing

Unit tests:

- [x] `tokenizer-check` JSON output.
- [x] `tokenizer-check` encoding override.
- [ ] Parser coverage for every command and global option.
- [ ] Text and JSON formatting coverage.
- [ ] Exit-code behavior for validation, not-found, and runtime errors.

Integration tests:

- [ ] `ingest` stores a temporary conversation.
- [ ] `search` finds the ingested conversation.
- [ ] `retrieve` returns the ingested conversation.
- [ ] `ask` returns citations for the ingested conversation.
- [ ] Temporary storage paths prevent writes to user data during tests.

Regression tests:

- [ ] CLI and API/MCP result envelopes stay aligned where they expose the same operation.
- [ ] CLI commands do not leak internal hash fields in user-facing output.

## Done When

- [ ] `python -m memory.cli ingest <file>` stores valid conversations.
- [ ] `python -m memory.cli search "<query>"` returns structured results.
- [ ] `python -m memory.cli retrieve <id>` returns stored conversations.
- [ ] `python -m memory.cli ask "<question>"` returns answer and citations.
- [ ] CLI output is script-friendly with `--json`.
- [ ] CLI commands have stable exit codes.
- [ ] README documents the supported CLI commands without assuming unimplemented console scripts.
