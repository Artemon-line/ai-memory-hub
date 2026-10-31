# Efficiency Revival Plan

Source date: `25-10-2026`

## Goal

Make ai-memory-hub cheaper and faster to operate without weakening retrieval
quality, authorization, durability, privacy, or deterministic fallbacks.

Efficiency means improving all of the following together:

- end-to-end latency for insert, search, retrieve, ask, and import;
- useful work per embedding, storage call, candidate, byte, and model token;
- throughput under concurrent ingestion and retrieval;
- resident memory, database growth, index growth, and temporary allocations;
- startup, readiness, maintenance, and CI cost; and
- operator effort required to diagnose slow or wasteful behavior.

This is a measurement-first plan. An optimization is complete only when a
before/after report shows a material gain and the existing quality and safety
gates still pass.

## Non-Goals

- Do not trade recall, fact correctness, provenance, or tenant isolation for a
  lower latency number.
- Do not introduce a mandatory hosted cache, queue, model, or telemetry service.
- Do not cache raw private conversation payloads outside the configured stores.
- Do not optimize every supported provider identically; use capability-aware
  paths and retain bounded fallbacks.
- Do not change public HTTP or MCP response shapes solely for performance.

## Current Baseline And Evidence

The following local measurements are directional baselines from one Windows
development machine on `25-10-2026`; they are not cross-platform service-level
objectives.

| Benchmark | Current local result | Planning implication |
| --- | ---: | --- |
| Config parsing | `1.43 ms` average over 200 warm iterations | Cache only immutable, path-keyed runtime configuration; first prove it repeats on request paths. |
| Schema loading | `0.66 ms` average over 200 warm iterations | A path-and-mtime-aware schema cache may help validation-heavy ingestion. |
| Token counting | `0.03 ms` warm average | Encoding initialization is already cached; text-level caching is low priority unless real traces show repeated identical text. |
| Schema compatibility | `0.01 ms` average | Do not optimize this independently. |
| Default ask | `2.41 ms` average in the deterministic benchmark | Preserve as the local comparison path. |
| Request-level 80-token budget | `3.06 ms`, `1.27x` default latency | The extra CPU is acceptable only when it reduces downstream context or model cost. |
| Config-enabled 80-token budget | `2.77 ms`, `1.15x` default latency | Prefer one configured budget over repeated per-client policy decisions. |
| Selected ask context | 8 chunks down to 2 chunks / 75 tokens | Measure total model latency and token cost, not only local selection overhead. |

The token-budget benchmark also emitted an audit persistence exception for
each measured request because its stub store cannot retain fallback audit
events. That repeated exception logging pollutes profiles and adds avoidable
CPU and I/O. Benchmark and test doubles must support a silent bounded audit
sink before their results are trusted for optimization decisions.

Existing observability already measures API, MCP, ingestion-stage, embedding,
and vector-search durations. Existing benchmark coverage includes token
budgets, cache candidates, retrieval quality, graph quality, and the versioned
retrieval/extraction quality gate. The plan should extend these assets instead
of creating a separate performance framework.

## Efficiency Scorecard

Every performance report should identify the deployment profile, corpus size,
provider versions, cold/warm state, concurrency, and machine class.

### Latency

- insert, append, search, retrieve, ask, and import p50/p95/p99;
- embedding and vector-search p50/p95 separately;
- query planning, lexical search, fact search, fusion, reranking, context
  assembly, audit persistence, and serialization duration;
- cold startup and readiness time; and
- first request versus steady-state latency.

### Work Amplification

- embedding requests and embedded chunks per accepted conversation;
- vector candidates fetched per returned result;
- filtered-search attempts and candidates discarded after retrieval;
- metadata reads and rows loaded per request;
- facts, graph records, and summaries scanned per answer;
- audit writes and log records per operation; and
- context tokens supplied per cited or answer-supporting token.

### Capacity And Cost

- conversations and messages ingested per second;
- queries per second at concurrency 1, 4, 16, and 32;
- peak and steady-state resident memory;
- metadata and vector bytes per conversation and per chunk;
- model calls, input tokens, output tokens, and estimated provider cost; and
- background maintenance duration and temporary disk use.

### Mandatory Guardrails

- the versioned quality gate remains green;
- filtered recall, precision@1, recall@k, MRR, nDCG, fact precision/recall,
  temporal accuracy, and fallback accuracy do not regress beyond an approved
  tolerance;
- authorization/project isolation and secret-redaction tests remain green;
- no unbounded cache, queue, candidate list, retry loop, or log stream; and
- rollback is configuration-only or a small isolated code revert.

## Target Deployment Profiles

Use three profiles so local optimizations do not accidentally penalize larger
deployments.

| Profile | Representative setup | Primary constraint |
| --- | --- | --- |
| Local | SQLite + LanceDB/local embeddings, one user | startup, memory, idle cost, simple operations |
| Team | Postgres + PGVector or Qdrant, 4–32 concurrent clients | throughput, pooling, tenant fairness |
| Remote-provider | hosted metadata/vector/model services | round trips, batching, retries, token and request cost |

Each optimization must state which profiles benefit and which may regress.

## Phase 0: Make Measurements Trustworthy

Deliver this phase before changing production algorithms.

- [ ] Fix benchmark/test audit sinks so expected unsupported persistence does
      not emit exception traces per request.
- [ ] Add a request-scoped work ledger to benchmark reports: storage calls,
      embedding calls/texts, candidate counts, filtered candidates, reranker
      calls, audit writes, selected chunks, and context tokens.
- [ ] Add cold/warm separation and p50/p95/p99 output to performance helpers;
      averages alone hide tail regressions.
- [x] Add reproducible synthetic corpora at small, medium, and large scales,
      including high-selectivity and low-selectivity filters.
- [x] Add concurrency runners for insert, search, and ask without external
      credentials.
- [ ] Persist machine-readable before/after reports as CI artifacts.
- [ ] Keep deterministic CI gates short; run provider load profiles on demand or
      on a scheduled workflow.

Acceptance criteria:

- Repeated benchmark runs on an idle machine have documented variance bounds.
- Profiles contain no repeated expected exception logging.
- A report can attribute a regression to a stage or amplification counter, not
  only to total request time.
- Performance fixtures contain synthetic data only.

Implementation status:

- The first benchmark-hygiene slice adds a bounded audit sink to the token
  budget benchmark, separates first-call and warm latency, reports warm
  average/p50/p95/p99/min/max distributions, and records per-request search,
  candidate, audit-write, and result/context amplification. Cache-candidate
  operations now report first-call and warm percentile distributions. Scale
  corpora, broader provider work ledgers, and persisted CI comparison artifacts
  remain follow-up work.

The synthetic scale-corpus slice adds deterministic 128, 4,096, and 32,768
conversation profiles with schema-valid messages, stable identities, timestamps,
and hashes. Each profile includes unfiltered, 50%-selectivity, and 0.78%-selectivity
retrieval cases. The generator can stream JSONL plus a manifest containing the
canonical byte count and SHA-256 fingerprint, so local and CI measurements can
prove they used identical synthetic inputs without retaining the large corpus in
memory. Concurrency runners and persisted CI comparison artifacts remain
follow-up work.

The credential-free concurrency slice runs real insert, search, and ask paths
against fresh SQLite and in-memory vector providers at configurable concurrency
levels (1, 4, 16, and 32 by default). Every level starts from the same versioned
synthetic corpus and reports request completion, privacy-safe failure classes,
durable rows after failed requests, wall time, throughput, and
average/p50/p95/p99 latency. The runner uses a
deterministic hash embedder and fallback answers, so it requires no network or
provider credentials. Persisted CI comparison artifacts remain follow-up work.

## Phase 1: Remove Request-Path Waste

Start with low-risk work that should not alter ranking or storage semantics.

- [x] Trace config parsing and schema loading to confirm whether they occur on
      hot request paths.
- [x] If repeated, cache parsed configuration by resolved path plus file
      identity and invalidate explicitly on reload.
- [x] If repeated, cache validated schemas by resolved path plus modification
      time; never allow one configured schema to contaminate another runtime.
- [ ] Reuse bounded provider clients and connection pools rather than creating
      network clients per operation.
- [x] Compute normalized conversation/search text once per candidate within a
      request and reuse it across lexical, metadata, fact, and boost stages.
- [x] Replace repeated single-record lookups with existing `get_many` or a new
      provider capability only where traces show N+1 behavior.
- [ ] Avoid building detailed diagnostics, provenance projections, or full
      result bodies when the caller requested a concise response.
- [ ] Bound and deduplicate warning/error logs by failure class while retaining
      counters and first-occurrence diagnostics.

Acceptance criteria:

- At least one measured hot-path cost is removed or the phase closes with
  evidence that the suspected work is not material.
- Warm p95 latency improves by at least 10% on an affected path, or total work
  amplification drops by at least 20%.
- Cache invalidation and multi-config tests pass.
- No raw payload, query, embedding, token, or secret is added to cache keys or
  logs.

Implementation note (runtime immutable-data cache slice): tracing confirmed
that configuration parsing and schema file loading occur during runtime
construction rather than on normal request paths, so parsed configuration is
intentionally left uncached. Payload validation did, however, re-check and
recompile the same schema for every ingest. Runtimes now reuse a checked
validator cached by resolved schema path, nanosecond modification time, and
file size; the cache is bounded to 16 identities and supports explicit
invalidation. Direct schema loads remain isolated so caller mutation cannot
contaminate another runtime. On a 100-iteration local synthetic run, reusing
the validator reduced unprofiled validation p95 from `29.60 ms` to `0.33 ms`
(more than 98%); exact timings are environment-dependent. Invalidation, mutation
isolation, and multi-schema tests cover reload and concurrent configuration
safety.

Implementation note (fact-source batch hydration slice): fact filtering,
text ranking, temporal projection, retrieval-candidate construction, and
provenance assembly now deduplicate source conversation IDs and reuse bounded
`get_many` reads. Batches are capped at 500 IDs to match every metadata
provider contract, while custom stores without `get_many` retain the compatible
single-read fallback. The deterministic
`memory.benchmarks.fact_hydration_efficiency` fixture preserves all 120 fact
results while reducing the former 240 source reads across ranking and public
projection to one batch read for three unique conversations (more than 99%
fewer metadata read calls). No conversation payloads are cached across
requests.

Implementation note (request-local search-text reuse slice): fact ranking and
question narrowing now share each hydrated source conversation's assembled
search text for the duration of that operation. The cache uses in-memory object
identity, is discarded after the request stage, and never stores text across
requests. Lexical candidates already assembled their conversation text once per
candidate. The deterministic `memory.benchmarks.search_text_efficiency` fixture
preserves all 120 ranked facts while reducing source conversation search-text
construction from 120 times to three times for three unique conversations
(97.5% less construction work).

## Phase 2: Make Ingestion Proportional To New Information

- [ ] Establish stage timings and amplification counts for validate, normalize,
      hash, deduplicate, chunk, extract, embed, metadata write, vector write,
      derived-record write, and audit.
- [x] Complete deduplication and append-delta detection before embedding or
      derived extraction wherever the contract permits.
- [x] Batch embedding inputs up to provider-advertised limits with bounded
      payload size and deterministic result-to-chunk mapping.
- [ ] Use metadata/vector batch-write capabilities when available; retain the
      current atomic or compensating behavior on partial failure.
- [ ] Introduce bounded ingestion concurrency by project/provider, with
      backpressure rather than unbounded task creation.
- [ ] Avoid recomputing facts, graph records, summaries, or hashes for unchanged
      messages during append and reindex.
- [ ] Add checkpointed, resumable reindex batches so a large reindex does not
      retain the entire corpus or restart from zero.
- [x] Report bytes and rows written per accepted message and per duplicate.

Acceptance criteria:

- Duplicate ingestion performs zero embedding and vector-write work.
- Append work scales with new messages/chunks rather than full conversation
  length.
- Batch ingestion improves remote-provider throughput by at least 25% without
  increasing error leakage or partial-write ambiguity.
- Peak memory remains bounded by configured batch size.

Implementation note (duplicate-ingestion fast-path slice): exact duplicates
now return before summary/fact/graph derivation, metadata rewrites, embedding,
or vector writes when the stored manifest is indexed and the vector provider
confirms all expected chunk IDs. Pending manifests are reconciled from existing
vector rows, while missing vectors retain the full repair path. The deterministic
`memory.benchmarks.ingestion_efficiency` work ledger reports calls, rows, and
bytes for accepted and duplicate inputs. Its baseline fixture drops from two
embedding inputs and two vector-row writes on the former full-reindex path to
zero of each, a 100% reduction; the required audit row remains.

Implementation note (single-pass append-summary slice): generated summaries
are now attached only after trusted-append detection. New conversations retain
the same summary behavior, while an append generates summaries once from the
combined stored conversation instead of first writing transient summaries for
the incoming transcript and immediately overwriting the same derived
identities. The deterministic ingestion work ledger preserves the final
three-message summary and reduces the append fixture from two summary passes
and four derived-row writes to one pass and two writes (50% less summary work).
Rejected same-ID updates also perform no generated-summary writes. Other
unchanged-message derivation remains follow-up work.

Implementation note (incremental append-graph slice): trusted appends now run
graph extraction only over newly appended messages and carry the original
zero-based message offset into entity and relationship provenance. Existing
relationships remain available for conflict supersession, while unchanged
messages are not scanned or rewritten by the extraction stage. The deterministic
`memory.benchmarks.append_graph_efficiency` fixture reduces graph inputs for a
two-message conversation plus one-message append from three messages to one
(66.7%) and verifies that the new relationship still cites message index 2.
Facts were already append-incremental; unchanged-message hashing and reindex
work remain follow-up items.

Implementation note (verified append-identity reuse slice): normalization still
recomputes every client-supplied message hash so trusted appends cannot bypass
integrity checks. When the normalized payload contains the complete stored
prefix plus its append, the write path now reuses that already verified,
project-scoped aggregate conversation identity instead of joining and hashing
the same ordered hashes a second time. Sibling-thread continuations retain a
safe combined-history recomputation and now preserve project scoping. The
deterministic `memory.benchmarks.append_hash_efficiency` fixture reduces
aggregate hash passes and message-hash scans for a 64-message conversation plus
one-message append from two/130 to one/65 (50%). Per-message normalization and
broader unchanged-message derivation remain follow-up work.

Implementation note (bounded embedding-batch slice): remote embedding inputs
are now split sequentially by configured count and serialized UTF-8 JSON input
bytes before provider calls. Vector persistence begins only after every batch succeeds,
so a later provider failure cannot expose a partial vector write. HTTP response
indexes are validated and restored to request order when present; legacy
unindexed responses retain their declared order. The deterministic
`memory.benchmarks.embedding_batch_efficiency` fixture preserves all ten
chunk/vector mappings while reducing the largest request from ten inputs to
three (70%); its four batches remain at or below both advertised limits. Bounded
concurrency and remote-provider throughput measurements remain follow-up work.

## Phase 3: Reduce Retrieval Amplification

- [ ] Break search and ask latency into planning, embedding, dense, lexical,
      fact, graph, fusion, reranking, hydration, context, and serialization.
- [ ] Run independent candidate sources concurrently only when provider clients
      are safe and a shared deadline can cancel or ignore late optional work.
- [ ] Make candidate limits responsive to `top_k`, filter selectivity, provider
      pushdown capability, and observed hit yield; retain hard global caps.
- [ ] Hydrate only unique candidate IDs and only fields required by the selected
      response mode and reranker projection.
- [ ] Skip semantic reranking when deterministic evidence is decisive, the
      candidate set is too small to change order, or the remaining deadline is
      insufficient.
- [ ] Reuse the query plan between `memory_lookup`, search, and ask sub-steps
      inside one request; do not cache authorization scope from model output.
- [ ] Add a bounded, privacy-safe query-embedding cache only after production
      traces demonstrate meaningful exact-query repetition. Key it by embedding
      provider/model/version and normalized query, never by incomplete tenant
      scope when results are cached.
- [ ] Prefer fact/profile or summary evidence before raw chunks when it answers
      the question with equal or better confidence and provenance.

Acceptance criteria:

- Candidate-to-result amplification falls by at least 25% on filtered and
  unfiltered medium/large corpora.
- Search and ask p95 improve by at least 20% for one target profile.
- Reranker/model call rate drops without reducing fallback accuracy or quality
  metrics.
- Deadline and cancellation behavior is deterministic and test-covered.

Implementation note (adaptive candidate-budget slice): unfiltered lexical and
fact candidate admission now uses the smaller of each configured hard cap and
`top_k * candidate_multiplier`, matching the existing dense-search budget.
Filtered and non-active-memory searches retain their configured maxima because
some metadata providers apply those constraints after their initial text
limit. The deterministic `memory.benchmarks.retrieval_amplification` fixture
reduces the representative `top_k=5`, multiplier-3 lexical/fact budget from 75
to 30 candidates (60%) while preserving the full 75-candidate filtered budget.
The versioned quality gate retains perfect precision@1, recall@k, MRR, nDCG,
filtered recall, temporal accuracy, and fallback accuracy on its synthetic
corpus. Provider-pushdown and observed-yield adaptation remain follow-up work.

Implementation note (small-set reranker-bypass slice): rank fusion now reports
`insufficient_candidates` and skips both bounded projection construction and
semantic reranker invocation when its rerankable subset contains zero or one
candidate. A single candidate cannot change relative order, including when a
configured reranker batch limit exposes only the leading item. Multi-candidate
semantic, timeout, error, and deterministic-fallback paths are unchanged. The
deterministic `memory.benchmarks.reranker_efficiency` fixture reduces the
single-candidate reranker call count from one to zero (100%), preserves the
result, and verifies that a two-candidate request still makes one call. The
versioned quality gate remains green with unchanged retrieval metrics.

## Phase 4: Bound Context And Model Cost

- [ ] Measure input/output tokens and selected evidence per ask mode and client.
- [ ] Define default context budgets by concise/detailed response mode, with
      explicit overrides and stable diagnostics.
- [x] Deduplicate overlapping chunks before token counting and context assembly.
- [ ] Prefer the smallest evidence span that preserves the claim and citation.
- [ ] Use existing summaries and validated facts as compact evidence; never
      synthesize a summary merely to save tokens unless its creation cost and
      provenance are accounted for.
- [ ] Gate optional planner, extractor, reranker, and answer-model calls with
      deterministic sufficiency checks.
- [ ] Track model calls avoided, tokens avoided, and answer quality together.

Acceptance criteria:

- Median selected context tokens fall by at least 30% on detailed ask cases or
  at least 50% on concise cases.
- Total answer latency or provider cost improves after including local token
  selection overhead.
- Citation completeness and answer-contract accuracy remain unchanged.

Implementation note (overlapping-context deduplication slice): budgeted ask,
fact/chunk arbitration, and handoff context now remove an exact suffix/prefix
overlap only when adjacent chunks share both a conversation ID and stored source
message hash. Ranking and citation identities remain unchanged, and different
messages retain repeated wording. The deterministic
`memory.benchmarks.context_overlap_efficiency` fixture preserves all unique
evidence while reducing total selected-context accounting from 40 to 38 tokens
(5%); citation framing accounts for the invariant portion of that measurement.

## Phase 5: Storage And Index Efficiency

- [ ] Capture query plans and row counts for SQLite and Postgres metadata paths
      used by search, fact/profile reads, timeline, audit, and project scoping.
- [ ] Add or adjust indexes only for measured scans; include write and storage
      cost in the before/after report.
- [ ] Enforce cursor pagination and projection reads on potentially unbounded
      list/search paths.
- [ ] Measure index-chunk manifest, fact, graph, audit, and tombstone growth per
      conversation.
- [ ] Define safe retention/compaction for superseded derived records, stale
      pending items, and audit data without altering immutable source memories.
- [ ] Document provider-specific vacuum, compaction, index-readiness, and
      connection-pool guidance.
- [ ] Add health diagnostics for pool saturation, index lag, storage growth,
      fallback activation, and maintenance age.

Acceptance criteria:

- The large corpus has no unapproved full scan on critical read paths.
- Metadata and vector growth remain within documented per-conversation budgets.
- Maintenance is resumable, bounded, observable, and safe to interrupt.
- Storage optimizations pass SQLite, Postgres, and relevant provider contracts.

## Phase 6: Runtime And Operational Efficiency

- [ ] Audit synchronous provider calls executed on async API/MCP paths and move
      blocking work behind an appropriate bounded execution boundary.
- [ ] Define connection, worker, queue, batch, timeout, and retry budgets per
      deployment profile.
- [ ] Use one end-to-end request deadline; prevent nested retries from exceeding
      it.
- [ ] Add overload behavior that fails predictably or sheds optional work before
      memory and queues grow without bound.
- [ ] Separate readiness checks from expensive provider probes with bounded,
      stale-aware caching.
- [ ] Reduce CI duplication by identifying equivalent push and pull-request
      runs while preserving branch-protection evidence.
- [ ] Publish a compact efficiency dashboard/runbook using existing metrics.

Acceptance criteria:

- Load tests show bounded queue depth and memory at the documented concurrency
  ceiling.
- Provider outage tests complete within the request deadline and do not create
  retry storms.
- Idle local deployments retain a small, documented resource footprint.
- CI minutes decrease without removing required test, security, or quality
  coverage.

## Proposed PR-Sized Workstreams

Implement in this order; each item should remain independently reviewable.

1. **Benchmark hygiene and work ledger** — fix audit-log amplification, add
   counters, percentiles, cold/warm modes, and synthetic scale profiles.
2. **Runtime immutable-data caches** — prove and optimize repeated config/schema
   work with explicit invalidation.
3. **Incremental and batched ingestion** — dedupe before expensive work, batch
   embeddings/writes, and bound concurrency.
4. **Retrieval amplification controls** — stage timings, adaptive candidate
   limits, unique hydration, and deadline-aware optional work.
5. **Context and model-call economy** — evidence dedupe, response-mode budgets,
   and deterministic model bypasses.
6. **Metadata query/index audit** — measured indexes, projections, pagination,
   growth budgets, and maintenance guidance.
7. **Provider and concurrency profiles** — local/team/remote load reports,
   pooling, backpressure, deadlines, and outage tests.
8. **Operational efficiency and CI** — dashboard/runbook, idle footprint, and
   duplicate-work reduction.

Do not combine workstreams 2–8 into one optimization PR. Measurement changes
from workstream 1 may land together because later before/after reports depend on
them.

## Decision Rules

- Prefer deleting work over making unnecessary work faster.
- Prefer request-local reuse before cross-request caching.
- Cache immutable or versioned values; require explicit invalidation for mutable
  values.
- Include provider/model/version and relevant policy in cache identities.
- Never cache authorization decisions or results under a key that omits owner,
  project, visibility, or data-version scope.
- Bound every cache by entry count and/or bytes, expose hit/miss/eviction
  metrics, and test eviction.
- Parallelize independent I/O only under a shared deadline and concurrency cap.
- Keep local-first deterministic paths functional when optional services are
  absent.
- Reject improvements that move cost to startup, memory, storage, CI, or model
  usage without showing a favorable total-cost tradeoff.

## Validation Matrix

Each implementation PR should run the smallest relevant subset first, then the
broader gates appropriate to its blast radius.

```bash
uv run python -m ruff check memory tests tools
uv run python -m pyright
uv run pytest tests/unit tests/integration -q
uv run python -m memory.benchmarks.cache_candidates --iterations 1000
uv run python -m memory.benchmarks.ingestion_efficiency
uv run python -m memory.benchmarks.append_graph_efficiency
uv run python -m memory.benchmarks.append_hash_efficiency
uv run python -m memory.benchmarks.embedding_batch_efficiency
uv run python -m memory.benchmarks.reranker_efficiency
uv run python -m memory.benchmarks.retrieval_amplification
uv run python -m memory.benchmarks.context_overlap_efficiency
uv run python -m memory.benchmarks.token_budget --iterations 200
uv run python -m memory.benchmarks.quality_gate \
  --output reports/quality/quality-gate.json
```

Add provider contract, live-provider, concurrency, or outage tests when the
changed path touches pooling, batching, retries, filtering, persistence, or
deadlines. Documentation changes must pass the strict MkDocs build.

## Done When

- [ ] Baseline and before/after reports exist for all three deployment profiles.
- [ ] Search and ask p95 improve by at least 20% on a representative medium
      corpus without quality regression.
- [ ] Duplicate and append ingestion avoid work proportional to unchanged data.
- [ ] Remote ingestion throughput improves by at least 25% through measured
      batching and bounded concurrency.
- [ ] Candidate-to-result amplification falls by at least 25% on representative
      filtered and unfiltered queries.
- [ ] Context/model cost falls by at least 30% on the target ask workload with
      unchanged citation and answer quality.
- [ ] Memory, storage growth, queue depth, retries, cache size, and maintenance
      work all have explicit bounds and diagnostics.
- [ ] Local-first operation, provider fallbacks, auth isolation, redaction,
      durability, and public contracts remain intact.
- [ ] The quality gate, full unit/integration suite, provider contracts, and
      applicable end-to-end tests pass.
