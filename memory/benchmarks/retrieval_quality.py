from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from typing import Any

from memory.ingestion import mvp_ingestion


@dataclass(frozen=True)
class RetrievalCase:
    name: str
    query: str
    expected_ids: tuple[str, ...]
    vector_scores: dict[str, float]
    top_k: int = 3


class EvaluationEmbedder:
    def __init__(self, query_indexes: dict[str, int]):
        self.query_indexes = query_indexes

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        return [[float(self.query_indexes.get(text, -1))] for text in texts]


class EvaluationMetadataStore:
    def __init__(self, conversations: list[dict[str, Any]]):
        self.by_id = {str(conversation["id"]): conversation for conversation in conversations}

    def get(self, memory_id: str) -> dict[str, Any] | None:
        return self.by_id.get(memory_id)

    def get_many(self, ids: list[str]) -> dict[str, dict[str, Any]]:
        return {memory_id: self.by_id[memory_id] for memory_id in ids if memory_id in self.by_id}

    def search_text(self, query: str, *, limit: int) -> list[dict[str, Any]]:
        tokens = _query_tokens(query)
        matches: list[dict[str, Any]] = []
        for conversation in self.by_id.values():
            text = _conversation_text(conversation)
            if all(token in text for token in tokens):
                matches.append(conversation)
        return matches[:limit]


class EvaluationVectorStore:
    def __init__(self, cases: list[RetrievalCase]):
        self._scores_by_case = [case.vector_scores for case in cases]

    def search(self, query_vector: list[float], top_k: int = 5) -> list[dict[str, Any]]:
        case_index = int(query_vector[0])
        if case_index < 0 or case_index >= len(self._scores_by_case):
            return []
        rows = [
            {
                "memory_id": memory_id,
                "chunk_index": 0,
                "role": "user",
                "text": "",
                "score": score,
            }
            for memory_id, score in self._scores_by_case[case_index].items()
        ]
        return sorted(rows, key=lambda row: (float(row["score"]), str(row["memory_id"])))[:top_k]


def run_evaluation(
    *,
    top_k: int = 3,
    min_precision_at_1: float = 0.85,
    min_recall_at_k: float = 0.90,
    min_mrr: float = 0.85,
) -> dict[str, Any]:
    if top_k < 1:
        raise ValueError("top_k must be positive")

    conversations = _representative_conversations()
    cases = _representative_cases(top_k=top_k)
    baseline = _evaluate(
        conversations=conversations,
        cases=cases,
        tuned=False,
        top_k=top_k,
    )
    tuned = _evaluate(
        conversations=conversations,
        cases=cases,
        tuned=True,
        top_k=top_k,
    )
    report = {
        "corpus": {
            "conversations": len(conversations),
            "cases": len(cases),
            "description": "Representative local project-memory queries from MCP, storage, auth, observability, and user-profile recall.",
        },
        "top_k": top_k,
        "baseline": baseline["summary"],
        "tuned": tuned["summary"],
        "cases": tuned["cases"],
        "baseline_cases": baseline["cases"],
        "thresholds": {
            "min_precision_at_1": min_precision_at_1,
            "min_recall_at_k": min_recall_at_k,
            "min_mrr": min_mrr,
            "failures": _threshold_failures(
                tuned=tuned["summary"],
                baseline=baseline["summary"],
                min_precision_at_1=min_precision_at_1,
                min_recall_at_k=min_recall_at_k,
                min_mrr=min_mrr,
            ),
        },
        "tuned_retrieval_config": {
            "vector_score_threshold": 7.5,
            "keyword_enabled": True,
            "keyword_weight": 0.25,
            "metadata_weight": 0.15,
            "candidate_multiplier": 3,
        },
    }
    return report


def assert_thresholds(report: dict[str, Any]) -> None:
    failures = report.get("thresholds", {}).get("failures", [])
    if failures:
        raise AssertionError("; ".join(str(failure) for failure in failures))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Evaluate retrieval quality on representative local memory queries.")
    parser.add_argument("--top-k", type=int, default=3)
    parser.add_argument("--min-precision-at-1", type=float, default=0.85)
    parser.add_argument("--min-recall-at-k", type=float, default=0.90)
    parser.add_argument("--min-mrr", type=float, default=0.85)
    args = parser.parse_args(argv)

    report = run_evaluation(
        top_k=args.top_k,
        min_precision_at_1=args.min_precision_at_1,
        min_recall_at_k=args.min_recall_at_k,
        min_mrr=args.min_mrr,
    )
    print(json.dumps(report, indent=2, sort_keys=True))
    try:
        assert_thresholds(report)
    except AssertionError:
        return 1
    return 0


def _evaluate(
    *,
    conversations: list[dict[str, Any]],
    cases: list[RetrievalCase],
    tuned: bool,
    top_k: int,
) -> dict[str, Any]:
    previous_runtime = mvp_ingestion._RUNTIME
    query_indexes = {case.query: index for index, case in enumerate(cases)}
    try:
        mvp_ingestion.configure_runtime(
            runtime=mvp_ingestion.RuntimeDependencies(
                embedding_provider=EvaluationEmbedder(query_indexes),  # type: ignore[arg-type]
                metadata_store=EvaluationMetadataStore(conversations),
                vector_store=EvaluationVectorStore(cases),
                health_state={"mode": "evaluation", "vector_fallback_active": False},
                retrieval_vector_score_threshold=7.5 if tuned else 1000.0,
                retrieval_keyword_enabled=tuned,
                retrieval_keyword_candidate_limit=50,
                retrieval_keyword_weight=0.25 if tuned else 0.0,
                retrieval_metadata_weight=0.15 if tuned else 0.0,
                retrieval_candidate_multiplier=3,
            )
        )
        rows = [_evaluate_case(case, top_k=top_k) for case in cases]
    finally:
        mvp_ingestion._RUNTIME = previous_runtime

    return {"cases": rows, "summary": _summarize(rows)}


def _evaluate_case(case: RetrievalCase, *, top_k: int) -> dict[str, Any]:
    result = mvp_ingestion.search(case.query, top_k=top_k)
    result_ids = [str(row["id"]) for row in result.get("results", [])]
    expected = set(case.expected_ids)
    hits = [memory_id for memory_id in result_ids if memory_id in expected]
    first_hit_rank = next(
        (index + 1 for index, memory_id in enumerate(result_ids) if memory_id in expected),
        None,
    )
    return {
        "name": case.name,
        "query": case.query,
        "expected_ids": list(case.expected_ids),
        "result_ids": result_ids,
        "precision_at_1": 1.0 if result_ids and result_ids[0] in expected else 0.0,
        "precision_at_k": len(hits) / max(len(result_ids), 1),
        "recall_at_k": len(hits) / len(expected),
        "reciprocal_rank": 0.0 if first_hit_rank is None else 1.0 / first_hit_rank,
    }


def _summarize(rows: list[dict[str, Any]]) -> dict[str, float]:
    count = max(len(rows), 1)
    return {
        "precision_at_1": sum(float(row["precision_at_1"]) for row in rows) / count,
        "precision_at_k": sum(float(row["precision_at_k"]) for row in rows) / count,
        "recall_at_k": sum(float(row["recall_at_k"]) for row in rows) / count,
        "mrr": sum(float(row["reciprocal_rank"]) for row in rows) / count,
    }


def _threshold_failures(
    *,
    tuned: dict[str, float],
    baseline: dict[str, float],
    min_precision_at_1: float,
    min_recall_at_k: float,
    min_mrr: float,
) -> list[str]:
    failures: list[str] = []
    if tuned["precision_at_1"] < min_precision_at_1:
        failures.append(f"precision_at_1 {tuned['precision_at_1']:.3f} < {min_precision_at_1:.3f}")
    if tuned["recall_at_k"] < min_recall_at_k:
        failures.append(f"recall_at_k {tuned['recall_at_k']:.3f} < {min_recall_at_k:.3f}")
    if tuned["mrr"] < min_mrr:
        failures.append(f"mrr {tuned['mrr']:.3f} < {min_mrr:.3f}")
    if tuned["mrr"] < baseline["mrr"]:
        failures.append(f"mrr regressed below baseline {tuned['mrr']:.3f} < {baseline['mrr']:.3f}")
    if tuned["precision_at_1"] < baseline["precision_at_1"]:
        failures.append(
            f"precision_at_1 regressed below baseline {tuned['precision_at_1']:.3f} < {baseline['precision_at_1']:.3f}"
        )
    if tuned["recall_at_k"] < baseline["recall_at_k"]:
        failures.append(
            f"recall_at_k regressed below baseline {tuned['recall_at_k']:.3f} < {baseline['recall_at_k']:.3f}"
        )
    return failures


def _representative_conversations() -> list[dict[str, Any]]:
    return [
        _conversation(
            "11111111-1111-4111-8111-111111111111",
            "Codex opencode Docker PGVector handoff",
            "codex",
            ["mcp", "docker", "pgvector", "codex", "opencode", "tiktoken"],
            "Use Docker Compose from WSL to run ai-memory-hub with Postgres and PGVector. "
            "Codex saves the conversation through MCP, then opencode asks what the recent Codex conversation was about. "
            "The container enables tokenizer support with tiktoken and exposes the MCP endpoint on port 8000.",
        ),
        _conversation(
            "22222222-2222-4222-8222-222222222222",
            "Bearer API key auth plan",
            "manual",
            ["auth", "api-key", "mcp", "http"],
            "Protect memory HTTP and MCP routes with Authorization Bearer tokens or X-API-Key. "
            "Keep health and readiness endpoints public until OAuth is needed.",
        ),
        _conversation(
            "33333333-3333-4333-8333-333333333333",
            "Observability logging telemetry plan",
            "manual",
            ["observability", "logging", "opentelemetry", "jaeger"],
            "Structured stdout logs should include request IDs without raw payloads, embeddings, API keys, or DSNs. "
            "OpenTelemetry traces can flow through an OTel Collector to Jaeger.",
        ),
        _conversation(
            "44444444-4444-4444-8444-444444444444",
            "Memory quality fact layer implementation",
            "codex",
            ["facts", "profile", "memory-ask", "retrieval"],
            "The fact layer extracts profile facts, project facts, recurring topics, and corrections. "
            "memory_ask returns confidence, answer_basis, and compact provenance.",
        ),
        _conversation(
            "55555555-5555-4555-8555-555555555555",
            "User guitar profile",
            "mcp",
            ["profile", "guitar", "music"],
            "I own a Gibson Special with P90 pickups. The correction says the finish is TV yellow, not cherry.",
        ),
        _conversation(
            "66666666-6666-4666-8666-666666666666",
            "Cache candidate benchmark",
            "codex",
            ["benchmark", "cache", "lru", "performance"],
            "The cache candidate benchmark measures schema loading, config parsing, compatibility checks, and token counting. "
            "It reports whether an LRU cache is worth adding.",
        ),
    ]


def _representative_cases(*, top_k: int) -> list[RetrievalCase]:
    _ = top_k
    return [
        RetrievalCase(
            name="mcp_pgvector_handoff",
            query="Codex opencode PGVector Docker handoff",
            expected_ids=("11111111-1111-4111-8111-111111111111",),
            vector_scores={
                "22222222-2222-4222-8222-222222222222": 0.20,
                "33333333-3333-4333-8333-333333333333": 0.35,
                "11111111-1111-4111-8111-111111111111": 1.10,
                "66666666-6666-4666-8666-666666666666": 1.70,
            },
        ),
        RetrievalCase(
            name="bearer_api_key_auth",
            query="bearer API key auth MCP",
            expected_ids=("22222222-2222-4222-8222-222222222222",),
            vector_scores={
                "11111111-1111-4111-8111-111111111111": 0.15,
                "33333333-3333-4333-8333-333333333333": 0.45,
                "22222222-2222-4222-8222-222222222222": 0.95,
                "44444444-4444-4444-8444-444444444444": 1.30,
            },
        ),
        RetrievalCase(
            name="observability_otel",
            query="structured logging OpenTelemetry Jaeger",
            expected_ids=("33333333-3333-4333-8333-333333333333",),
            vector_scores={
                "66666666-6666-4666-8666-666666666666": 0.10,
                "22222222-2222-4222-8222-222222222222": 0.40,
                "33333333-3333-4333-8333-333333333333": 0.85,
                "11111111-1111-4111-8111-111111111111": 1.50,
            },
        ),
        RetrievalCase(
            name="profile_guitar_fact",
            query="Gibson P90 guitar",
            expected_ids=("55555555-5555-4555-8555-555555555555",),
            vector_scores={
                "44444444-4444-4444-8444-444444444444": 0.25,
                "66666666-6666-4666-8666-666666666666": 0.55,
                "55555555-5555-4555-8555-555555555555": 0.90,
                "33333333-3333-4333-8333-333333333333": 1.25,
            },
        ),
        RetrievalCase(
            name="cache_candidates",
            query="LRU cache benchmark candidates",
            expected_ids=("66666666-6666-4666-8666-666666666666",),
            vector_scores={
                "33333333-3333-4333-8333-333333333333": 0.30,
                "11111111-1111-4111-8111-111111111111": 0.55,
                "66666666-6666-4666-8666-666666666666": 0.95,
                "44444444-4444-4444-8444-444444444444": 1.15,
            },
        ),
        RetrievalCase(
            name="tokenizer_container",
            query="tiktoken tokenizer container",
            expected_ids=("11111111-1111-4111-8111-111111111111",),
            vector_scores={
                "66666666-6666-4666-8666-666666666666": 0.15,
                "44444444-4444-4444-8444-444444444444": 0.50,
                "11111111-1111-4111-8111-111111111111": 0.80,
                "33333333-3333-4333-8333-333333333333": 1.40,
            },
        ),
    ]


def _conversation(
    memory_id: str,
    title: str,
    source: str,
    tags: list[str],
    text: str,
) -> dict[str, Any]:
    return {
        "id": memory_id,
        "source": source,
        "timestamp": "2026-06-12T00:00:00Z",
        "title": title,
        "messages": [{"role": "user", "text": text}],
        "metadata": {
            "imported_at": "2026-06-12T00:00:00Z",
            "updated_at": "2026-06-12T00:00:00Z",
            "conversation_hash": "sha256:" + memory_id.replace("-", "")[:64].ljust(64, "0"),
            "tags": tags,
            "topics": tags,
        },
    }


def _conversation_text(conversation: dict[str, Any]) -> str:
    parts = [
        str(conversation.get("id", "")),
        str(conversation.get("source", "")),
        str(conversation.get("title", "")),
    ]
    metadata = conversation.get("metadata", {})
    if isinstance(metadata, dict):
        for key in ("tags", "topics"):
            value = metadata.get(key)
            if isinstance(value, list):
                parts.extend(str(item) for item in value)
    for message in conversation.get("messages", []):
        if isinstance(message, dict):
            parts.append(str(message.get("text", "")))
    return " ".join(parts).lower()


def _query_tokens(query: str) -> list[str]:
    return mvp_ingestion._query_tokens(query)


if __name__ == "__main__":
    raise SystemExit(main())
