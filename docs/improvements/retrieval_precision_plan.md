# Retrieval Precision Plan

Source date: `28-05-2026`

## Goal

Improve retrieval precision by tightening candidate selection and ranking before results are exposed to `memory_ask`.

## Scope

- Add a similarity threshold.
- Add hybrid search that combines keyword and vector signals.
- Add metadata-aware reranking.
- Treat these as one implementation stream because they all affect candidate scoring and ordering.

## Phases

### Phase 1: Similarity threshold

- [x] Filter out low-confidence matches before they reach the final candidate set.
- [x] Establish a minimum score policy that is conservative enough to suppress obvious noise.

Acceptance criteria:

- [x] Irrelevant matches such as generic low-signal text are excluded.
- [x] Relevant matches still pass through at normal query thresholds.
- [x] Threshold behavior is deterministic and test-covered.

### Phase 2: Hybrid search

- [x] Combine keyword and vector retrieval signals.
- [x] Keep vector recall, but use keyword matches to recover exact-term relevance.
- [x] Define how scores are merged so the ranking remains explainable.

Acceptance criteria:

- [x] Exact-term queries benefit from keyword matching.
- [x] Semantic queries still work through vector similarity.
- [x] The merged ranking is stable across repeated queries.

### Phase 3: Metadata-aware reranking

- [x] Boost items whose metadata matches the query intent.
- [x] Use metadata such as tags or source context to improve ranking.
- [x] Apply reranking after the candidate set is assembled.

Acceptance criteria:

- [x] Queries like `GPU` prefer GPU-tagged memories when available.
- [x] Metadata does not override obviously better semantic matches without justification.
- [x] Reranking remains predictable and testable.

### Phase 4: Integration tuning

- [x] Reconcile the threshold, hybrid scoring, and reranking behavior together.
- [x] Verify the final pipeline still returns a sane top-k list for `memory_ask`.
- Adjust defaults only if the combined effect improves precision without damaging recall.

Acceptance criteria:

- The final ranked list is better than the baseline on representative queries.
- Precision improvements do not introduce major regressions in recall.
- [x] The pipeline behaves consistently for both short and noisy queries.

## Testing

- [x] Unit tests for thresholding behavior.
- [x] Retrieval tests for keyword + vector blending.
- [x] Metadata reranking tests using tagged examples.
- [x] Regression tests to ensure the ranking order is stable for fixed fixtures.

## Done When

- [x] Low-signal matches are filtered out.
- [x] Keyword and vector search work together.
- [x] Metadata can influence ranking in a controlled way.
- Retrieval quality is measurably better for representative queries.
