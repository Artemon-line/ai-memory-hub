from __future__ import annotations

import argparse
import json
from statistics import mean
from time import perf_counter
from typing import Any, Callable

from memory.ingestion import mvp_ingestion

_QUESTION = "What stored implementation details mention token budgets?"
_TOP_K = 8
_BUDGET = 80
_ENCODING = "cl100k_base"


class _Embedder:
    dimension = 1

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        return [[1.0] for _ in texts]


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
    try:
        mvp_ingestion.search = _fixed_search  # type: ignore[method-assign]
        default_result, default_ms = _measure_default(iterations=iterations)
        budgeted_result, budgeted_ms = _measure_budgeted(
            iterations=iterations, budget=budget, tokenizer_enabled=False
        )
        config_result, config_ms = _measure_budgeted(
            iterations=iterations, budget=budget, tokenizer_enabled=True
        )
        chunking_result = _measure_chunking()
    finally:
        mvp_ingestion.search = original_search  # type: ignore[method-assign]
        mvp_ingestion._RUNTIME = original_runtime

    default_avg = mean(default_ms)
    budgeted_avg = mean(budgeted_ms)
    config_avg = mean(config_ms)
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


def _fixed_search(query: str, top_k: int = 5) -> dict[str, Any]:
    _ = query
    return {"status": "ok", "results": _FIXED_MATCHES[:top_k]}


def _measure_default(*, iterations: int) -> tuple[dict[str, Any], list[float]]:
    _configure_runtime(tokenizer_enabled=False)
    return _measure(lambda: mvp_ingestion.ask(_QUESTION, top_k=_TOP_K), iterations)


def _measure_budgeted(
    *, iterations: int, budget: int, tokenizer_enabled: bool
) -> tuple[dict[str, Any], list[float]]:
    _configure_runtime(tokenizer_enabled=tokenizer_enabled, ask_max_context_tokens=budget)
    if tokenizer_enabled:
        return _measure(lambda: mvp_ingestion.ask(_QUESTION, top_k=_TOP_K), iterations)
    return _measure(
        lambda: mvp_ingestion.ask(
            _QUESTION, top_k=_TOP_K, max_context_tokens=budget
        ),
        iterations,
    )


def _measure(
    fn: Callable[[], dict[str, Any]], iterations: int
) -> tuple[dict[str, Any], list[float]]:
    result = fn()
    samples: list[float] = []
    for _ in range(iterations):
        start = perf_counter()
        result = fn()
        samples.append((perf_counter() - start) * 1000.0)
    return result, samples


def _measure_chunking() -> dict[str, Any]:
    message = {
        "role": "user",
        "hash": "sha256:" + ("a" * 64),
        "text": " ".join(f"token{i}" for i in range(120)),
    }
    obj = {
        "id": "00000000-0000-4000-8000-000000000099",
        "messages": [message],
    }

    _configure_runtime(tokenizer_enabled=False, chunking_strategy="message")
    default_chunks = mvp_ingestion.chunk_selected_messages(
        obj, obj["messages"], start_index=0
    )

    _configure_runtime(
        tokenizer_enabled=False,
        chunking_strategy="token",
        chunking_max_tokens=40,
        chunking_overlap_tokens=10,
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
) -> None:
    mvp_ingestion.configure_runtime(
        runtime=mvp_ingestion.RuntimeDependencies(
            embedding_provider=_Embedder(),
            metadata_store=object(),
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


if __name__ == "__main__":
    raise SystemExit(main())
