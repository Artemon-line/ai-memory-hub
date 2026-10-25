# Retrieval And Extraction Quality Evaluation

The representative quality gate complements the small retrieval smoke benchmark.
It uses the versioned, synthetic corpus in
`memory/benchmarks/corpora/quality-v1.json`; committed cases must never contain
private production conversations.

Run the deterministic gate locally:

```bash
uv run python -m memory.benchmarks.quality_gate \
  --output reports/quality/quality-gate.json
```

The command exits non-zero when a threshold fails. Its versioned JSON report
contains precision@1, recall@k, MRR, nDCG@k, filtered recall, fact extraction
precision and recall, temporal and conflict accuracy, answer-contract accuracy,
retrieval latency percentiles, fallback rate, and fallback accuracy.

The corpus covers semantic paraphrases, exact identifiers, explicit and
natural-language filters, a relevant filtered result beyond the unfiltered top
100, corrections and historical values, negation and uncertainty, quoted and
assistant-generated claims, multilingual text, provider score scales, and
reranker timeout/error fallbacks. The deterministic runner needs no external
credentials.

## Optional Provider Or Model Runs

A provider adapter can emit an observations JSON object with these top-level
fields:

- `runner`: a stable provider/model configuration name;
- `retrieval`: case names, ranked `result_ids`, and fallback flags;
- `extraction`: case names and `[subject, predicate, object]` facts;
- `temporal`: case names, latest values, history values, and conflict flags;
- `answers`: case names, mode, answer, and ordered evidence IDs; and
- `latency_ms`: per-query retrieval latency samples.

Evaluate it against the same labels and report schema:

```bash
uv run python -m memory.benchmarks.quality_gate \
  --observations reports/provider-observations.json \
  --output reports/provider-quality.json
```

Keep expensive or credentialed provider runs opt-in. The normal CI lane uses
only the deterministic runner.

## Changing Thresholds Or Corpus Labels

Threshold values live in `memory.benchmarks.quality_gate.DEFAULT_THRESHOLDS`.
Their rationale and baseline identity live beside the corpus. A pull request
that changes a threshold must explain why and include before/after reports.
Corpus changes should increment the corpus identifier or schema version when
results are no longer directly comparable.
