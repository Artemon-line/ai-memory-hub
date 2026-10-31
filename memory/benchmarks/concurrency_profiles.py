from __future__ import annotations

import argparse
import gc
import hashlib
import json
import math
import sqlite3
import threading
from collections import Counter
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from statistics import mean
from tempfile import TemporaryDirectory
from time import perf_counter
from typing import Any

from memory.backend.metadata_store import SQLiteMetadataStore
from memory.backend.vector_store import InMemoryVectorStore
from memory.benchmarks.scale_corpora import build_manifest, iter_conversations
from memory.ingestion import mvp_ingestion

_DEFAULT_CONCURRENCY = (1, 4, 16, 32)
_DEFAULT_QUERY_REQUESTS = 32
_EMBEDDING_DIMENSION = 8


class _DeterministicEmbedder:
    dimension = _EMBEDDING_DIMENSION

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        return [_hash_vector(text) for text in texts]


class _BenchmarkMetadataStore(SQLiteMetadataStore):
    """Count expected audit contention without polluting benchmark profiles."""

    def __init__(self, db_path: str) -> None:
        super().__init__(db_path)
        self.audit_attempts = 0
        self.audit_failures = 0
        self._audit_counter_lock = threading.Lock()

    def append_audit_event(self, event: dict[str, Any]) -> dict[str, Any]:
        with self._audit_counter_lock:
            self.audit_attempts += 1
        try:
            return super().append_audit_event(event)
        except sqlite3.OperationalError as exc:
            if "locked" not in str(exc).lower():
                raise
            with self._audit_counter_lock:
                self.audit_failures += 1
            return event


def run_benchmark(
    *,
    profile: str = "small",
    concurrency_levels: Sequence[int] = _DEFAULT_CONCURRENCY,
    query_requests: int = _DEFAULT_QUERY_REQUESTS,
    corpus_limit: int | None = None,
    max_failure_rate: float | None = None,
) -> dict[str, Any]:
    """Run credential-free insert, search, and ask concurrency profiles."""
    levels = tuple(int(level) for level in concurrency_levels)
    if not levels or any(level < 1 for level in levels):
        raise ValueError("concurrency levels must contain positive integers")
    if len(set(levels)) != len(levels):
        raise ValueError("concurrency levels must be unique")
    if query_requests < 1:
        raise ValueError("query_requests must be positive")
    if max_failure_rate is not None and not 0.0 <= max_failure_rate <= 1.0:
        raise ValueError("max_failure_rate must be between 0 and 1")

    manifest = build_manifest(profile)
    profile_size = int(manifest["conversations"])
    selected_size = profile_size if corpus_limit is None else int(corpus_limit)
    if selected_size < 1 or selected_size > profile_size:
        raise ValueError("corpus_limit must be between 1 and the profile size")
    conversations = []
    for index, conversation in enumerate(iter_conversations(profile)):
        if index >= selected_size:
            break
        conversations.append(conversation)

    return {
        "measurement": "credential_free_concurrency_profiles",
        "deployment_profile": "local",
        "providers": {
            "metadata": "sqlite",
            "vector": "memory",
            "embedding": "deterministic-hash",
            "answer": "deterministic-fallback",
        },
        "corpus": {
            "id": manifest["corpus_id"],
            "profile": profile,
            "available_conversations": profile_size,
            "selected_conversations": selected_size,
            "data_classification": "synthetic",
            "sha256": manifest["sha256"],
        },
        "query_requests_per_operation": query_requests,
        "thresholds": {"max_failure_rate": max_failure_rate},
        "profiles": [
            _run_level(
                concurrency=level,
                conversations=conversations,
                retrieval_cases=manifest["retrieval_cases"],
                query_requests=query_requests,
            )
            for level in levels
        ],
    }


def assert_thresholds(report: dict[str, Any]) -> None:
    failures: list[str] = []
    for profile in report.get("profiles", []):
        concurrency = profile.get("concurrency")
        for operation_name in ("insert", "search", "ask"):
            operation = profile.get("operations", {}).get(operation_name, {})
            requests = int(operation.get("requests", 0))
            completed = int(operation.get("completed", 0))
            failed = int(operation.get("failed", 0))
            if completed + failed != requests:
                failures.append(
                    f"concurrency {concurrency} {operation_name} outcome counts are inconsistent"
                )
            max_failure_rate = report.get("thresholds", {}).get("max_failure_rate")
            if (
                max_failure_rate is not None
                and requests
                and (failed / requests) > float(max_failure_rate)
            ):
                failures.append(
                    f"concurrency {concurrency} {operation_name} failure rate "
                    f"{failed / requests:.3f} exceeded {float(max_failure_rate):.3f}"
                )
        insert_completed = int(
            profile.get("operations", {}).get("insert", {}).get("completed", 0)
        )
        stored_conversations = int(
            profile.get("result_counts", {}).get("stored_conversations", 0)
        )
        corpus_size = int(profile.get("corpus_size", 0))
        if not insert_completed <= stored_conversations <= corpus_size:
            failures.append(
                f"concurrency {concurrency} stored count is outside valid outcome bounds"
            )
        if profile.get("result_counts", {}).get("search_results", 0) < 1:
            failures.append(f"concurrency {concurrency} search returned no results")
        if profile.get("result_counts", {}).get("ask_citations", 0) < 1:
            failures.append(f"concurrency {concurrency} ask returned no citations")
    if failures:
        raise AssertionError("; ".join(failures))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run credential-free local insert, search, and ask concurrency profiles."
    )
    parser.add_argument("--profile", choices=("small", "medium", "large"), default="small")
    parser.add_argument(
        "--concurrency",
        nargs="+",
        type=int,
        default=list(_DEFAULT_CONCURRENCY),
        metavar="N",
    )
    parser.add_argument("--query-requests", type=int, default=_DEFAULT_QUERY_REQUESTS)
    parser.add_argument("--corpus-limit", type=int)
    parser.add_argument("--max-failure-rate", type=float)
    args = parser.parse_args(argv)
    report = run_benchmark(
        profile=args.profile,
        concurrency_levels=args.concurrency,
        query_requests=args.query_requests,
        corpus_limit=args.corpus_limit,
        max_failure_rate=args.max_failure_rate,
    )
    print(json.dumps(report, indent=2, sort_keys=True))
    try:
        assert_thresholds(report)
    except AssertionError as exc:
        print(str(exc))
        return 1
    return 0


def _run_level(
    *,
    concurrency: int,
    conversations: list[dict[str, Any]],
    retrieval_cases: list[dict[str, Any]],
    query_requests: int,
) -> dict[str, Any]:
    original_runtime = mvp_ingestion._RUNTIME
    with TemporaryDirectory(prefix="ai-memory-hub-concurrency-") as temp_dir:
        metadata_store = _BenchmarkMetadataStore(f"{temp_dir}/metadata.db")
        vector_store = InMemoryVectorStore(dimension=_EMBEDDING_DIMENSION)
        runtime = mvp_ingestion.RuntimeDependencies(
            embedding_provider=_DeterministicEmbedder(),
            metadata_store=metadata_store,
            vector_store=vector_store,
            health_state={
                "mode": "benchmark",
                "metadata_provider": "sqlite",
                "vector_provider": "memory",
                "embedding": {
                    "provider": "deterministic-hash",
                    "model": "sha256-v1",
                },
            },
            retrieval_vector_score_threshold=2.0,
            retrieval_keyword_enabled=True,
        )
        try:
            mvp_ingestion.configure_runtime(runtime=runtime)
            insert = _measure_concurrent(
                operation=lambda index: mvp_ingestion.ingest_messages(
                    deepcopy(conversations[index])
                ),
                requests=len(conversations),
                concurrency=concurrency,
            )
            search = _measure_concurrent(
                operation=lambda index: _run_search_case(
                    retrieval_cases[index % len(retrieval_cases)]
                ),
                requests=query_requests,
                concurrency=concurrency,
            )
            ask = _measure_concurrent(
                operation=lambda index: _run_ask_case(
                    retrieval_cases[index % len(retrieval_cases)]
                ),
                requests=query_requests,
                concurrency=concurrency,
            )
            search_results = max(
                (len(result.get("results", [])) for result in search.pop("results")),
                default=0,
            )
            ask_citations = max(
                (len(result.get("citations", [])) for result in ask.pop("results")),
                default=0,
            )
            insert.pop("results")
            stored_conversations = len(metadata_store.list_conversations())
            return {
                "concurrency": concurrency,
                "corpus_size": len(conversations),
                "operations": {
                    "insert": insert,
                    "search": search,
                    "ask": ask,
                },
                "result_counts": {
                    "stored_conversations": stored_conversations,
                    "stored_after_failed_request": max(
                        stored_conversations - int(insert["completed"]),
                        0,
                    ),
                    "audit_write_attempts": metadata_store.audit_attempts,
                    "audit_write_failures": metadata_store.audit_failures,
                    "vector_rows": vector_store.get_stats()["rows"],
                    "search_results": search_results,
                    "ask_citations": ask_citations,
                },
            }
        finally:
            mvp_ingestion._RUNTIME = original_runtime
            del runtime
            gc.collect()


def _measure_concurrent(
    *,
    operation: Callable[[int], dict[str, Any]],
    requests: int,
    concurrency: int,
) -> dict[str, Any]:
    start_gate = threading.Event()

    def invoke(index: int) -> tuple[float, dict[str, Any] | None, str | None]:
        start_gate.wait()
        started = perf_counter()
        try:
            result = operation(index)
            latency_ms = (perf_counter() - started) * 1_000
            if result.get("status") != "ok":
                return latency_ms, result, "non_ok_status"
            return latency_ms, result, None
        except Exception as exc:  # benchmark failures are reported, not logged
            return (perf_counter() - started) * 1_000, None, type(exc).__name__

    effective_concurrency = min(concurrency, requests)
    with ThreadPoolExecutor(max_workers=effective_concurrency) as executor:
        futures = [executor.submit(invoke, index) for index in range(requests)]
        wall_started = perf_counter()
        start_gate.set()
        outcomes = [future.result() for future in futures]
        wall_seconds = perf_counter() - wall_started

    latencies = [latency for latency, _result, _error in outcomes]
    results = [result for _latency, result, error in outcomes if error is None and result]
    errors = Counter(error for _latency, _result, error in outcomes if error is not None)
    failed = sum(errors.values())
    return {
        "requests": requests,
        "completed": requests - failed,
        "failed": failed,
        "errors_by_type": dict(sorted(errors.items())),
        "configured_concurrency": concurrency,
        "effective_concurrency": effective_concurrency,
        "wall_seconds": wall_seconds,
        "throughput_per_second": requests / wall_seconds if wall_seconds else 0.0,
        "latency_ms": _latency_summary(latencies),
        "results": results,
    }


def _run_search_case(case: dict[str, Any]) -> dict[str, Any]:
    return mvp_ingestion.search(
        str(case["query"]),
        top_k=5,
        tags=case.get("filters", {}).get("tags"),
    )


def _run_ask_case(case: dict[str, Any]) -> dict[str, Any]:
    return mvp_ingestion.ask(
        str(case["query"]),
        top_k=5,
        tags=case.get("filters", {}).get("tags"),
    )


def _latency_summary(samples: list[float]) -> dict[str, float]:
    ordered = sorted(samples)
    return {
        "avg": mean(ordered),
        "min": ordered[0],
        "p50": _percentile(ordered, 0.50),
        "p95": _percentile(ordered, 0.95),
        "p99": _percentile(ordered, 0.99),
        "max": ordered[-1],
    }


def _percentile(ordered: list[float], percentile: float) -> float:
    return ordered[min(math.ceil(len(ordered) * percentile) - 1, len(ordered) - 1)]


def _hash_vector(text: str) -> list[float]:
    digest = hashlib.sha256(text.encode("utf-8")).digest()
    return [((digest[index] / 255.0) * 2.0) - 1.0 for index in range(_EMBEDDING_DIMENSION)]


if __name__ == "__main__":
    raise SystemExit(main())
