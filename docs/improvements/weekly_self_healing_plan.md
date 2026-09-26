# Weekly Self-Healing GitHub Job Plan

## Summary

Add a scheduled GitHub Actions workflow that detects routine repository drift,
applies narrowly allowlisted repairs, validates them, and opens a pull request
for human review. It must never push directly to the default branch, merge its
own pull request, modify secrets, or send conversation data to third parties.

## Goals

- Run the repository's normal lint, type, unit, integration, documentation, and
  fixture privacy checks once per week.
- Repair deterministic maintenance issues such as formatter output, generated
  documentation indexes, and approved dependency-lock refreshes.
- Open or update one weekly pull request containing only validated changes.
- Produce useful logs and artifacts when repair is unsafe or unsuccessful.

## Non-Goals

- Generating product behavior changes with an unconstrained AI agent.
- Automatically changing authentication, authorization, storage, ingestion, or
  privacy semantics.
- Uploading private exports, runtime memory stores, logs containing conversation
  text, credentials, embeddings, or user queries.
- Automatically approving or merging changes.

## Proposed Workflow

Create `.github/workflows/weekly-self-healing.yml` with both `schedule` and
`workflow_dispatch` triggers. Use a concurrency group so only one weekly run is
active. Start with read-only repository permissions, then grant only
`contents: write` and `pull-requests: write` to the final PR-creation job.

The workflow should:

1. Check out the default branch with pinned action SHAs and no persisted GitHub
   credentials in validation jobs.
2. Install the repository's pinned Python and `uv` environment.
3. Run deterministic checks before making changes:

   ```bash
   uv run python -m ruff check memory tests tools
   uv run python -m pyright
   uv run pytest tests/unit tests/integration -q
   uv run python tools/prepare_mkdocs.py
   uv run python -m mkdocs build --strict
   ```

4. Run an allowlisted repair script owned by the repository. Initially permit
   only `ruff format`, generated documentation preparation, and explicitly
   approved lockfile refresh commands. Reject changes outside the allowlist.
5. Re-run all affected checks. If any fail, upload the patch and diagnostics as
   artifacts and stop without creating a pull request.
6. Scan the patch for secrets and private conversation markers. Also reject new
   files under runtime data directories or fixture files not explicitly marked
   synthetic.
7. If the validated patch is non-empty, create or update a branch named
   `automation/weekly-self-healing` and open one pull request using a stable
   title. Include the failed check, repair commands, validation results, and
   changed-file allowlist in the body.

## Guardrails

- Pin third-party actions by full commit SHA and review updates through
  Dependabot.
- Do not expose repository secrets to forked pull requests or repair commands.
- Set a short timeout and cap generated patch size and changed-file count.
- Fail closed when an unexpected file, binary, workflow, security policy, or
  dependency manifest changes.
- Require normal branch protection, CODEOWNERS, and CI before merge.
- Use a repository-owned script for repair logic so behavior is reviewable and
  locally reproducible.
- Record the triggering commit SHA and tool versions in the pull request.

## Rollout

### Phase 1: Observe

Run weekly checks and upload diagnostics only. Confirm runtime, false-positive
rate, and privacy behavior for at least two scheduled runs.

### Phase 2: Deterministic Repair

Enable formatting and generated-doc repairs. Open pull requests but do not
modify dependencies.

### Phase 3: Controlled Maintenance

Optionally add approved lockfile refreshes after supply-chain review. Keep
security-sensitive and semantic code changes outside automatic repair scope.

## Acceptance Criteria

- A manual dispatch can reproduce a scheduled run.
- A clean repository produces no branch or pull request.
- A seeded formatting or generated-doc drift produces one validated pull
  request containing only allowlisted files.
- Failed validation produces diagnostics but no repository write.
- Secret scanning and synthetic-fixture checks block unsafe patches.
- The workflow cannot push to or merge into the default branch.
