from __future__ import annotations

import json
from typing import Any, Mapping, Sequence

from memory.ingestion.retrieval_ranking import RerankCandidate, rank_candidate_lists


class _CountingReranker:
    def __init__(self) -> None:
        self.calls = 0
        self.candidates = 0

    def rerank(
        self, query: str, candidates: Sequence[RerankCandidate]
    ) -> Mapping[str, float]:
        _ = query
        self.calls += 1
        self.candidates += len(candidates)
        return {candidate.key: float(index) for index, candidate in enumerate(candidates)}


def run_benchmark() -> dict[str, Any]:
    """Measure reranker calls for candidate sets that can and cannot change order."""
    single_reranker = _CountingReranker()
    single = rank_candidate_lists(
        "bounded reranker",
        {"dense": [_row("only", "only candidate")]},
        reranker=single_reranker,
    )
    multi_reranker = _CountingReranker()
    multiple = rank_candidate_lists(
        "bounded reranker",
        {
            "dense": [
                _row("first", "first candidate"),
                _row("second", "bounded reranker candidate"),
            ]
        },
        reranker=multi_reranker,
    )
    previous_single_calls = 1
    return {
        "measurement": "deterministic_reranker_call_amplification",
        "single_candidate": {
            "previous_calls": previous_single_calls,
            "optimized_calls": single_reranker.calls,
            "optimized_candidates_sent": single_reranker.candidates,
            "diagnostic_mode": single.diagnostics["reranker"],
            "result_ids": [str(row["id"]) for row in single.rows],
        },
        "multi_candidate": {
            "optimized_calls": multi_reranker.calls,
            "optimized_candidates_sent": multi_reranker.candidates,
            "diagnostic_mode": multiple.diagnostics["reranker"],
            "result_ids": [str(row["id"]) for row in multiple.rows],
        },
        "single_candidate_call_reduction": _reduction(
            previous_single_calls,
            single_reranker.calls,
        ),
    }


def assert_thresholds(report: dict[str, Any]) -> None:
    single = report["single_candidate"]
    multiple = report["multi_candidate"]
    if single["optimized_calls"] != 0:
        raise AssertionError("single-candidate ranking must not call the reranker")
    if single["result_ids"] != ["only"]:
        raise AssertionError("single-candidate ranking must preserve its result")
    if multiple["optimized_calls"] != 1 or multiple["optimized_candidates_sent"] != 2:
        raise AssertionError("multi-candidate ranking must retain semantic reranking")


def main() -> int:
    report = run_benchmark()
    print(json.dumps(report, indent=2, sort_keys=True))
    try:
        assert_thresholds(report)
    except AssertionError as exc:
        print(str(exc))
        return 1
    return 0


def _row(memory_id: str, text: str) -> dict[str, Any]:
    return {
        "id": memory_id,
        "chunk_index": 0,
        "role": "user",
        "text": text,
        "score": 0.0,
        "conversation": {
            "id": memory_id,
            "source": "benchmark",
            "timestamp": "2026-10-26T00:00:00Z",
            "metadata": {},
        },
    }


def _reduction(previous: int, optimized: int) -> float:
    if previous <= 0:
        return 0.0
    return 1.0 - (optimized / previous)


if __name__ == "__main__":
    raise SystemExit(main())
