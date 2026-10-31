from __future__ import annotations

import argparse
import json
from time import perf_counter
from typing import Any, Callable

from memory.benchmarks.latency import cold_warm_latency_ms
from memory.benchmarks.work_ledger import WorkLedger
from memory.ingestion import mvp_ingestion, tokenizer

_QUESTION = "What stored implementation details mention token budgets?"
_TOP_K = 8
_BUDGET = 80
_ENCODING = "cl100k_base"


class _Embedder:
    dimension = 1

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        return [[1.0] for _ in texts]


class _BenchmarkAuditStore:
    def __init__(self, ledger: WorkLedger):
        self._ledger = ledger

    def append_audit_event(self, _event: dict[str, Any]) -> None:
        self._ledger.audit_writes += 1


class _BenchmarkSearch:
    def __init__(self, ledger: WorkLedger):
        self._ledger = ledger

    def __call__(self, query: str, top_k: int = 5, **_kwargs: Any) -> dict[str, Any]:
        _ = query
        rows = _FIXED_MATCHES[:top_k]
        self._ledger.storage_calls += 1
        self._ledger.candidate_count += len(rows)
        return {"status": "ok", "results": rows}


_FIXED_MATCHES: list[dict[str, Any]] = [
    {
        "id": "00000000-0000-4000-8000-000000000001",
        "chunk_index": 0,
        "role": "user",
        "score": 0.01,
        "text": "Token budgeting should keep ask context small and cite only selected chunks.",
        "conversation": None,
    },
    {
        "id": "00000000-0000-4000-8000-000000000002",
        "chunk_index": 0,
        "role": "assistant",
        "score": 0.02,
        "text": "The tokenizer adapter should use tiktoken lazily and fall back deterministically.",
        "conversation": None,
    },
    {
        "id": "00000000-0000-4000-8000-000000000003",
        "chunk_index": 1,
        "role": "user",
        "score": 0.03,
        "text": "Long messages can be split into overlapping token windows during ingestion.",
        "conversation": None,
    },
    {
        "id": "00000000-0000-4000-8000-000000000004",
        "chunk_index": 0,
        "role": "assistant",
        "score": 0.04,
        "text": "Default message chunking must remain unchanged for existing clients and tests.",
        "conversation": None,
    },
    {
        "id": "00000000-0000-4000-8000-000000000005",
        "chunk_index": 2,
        "role": "user",
        "score": 0.05,
        "text": "Diagnostics include context_tokens_used chunks_selected chunks_dropped and tokenizer_used.",
        "conversation": None,
    },
    {
        "id": "00000000-0000-4000-8000-000000000006",
        "chunk_index": 0,
        "role": "assistant",
        "score": 0.06,
        "text": "SQLite metadata stores token chunk manifests in metadata index_chunks for chunk state tracking.",
        "conversation": None,
    },
    {
        "id": "00000000-0000-4000-8000-000000000007",
        "chunk_index": 0,
        "role": "user",
        "score": 0.07,
        "text": "MCP memory_ask accepts max_context_tokens while preserving the stable error envelope.",
        "conversation": None,
    },
    {
        "id": "00000000-0000-4000-8000-000000000008",
        "chunk_index": 1,
        "role": "assistant",
        "score": 0.08,
        "text": "HTTP ask validation uses the existing FastAPI and Pydantic request validation path.",
        "conversation": None,
    },
]


def run_benchmark(
    *,
    iterations: int = 200,
    budget: int = _BUDGET,
    max_budgeted_ratio: float | None = None,
    max_config_enabled_ratio: float | None = None,
) -> dict[str, Any]:
    """Run a deterministic lightweight token-budget benchmark.

    The benchmark uses a fixed in-memory retrieval result to avoid network,
    embedding, and storage variance. Ratios are reported by default and only
    enforced when a threshold is provided.
    """
    if iterations < 1:
        raise ValueError("iterations must be positive")
    if budget < 1:
        raise ValueError("budget must be positive")

    original_runtime = mvp_ingestion._RUNTIME
    original_search = mvp_ingestion.search
    ledger = WorkLedger()
    try:
        mvp_ingestion.search = _BenchmarkSearch(ledger)  # type: ignore[method-assign]
        default_result, default_latency, default_work = _measure_default(
            iterations=iterations, ledger=ledger
        )
        budgeted_result, budgeted_latency, budgeted_work = _measure_budgeted(
            iterations=iterations,
            budget=budget,
            tokenizer_enabled=False,
            ledger=ledger,
        )
        config_result, config_latency, config_work = _measure_budgeted(
            iterations=iterations,
            budget=budget,
            tokenizer_enabled=True,
            ledger=ledger,
        )
        chunking_result = _measure_chunking(ledger=ledger)
    finally:
        mvp_ingestion.search = original_search  # type: ignore[method-assign]
        mvp_ingestion._RUNTIME = original_runtime

    default_avg = float(default_latency["warm_avg"])
    budgeted_avg = float(budgeted_latency["warm_avg"])
    config_avg = float(config_latency["warm_avg"])
    budgeted_ratio = _safe_ratio(budgeted_avg, default_avg)
    config_ratio = _safe_ratio(config_avg, default_avg)

    threshold_failures: list[str] = []
    if max_budgeted_ratio is not None and budgeted_ratio > max_budgeted_ratio:
        threshold_failures.append(
            f"budgeted_request ratio {budgeted_ratio:.3f} exceeded {max_budgeted_ratio:.3f}"
        )
    if (
        max_config_enabled_ratio is not None
        and config_ratio > max_config_enabled_ratio
    ):
        threshold_failures.append(
            f"config_enabled ratio {config_ratio:.3f} exceeded {max_config_enabled_ratio:.3f}"
        )

    return {
        "iterations": iterations,
        "budget": budget,
        "latency_ms": {
            "default_unbudgeted": default_latency,
            "budgeted_request": budgeted_latency,
            "config_enabled": config_latency,
            "default_unbudgeted_avg": default_avg,
            "budgeted_request_avg": budgeted_avg,
            "config_enabled_avg": config_avg,
            "budgeted_request_ratio": budgeted_ratio,
            "config_enabled_ratio": config_ratio,
        },
        "selected_context": {
            "default_chunks": len(default_result.get("citations", [])),
            "budgeted_chunks": int(budgeted_result.get("chunks_selected", 0)),
            "config_enabled_chunks": int(config_result.get("chunks_selected", 0)),
            "budgeted_tokens": int(budgeted_result.get("context_tokens_used", 0)),
            "config_enabled_tokens": int(config_result.get("context_tokens_used", 0)),
            "budgeted_dropped": int(budgeted_result.get("chunks_dropped", 0)),
            "config_enabled_dropped": int(config_result.get("chunks_dropped", 0)),
        },
        "work_amplification": {
            "default_unbudgeted": _with_result_work(default_work, default_result),
            "budgeted_request": _with_result_work(budgeted_work, budgeted_result),
            "config_enabled": _with_result_work(config_work, config_result),
        },
        "chunking": chunking_result,
        "thresholds": {
            "max_budgeted_ratio": max_budgeted_ratio,
            "max_config_enabled_ratio": max_config_enabled_ratio,
            "failures": threshold_failures,
        },
    }


def assert_thresholds(report: dict[str, Any]) -> None:
    failures = report.get("thresholds", {}).get("failures", [])
    if failures:
        raise AssertionError("; ".join(str(item) for item in failures))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run token-budget performance checks.")
    parser.add_argument("--iterations", type=int, default=200)
    parser.add_argument("--budget", type=int, default=_BUDGET)
    parser.add_argument("--max-budgeted-ratio", type=float, default=None)
    parser.add_argument("--max-config-enabled-ratio", type=float, default=None)
    args = parser.parse_args(argv)

    report = run_benchmark(
        iterations=args.iterations,
        budget=args.budget,
        max_budgeted_ratio=args.max_budgeted_ratio,
        max_config_enabled_ratio=args.max_config_enabled_ratio,
    )
    print(json.dumps(report, indent=2, sort_keys=True))
    try:
        assert_thresholds(report)
    except AssertionError:
        return 1
    return 0


def _measure_default(
    *, iterations: int, ledger: WorkLedger
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    _configure_runtime(tokenizer_enabled=False, ledger=ledger)
    return _measure(lambda: mvp_ingestion.ask(_QUESTION, top_k=_TOP_K), iterations, ledger)


def _measure_budgeted(
    *, iterations: int, budget: int, tokenizer_enabled: bool, ledger: WorkLedger
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    _configure_runtime(
        tokenizer_enabled=tokenizer_enabled,
        ask_max_context_tokens=budget,
        ledger=ledger,
    )
    if tokenizer_enabled:
        return _measure(
            lambda: mvp_ingestion.ask(_QUESTION, top_k=_TOP_K), iterations, ledger
        )
    return _measure(
        lambda: mvp_ingestion.ask(
            _QUESTION, top_k=_TOP_K, max_context_tokens=budget
        ),
        iterations,
        ledger,
    )


def _measure(
    fn: Callable[[], dict[str, Any]], iterations: int, ledger: WorkLedger
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    ledger.reset()
    tokenizer._get_encoding.cache_clear()
    start = perf_counter()
    result = fn()
    _record_result_work(ledger, result)
    first_call_ms = (perf_counter() - start) * 1000.0
    first_call_work = ledger.snapshot(requests=1)
    ledger.reset()
    samples: list[float] = []
    for _ in range(iterations):
        start = perf_counter()
        result = fn()
        _record_result_work(ledger, result)
        samples.append((perf_counter() - start) * 1000.0)
    return result, _latency_summary(first_call_ms=first_call_ms, samples=samples), {
        "first_call": first_call_work,
        "warm": ledger.snapshot(requests=iterations),
    }


def _measure_chunking(*, ledger: WorkLedger) -> dict[str, Any]:
    message = {
        "role": "user",
        "hash": "sha256:" + ("a" * 64),
        "text": " ".join(f"token{i}" for i in range(120)),
    }
    obj = {
        "id": "00000000-0000-4000-8000-000000000099",
        "messages": [message],
    }

    _configure_runtime(tokenizer_enabled=False, chunking_strategy="message", ledger=ledger)
    default_chunks = mvp_ingestion.chunk_selected_messages(
        obj, obj["messages"], start_index=0
    )

    _configure_runtime(
        tokenizer_enabled=False,
        chunking_strategy="token",
        chunking_max_tokens=40,
        chunking_overlap_tokens=10,
        ledger=ledger,
    )
    token_chunks = mvp_ingestion.chunk_selected_messages(
        obj, obj["messages"], start_index=0
    )

    return {
        "message_strategy_chunks": len(default_chunks),
        "token_strategy_chunks": len(token_chunks),
        "token_strategy_total_chars": sum(len(str(chunk["text"])) for chunk in token_chunks),
    }


def _configure_runtime(
    *,
    tokenizer_enabled: bool,
    ask_max_context_tokens: int = _BUDGET,
    chunking_strategy: str = "message",
    chunking_max_tokens: int = 800,
    chunking_overlap_tokens: int = 80,
    ledger: WorkLedger,
) -> None:
    mvp_ingestion.configure_runtime(
        runtime=mvp_ingestion.RuntimeDependencies(
            embedding_provider=_Embedder(),
            metadata_store=_BenchmarkAuditStore(ledger),
            vector_store=object(),
            health_state={"mode": "ok"},
            tokenizer_enabled=tokenizer_enabled,
            tokenizer_encoding=_ENCODING,
            ask_max_context_tokens=ask_max_context_tokens,
            chunking_strategy=chunking_strategy,
            chunking_max_tokens=chunking_max_tokens,
            chunking_overlap_tokens=chunking_overlap_tokens,
        )
    )


def _safe_ratio(value: float, baseline: float) -> float:
    if baseline <= 0:
        return 0.0
    return value / baseline


def _latency_summary(*, first_call_ms: float, samples: list[float]) -> dict[str, Any]:
    cold_warm = cold_warm_latency_ms(
        cold_ms=first_call_ms,
        warm_samples=samples,
    )
    warm = cold_warm["warm_ms"]
    return {
        "cold_warm": cold_warm,
        "first_call": first_call_ms,
        "warm_avg": warm["avg"],
        "warm_min": warm["min"],
        "warm_p50": warm["p50"],
        "warm_p95": warm["p95"],
        "warm_p99": warm["p99"],
        "warm_max": warm["max"],
    }


def _with_result_work(work: dict[str, Any], result: dict[str, Any]) -> dict[str, Any]:
    selected_chunks = int(result.get("chunks_selected", len(result.get("citations", []))))
    candidates_per_request = float(work["warm"]["per_request"]["candidate_count"])
    return {
        **work,
        "result": {
            "returned_results": len(result.get("results", [])),
            "selected_chunks": selected_chunks,
            "dropped_chunks": int(result.get("chunks_dropped", 0)),
            "context_tokens": int(result.get("context_tokens_used", 0)),
            "candidate_to_selected_ratio": (
                candidates_per_request / selected_chunks if selected_chunks else 0.0
            ),
        },
    }


def _record_result_work(ledger: WorkLedger, result: dict[str, Any]) -> None:
    selected = int(result.get("chunks_selected", len(result.get("citations", []))))
    ledger.selected_chunks += selected
    ledger.context_tokens += int(result.get("context_tokens_used", 0))
    ledger.filtered_candidates += max(int(result.get("chunks_dropped", 0)), 0)


if __name__ == "__main__":
    raise SystemExit(main())
