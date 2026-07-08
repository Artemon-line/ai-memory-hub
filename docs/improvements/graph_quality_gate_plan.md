# Graph Quality Gate Plan

## Goal

Create a concrete quality gate that must pass before graph-derived memory can
influence retrieval ranking or `memory_ask` answers.

The gate should make entity extraction, relationship extraction, provenance, and
graph-aware retrieval measurable with deterministic local fixtures. This avoids
promoting graph memory based on intuition alone.

## Scope

- [ ] Add a local benchmark module: `memory.benchmarks.graph_quality`.
- [ ] Define a representative golden corpus for graph-memory extraction and
      retrieval behavior.
- [ ] Report entity, relationship, provenance, conflict-detection, and
      graph-retrieval metrics.
- [ ] Fail with explicit threshold messages when graph quality is below the
      promotion bar.
- [ ] Keep graph retrieval disabled until the quality gate passes.

## Non-Goals

- [ ] Do not implement production graph retrieval in this plan.
- [ ] Do not require hosted LLMs or network access.
- [ ] Do not make graph extraction overwrite raw conversations or normalized
      facts.
- [ ] Do not expose graph-derived answers through MCP/API before provenance and
      quality gates are in place.

## Phase 1: Golden Corpus Shape

- [ ] Create a small deterministic corpus of representative conversations.
- [ ] Cover project-memory examples already used by the repo:
      Codex/opencode handoff, PGVector, bearer auth, observability, tokenizer
      setup, retrieval benchmarks, Velvet Lantern, tools, providers, and user
      preferences.
- [ ] For each conversation, define stable conversation IDs and message indexes.
- [ ] For each corpus item, define expected entities:
      `id`, `type`, `name`, `aliases`, `source_conversation_id`,
      `source_message_index`, and optional `source_fact_id`.
- [ ] For each corpus item, define expected relationships:
      `subject`, `predicate`, `object`, `confidence`, `source_conversation_id`,
      `source_message_index`, and optional `source_fact_id`.
- [ ] Include negative examples where text mentions nearby entities but should
      not create a relationship.
- [ ] Include conflict examples where newer memory contradicts older memory.

Acceptance criteria:

- [ ] The corpus is local, deterministic, and small enough for unit tests.
- [ ] Expected entities and relationships are human-readable.
- [ ] Provenance expectations are explicit instead of inferred later.

## Phase 2: Metric Definitions

- [ ] Entity precision: matched expected entities divided by produced entities.
- [ ] Entity recall: matched expected entities divided by expected entities.
- [ ] Entity F1: harmonic mean of entity precision and recall.
- [ ] Relationship precision: matched expected relationships divided by produced
      relationships.
- [ ] Relationship recall: matched expected relationships divided by expected
      relationships.
- [ ] Relationship F1: harmonic mean of relationship precision and recall.
- [ ] Provenance accuracy: matched extracted records with correct source
      provenance divided by matched extracted records.
- [ ] Conflict-detection precision and recall for contradictory relationship or
      fact candidates.
- [ ] Graph retrieval `precision_at_1`, `recall_at_k`, and MRR for relationship
      questions.
- [ ] Baseline comparison against non-graph retrieval for the same graph
      retrieval cases.

Matching policy:

- [ ] Entity matching should normalize case, whitespace, and configured aliases.
- [ ] Relationship matching should require normalized subject, predicate, and
      object match.
- [ ] Provenance matching should require the expected conversation ID and message
      index, with chunk/fact ID checks when available.
- [ ] Extra unsupported entities or relationships count against precision.
- [ ] Missing expected entities or relationships count against recall.

## Phase 3: Benchmark Module

- [ ] Add `memory/benchmarks/graph_quality.py`.
- [ ] Expose `run_evaluation(...) -> dict[str, Any]`.
- [ ] Expose `assert_thresholds(report: dict[str, Any]) -> None`.
- [ ] Add a CLI entrypoint runnable as:
      `uv run python -m memory.benchmarks.graph_quality`.
- [ ] Print JSON with:
      `corpus`, `entity`, `relationship`, `provenance`, `conflict_detection`,
      `graph_retrieval`, `baseline_retrieval`, and `thresholds` sections.
- [ ] Return exit code `1` when threshold failures exist.
- [ ] Keep the benchmark independent of external storage, embeddings, LLMs, and
      network services.

Suggested CLI options:

- [ ] `--top-k`
- [ ] `--min-entity-precision`
- [ ] `--min-entity-recall`
- [ ] `--min-relationship-precision`
- [ ] `--min-relationship-recall`
- [ ] `--min-provenance-accuracy`
- [ ] `--min-graph-precision-at-1`
- [ ] `--min-graph-recall-at-k`
- [ ] `--min-graph-mrr`

## Phase 4: Initial Thresholds

Initial thresholds should be conservative and adjustable as fixtures become more
representative.

- [ ] Entity precision must be at least `0.90`.
- [ ] Entity recall must be at least `0.80`.
- [ ] Relationship precision must be at least `0.95`.
- [ ] Relationship recall must be at least `0.75`.
- [ ] Provenance accuracy must be at least `0.98`.
- [ ] Graph retrieval `precision_at_1` must be at least `0.85`.
- [ ] Graph retrieval `recall_at_k` must be at least `0.90`.
- [ ] Graph retrieval MRR must be at least `0.85`.
- [ ] Graph retrieval must not regress below baseline retrieval on ordinary
      semantic queries.
- [ ] Every threshold failure must include the metric name, observed value, and
      expected value.

## Phase 5: Tests

- [ ] Add `tests/unit/test_graph_quality_benchmark.py`.
- [ ] Test report shape and required metric sections.
- [ ] Test invalid threshold arguments.
- [ ] Test explicit threshold failure messages.
- [ ] Test entity precision/recall/F1 calculations.
- [ ] Test relationship precision/recall/F1 calculations.
- [ ] Test provenance accuracy calculation.
- [ ] Test graph retrieval regression detection against baseline retrieval.
- [ ] Test CLI exit code behavior for passing and failing thresholds.

## Phase 6: Promotion Rule

- [ ] Graph extraction may be developed before the gate passes, but graph-derived
      records must remain review-only or disabled for retrieval.
- [ ] Graph lookup may become a retrieval candidate source only after the graph
      quality benchmark passes agreed thresholds.
- [ ] Any new entity type, relationship predicate, extractor, or graph reranking
      rule must add or update benchmark cases.
- [ ] Public API/MCP graph fields must stay experimental until the benchmark and
      shape tests cover them.

## Done When

- [ ] `memory.benchmarks.graph_quality` exists and runs locally without network
      dependencies.
- [ ] The benchmark reports entity, relationship, provenance, conflict, and
      graph-retrieval metrics.
- [ ] Threshold failures are explicit and actionable.
- [ ] Unit tests cover metric calculations and threshold failures.
- [ ] `docs/improvements/advanced_memory_plan.md` points to this gate before
      graph-aware retrieval.
