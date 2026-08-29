# Codex CLI Findings QA Result - 2026-08-29

Branch: `codex/cli-findings-plan`

Compared with: `AMH-QA-20260828-RESULTS`

## Automated Fixture Result

Local regression coverage now represents the seven product findings from the
manual Codex CLI MCP run:

- `memory_ask` synthesis and confidence for direct preference recall.
- Simple `I like ...` preference fact extraction and profile reads.
- Exact deduplication.
- Explicit correction supersession plus ambiguous-conflict behavior.
- Source, explicit tags, dates, `thread_id`, `project_id`, cursor pagination,
  and `result_mode="threads"` filters.
- Pending, quarantined, and rejected review-state reads through seeded storage.
- Tiny `max_context_tokens` answers lowering confidence when evidence is
  truncated below a useful threshold.

Validation evidence:

- `uv run pytest tests/integration/test_codex_cli_mcp_findings.py tests/unit/test_mvp_ask_budget.py tests/unit/test_mvp_ingestion.py tests/unit/test_mcp_tools.py tests/integration/test_payload_validation_edges.py -q`
  passed with `135 passed`.
- The Phase 4 commit hook passed with `629 passed, 18 skipped`.

## Real Codex CLI Smoke Attempt

Codex CLI installed locally:

- `codex --help` reports `codex exec` for non-interactive runs.
- `codex exec --help` reports `--ephemeral`, `--sandbox`, `-C`, and
  `--skip-git-repo-check`.

Harness compatibility updates made during the probe:

- Codex temporary provider config now uses `wire_api = "responses"`.
- The deterministic gateway can stream Responses API events and terminates with
  `response.completed`.
- The smoke prompt and verification include `memory_fact_search` so the client
  path covers fact inspection, not only validate/insert/search/retrieve/ask.

Latest configured command template tested:

```bash
export AMH_REAL_CLIENT_CODEX_COMMAND='codex exec --ephemeral --sandbox read-only --skip-git-repo-check -C "{artifact_dir}" "{prompt}"'
uv run python -m memory.tools.real_client_smoke \
  --client codex \
  --require-success-for codex \
  --artifact-dir /tmp/amh-real-client-smoke-codex \
  --client-timeout 180
```

Observed result with Codex CLI v0.147.0 and the local deterministic provider:

- Codex starts, loads the temporary `CODEX_HOME`, connects to the local
  Responses gateway, and discovers the ai-memory-hub MCP server.
- Calls emitted as `mcp__ai_memory_hub.memory_validate`,
  `mcp__ai_memory_hub.memory_insert`, `mcp__ai_memory_hub.memory_search`,
  `mcp__ai_memory_hub.memory_retrieve`, `mcp__ai_memory_hub.memory_ask`, and
  `mcp__ai_memory_hub.memory_fact_search` are rejected by Codex with
  `unsupported call`.
- ai-memory-hub never receives the intended insert, so direct verification fails
  with `direct search did not find smoke marker`.

## Comparison

The 2026-08-28 mixed/failing product observations now have local automated
coverage and fixes or documented product decisions. The follow-up real Codex CLI
run remains blocked by the client/local-provider MCP dispatch path, not by the
hub's MCP server or product behavior.

## Next Unblocker

Keep the Codex slot skip-safe until a Codex CLI release dispatches MCP namespace
calls from custom Responses providers, or until the project intentionally adds a
credentialed native-provider validation lane for real Codex CLI MCP runs.
