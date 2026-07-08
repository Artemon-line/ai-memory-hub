# Graph Quality Gate Plan

## Goal

Create a concrete quality gate that must pass before graph-derived memory can
influence retrieval ranking or `memory_ask` answers.

The gate should make entity extraction, relationship extraction, provenance, and
graph-aware retrieval measurable with deterministic local fixtures. This avoids
promoting graph memory based on intuition alone.

## Scope

- [x] Add a local benchmark module: `memory.benchmarks.graph_quality`.
- [x] Define a representative golden corpus for graph-memory extraction and
      retrieval behavior.
- [x] Report entity, relationship, provenance, conflict-detection, and
      graph-retrieval metrics.
- [x] Fail with explicit threshold messages when graph quality is below the
      promotion bar.
- [x] Keep graph retrieval disabled until the quality gate passes.

## Non-Goals

- [ ] Do not implement production graph retrieval in this plan.
- [ ] Do not require hosted LLMs or network access.
- [ ] Do not make graph extraction overwrite raw conversations or normalized
      facts.
- [ ] Do not expose graph-derived answers through MCP/API before provenance and
      quality gates are in place.

## Phase 1: Golden Corpus Shape

- [x] Create a small deterministic corpus of representative conversations.
- [x] Cover project-memory examples already used by the repo:
      Codex/opencode handoff, PGVector, bearer auth, observability, tokenizer
      setup, retrieval benchmarks, Velvet Lantern, tools, providers, and user
      preferences.
- [x] For each conversation, define stable conversation IDs and message indexes.
- [x] For each corpus item, define expected entities:
      `id`, `type`, `name`, `aliases`, `source_conversation_id`,
      `source_message_index`, and optional `source_fact_id`.
- [x] For each corpus item, define expected relationships:
      `subject`, `predicate`, `object`, `confidence`, `source_conversation_id`,
      `source_message_index`, and optional `source_fact_id`.
- [x] Include negative examples where text mentions nearby entities but should
      not create a relationship.
- [x] Include conflict examples where newer memory contradicts older memory.

Acceptance criteria:

- [x] The corpus is local, deterministic, and small enough for unit tests.
- [x] Expected entities and relationships are human-readable.
- [x] Provenance expectations are explicit instead of inferred later.

## Phase 2: Metric Definitions

- [x] Entity precision: matched expected entities divided by produced entities.
- [x] Entity recall: matched expected entities divided by expected entities.
- [x] Entity F1: harmonic mean of entity precision and recall.
- [x] Relationship precision: matched expected relationships divided by produced
      relationships.
- [x] Relationship recall: matched expected relationships divided by expected
      relationships.
- [x] Relationship F1: harmonic mean of relationship precision and recall.
- [x] Provenance accuracy: matched extracted records with correct source
      provenance divided by matched extracted records.
- [x] Conflict-detection precision and recall for contradictory relationship or
      fact candidates.
- [x] Graph retrieval `precision_at_1`, `recall_at_k`, and MRR for relationship
      questions.
- [x] Baseline comparison against non-graph retrieval for the same graph
      retrieval cases.

Matching policy:

- [x] Entity matching should normalize case, whitespace, and configured aliases.
- [x] Relationship matching should require normalized subject, predicate, and
      object match.
- [x] Provenance matching should require the expected conversation ID and message
      index, with chunk/fact ID checks when available.
- [x] Extra unsupported entities or relationships count against precision.
- [x] Missing expected entities or relationships count against recall.

## Phase 3: Benchmark Module

- [x] Add `memory/benchmarks/graph_quality.py`.
- [x] Expose `run_evaluation(...) -> dict[str, Any]`.
- [x] Expose `assert_thresholds(report: dict[str, Any]) -> None`.
- [x] Add a CLI entrypoint runnable as:
      `uv run python -m memory.benchmarks.graph_quality`.
- [x] Print JSON with:
      `corpus`, `entity`, `relationship`, `provenance`, `conflict_detection`,
      `graph_retrieval`, `baseline_retrieval`, and `thresholds` sections.
- [x] Return exit code `1` when threshold failures exist.
- [x] Keep the benchmark independent of external storage, embeddings, LLMs, and
      network services.

Suggested CLI options:

- [x] `--top-k`
- [x] `--min-entity-precision`
- [x] `--min-entity-recall`
- [x] `--min-relationship-precision`
- [x] `--min-relationship-recall`
- [x] `--min-provenance-accuracy`
- [x] `--min-graph-precision-at-1`
- [x] `--min-graph-recall-at-k`
- [x] `--min-graph-mrr`

## Phase 4: Initial Thresholds

Initial thresholds should be conservative and adjustable as fixtures become more
representative.

- [x] Entity precision must be at least `0.90`.
- [x] Entity recall must be at least `0.80`.
- [x] Relationship precision must be at least `0.95`.
- [x] Relationship recall must be at least `0.75`.
- [x] Provenance accuracy must be at least `0.98`.
- [x] Graph retrieval `precision_at_1` must be at least `0.85`.
- [x] Graph retrieval `recall_at_k` must be at least `0.90`.
- [x] Graph retrieval MRR must be at least `0.85`.
- [x] Graph retrieval must not regress below baseline retrieval on ordinary
      semantic queries.
- [x] Every threshold failure must include the metric name, observed value, and
      expected value.

## Phase 5: Tests

- [x] Add `tests/unit/test_graph_quality_benchmark.py`.
- [x] Test report shape and required metric sections.
- [x] Test invalid threshold arguments.
- [x] Test explicit threshold failure messages.
- [x] Test entity precision/recall/F1 calculations.
- [x] Test relationship precision/recall/F1 calculations.
- [x] Test provenance accuracy calculation.
- [x] Test graph retrieval regression detection against baseline retrieval.
- [x] Test CLI exit code behavior for passing and failing thresholds.

## Phase 6: Promotion Rule

- [x] Graph extraction may be developed before the gate passes, but graph-derived
      records must remain review-only or disabled for retrieval.
- [x] Graph lookup may become a retrieval candidate source only after the graph
      quality benchmark passes agreed thresholds.
- [x] Any new entity type, relationship predicate, extractor, or graph reranking
      rule must add or update benchmark cases.
- [x] Public API/MCP graph fields must stay experimental until the benchmark and
      shape tests cover them.

## Done When

- [x] `memory.benchmarks.graph_quality` exists and runs locally without network
      dependencies.
- [x] The benchmark reports entity, relationship, provenance, conflict, and
      graph-retrieval metrics.
- [x] Threshold failures are explicit and actionable.
- [x] Unit tests cover metric calculations and threshold failures.
- [x] `docs/improvements/advanced_memory_plan.md` points to this gate before
      graph-aware retrieval.
