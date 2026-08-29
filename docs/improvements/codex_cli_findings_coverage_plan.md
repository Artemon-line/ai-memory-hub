# Codex CLI MCP Findings Coverage Plan

Source date: `2026-08-28`

Status: in progress

Priority: P0 before claiming stable Codex CLI support

## Goal

Turn the latest real Codex CLI MCP findings into fixture-backed fixes,
regression coverage, and release criteria for ai-memory-hub.

This plan is intentionally about behavior seen through an MCP client, not only
unit-level implementation details. The hub should remain easy for Codex CLI and
other agents to use without requiring clients to re-interpret raw retrieval
evidence or compensate for memory-quality gaps.

## Source Evidence

Primary saved QA result:

- Memory `a5858840-3de5-4194-bf37-16f57dd3a353`
- Source `mcp`
- Thread `AMH-QA-20260828-RESULTS`
- Stored at `2026-08-28T14:23:49Z`

Supporting QA fixtures:

| Area | Fixture | Thread | Observation |
| --- | --- | --- | --- |
| Ask correctness | `e733588e-d728-468d-8801-7c7d60ea4138` | `AMH-QA-20260828-ASK-01` | Filtered, typo-heavy recall found the right chunks, but `memory_ask` behaved more like evidence retrieval than a clean synthesized answer. |
| Fact extraction | `2a79d7de-cc77-4a3a-ac27-6c70d5a0b0ab` | `AMH-QA-20260828-FACT-01` | Search found "I like potato chips", but fact/profile reads did not create a clean user preference fact and only surfaced recurring-topic style facts. |
| Correction | `33d79ee0-ce14-4a32-82b1-7e7406ffc999` | `AMH-QA-20260828-CORR` | The correction from QA avocado toast to QA pizza slice left two active favorite-food facts instead of superseding the older one. |
| Filters and pagination | `1c820292-cf4f-4ba1-bcab-66fdf5f915f0` | `AMH-QA-20260828-FILTER-THREAD` | Source, explicit tags, dates, `thread_id`, cursor pagination, project, status, and thread result mode mostly worked. Topic-derived tags did not behave like explicit metadata tags. |

These IDs are reference evidence only. Automated tests should recreate local
fixtures and must not depend on a developer's private memory hub.

## Current Findings

| Area | Latest result | Coverage direction |
| --- | --- | --- |
| `memory_ask` synthesis | Mixed. Retrieval and filters worked, but the answer shape pushed too much evidence-parsing work onto the agent. | Add tests that assert direct questions receive a concise synthesized answer plus aligned evidence. |
| Preference fact extraction | Failing for simple "I like ..." statements. | Extend deterministic or assisted extraction so common user preference language becomes profile/fact data. |
| Deduplication | Passing. Exact reinsertion returned the same ID with `deduplicated=true`. | Preserve this behavior as a regression while changing the other paths. |
| Correction supersession | Mixed/failing. New correction facts did not supersede older active facts. | Improve correction detection and fact supersession, or route ambiguous corrections into explicit review. |
| Filters and pagination | Mostly passing. Explicit metadata filters worked; auto/topic tag semantics were less clear. | Lock down explicit tag behavior and document whether topic-derived tags are filterable. |
| Review states | Not fully testable from the normal MCP client flow. | Add seeded or admin-scope tests for pending, quarantined, and rejected states without exposing unsafe general write controls. |
| Output budget | Mixed/failing. A tiny `max_context_tokens` value truncated evidence to a fragment while confidence stayed high. | Tie confidence and `confidence_reason` to usable context, not only pre-truncation retrieval strength. |

## Phase 1: Reproduce As Local Regression Fixtures

- [x] Add a focused regression test module for the Codex CLI findings.
- [x] Recreate the ask, preference, correction, filter, deduplication, review-state, and tight-budget fixtures using local test storage.
- [x] Exercise the MCP-facing request shapes where possible, including
      `response_format="concise"` and `response_format="detailed"`.
- [x] Keep private hub IDs only in documentation and test comments when useful;
      generated test IDs should be local and disposable.
- [x] Assert no test output logs raw conversation payloads beyond intentional
      fixture text.

Acceptance criteria:

- The test suite can reproduce every mixed or failing result without relying on
  a private memory project.
- Passing behavior from the Codex CLI run, especially deduplication and explicit
  filters, is locked down before fixes are attempted.
- The tests clearly separate product regressions from client setup failures.

## Phase 2: Improve `memory_ask` Synthesis And Confidence

- [x] Make direct preference questions return a synthesized answer in concise
      mode, not only evidence chunks.
- [x] Keep structured evidence available so agents can cite or inspect the
      basis for the answer.
- [x] Ensure citations and `results` align with the evidence actually used in
      the answer.
- [x] Treat typo-heavy wording as a retrieval quality input, while keeping the
      final answer clean and human-readable.
- [x] Lower confidence or explain uncertainty when answer synthesis cannot use
      enough intact context.

Acceptance criteria:

- The ask fixture answers that the user likes kettle cooked potato chips,
  especially sea salt flavor.
- Concise `memory_ask` remains small enough for agent recall and does not return
  full conversations.
- `confidence`, `confidence_reason`, and `answer_basis` match the usable answer
  context rather than only the initial retrieval hit.


## Phase 3: Fix Correction And Supersession Behavior

- [x] Detect explicit correction language that replaces an older preference or
      favorite-food fact with a newer one.
- [x] Supersede older facts when the subject, predicate family, and corrected
      object are clear.
- [x] Keep ambiguous corrections active as conflicts only when the replacement
      relationship is not safe to infer.
- [x] Add tests for "actually X, not Y" and "correction: X replaces Y" wording.
- [x] Verify detailed reads preserve the audit trail for superseded facts.

Acceptance criteria:

- The correction fixture leaves QA pizza slice active and marks QA avocado
  toast superseded or otherwise inactive for default profile answers.
- `memory_ask` answers with the corrected value for normal reads.
- Detailed or audit reads can still explain the correction history.

## Phase 4: Harden Filters, Review States, And Tight Budgets

- [x] Document the difference between explicit `metadata.tags` and generated
      `auto_tags`.
- [x] Decide whether topic-derived tags should be filterable through `tags`;
      either implement it consistently or document that tag filters only match
      explicit metadata tags.
- [x] Add regression coverage for `source`, `tags`, `date_from`, `date_to`,
      `thread_id`, `project_id`, `memory_status`, cursor pagination, and
      `result_mode="threads"`.
- [x] Add seeded storage or protected admin/test-path coverage for
      `pending_review`, `quarantined`, and `rejected` reads.
- [x] Make extremely small `max_context_tokens` values produce cautious answers
      when evidence is truncated below a useful threshold.

Acceptance criteria:

- Filter behavior is predictable and documented for both explicit and generated
  tags.
- Review-state reads are tested without adding a broad unsafe MCP state-forcing
  operation.
- Tight-budget answers never claim high confidence from unusably truncated
  evidence.

## Phase 5: Re-Run Through Codex CLI

- [ ] Update the real-client smoke runbook once the known-good Codex CLI command
      template is confirmed.
- [ ] Re-run the seven prioritized checks through Codex CLI after the fixes.
- [ ] Save a new QA result summary with fixture IDs and compare it with
      `AMH-QA-20260828-RESULTS`.
- [ ] Promote the Codex CLI slot from skip-safe to required only after the
      native command template and local-provider setup are stable.

Acceptance criteria:

- Codex CLI can validate, insert, search, retrieve, ask, and inspect facts
  without manual compensation by the agent.
- The old mixed/failing observations have either passing evidence or explicitly
  documented product decisions.
- The real-client smoke plan points to this coverage plan for product-quality
  regressions discovered by Codex CLI.

## Done When

- Codex CLI findings are represented by local automated tests.
- `memory_ask` returns cleaner synthesized concise answers for direct recall.
- Simple preferences are extracted into fact/profile reads.
- Clear corrections supersede older facts while preserving audit history.
- Explicit filters, review states, pagination, and tiny token budgets have
  documented and tested behavior.
- A follow-up Codex CLI run shows the latest result is no longer mixed/failing
  for the prioritized findings.
