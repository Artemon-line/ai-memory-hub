from __future__ import annotations

import argparse
import cProfile
import io
import json
import math
import pstats
from statistics import mean
from time import perf_counter
from typing import Any, Callable

from memory.config import parse_config
from memory.ingestion import validate
from memory.ingestion.tokenizer import count_tokens

_TEXT = "Token budgets, schema validation, and config parsing are cache candidates."
_CONFIG = {
    "providers": {
        "embeddings": "local",
        "embedding_dimension": 32,
        "metadata_db": "sqlite",
        "vector_db": "memory",
    },
    "tokenizer": {"encoding": "cl100k_base"},
}
_HASH = f"sha256:{'0' * 64}"
_VALID_PAYLOAD = {
    "id": "d9fd4c95-9cb3-4fd5-b967-3027f8863210",
    "source": "benchmark",
    "timestamp": "2026-01-01T00:00:00Z",
    "messages": [{"role": "user", "text": "benchmark", "hash": _HASH}],
    "metadata": {
        "imported_at": "2026-01-01T00:00:00Z",
        "updated_at": "2026-01-01T00:00:00Z",
        "conversation_hash": _HASH,
    },
}


def run_benchmark(*, iterations: int = 1000) -> dict[str, Any]:
    if iterations < 1:
        raise ValueError("iterations must be positive")

    validation_iterations = min(iterations, 100)
    original_schema = validate.load_schema()
    compiled_validator = validate.load_conversation_validator()
    measurements = {
        "schema_load": _measure(validate.load_schema, iterations),
        "schema_compatibility": _measure(
            lambda: validate.validate_schema_compatibility(original_schema),
            iterations,
        ),
        "schema_validator_load": _measure(validate.load_conversation_validator, iterations),
        "schema_validation_rebuild": _measure(
            lambda: validate.validate_conversation(_VALID_PAYLOAD, original_schema),
            validation_iterations,
        ),
        "schema_validation_reused": _measure(
            lambda: validate.validate_conversation(
                _VALID_PAYLOAD,
                validator=compiled_validator,
            ),
            validation_iterations,
        ),
        "config_parse": _measure(lambda: parse_config(_CONFIG), iterations),
        "token_count": _measure(lambda: count_tokens(_TEXT, "cl100k_base"), iterations),
    }
    cache_info = {
        "schema": validate.schema_cache_info(),
        "tokenizer": _tokenizer_cache_info(),
    }
    return {
        "iterations": iterations,
        "validation_iterations": validation_iterations,
        "operations": measurements,
        "cache_info": cache_info,
        "candidates": _candidate_summary(measurements),
    }


def profile_benchmark(*, iterations: int, profile_out: str | None) -> dict[str, Any]:
    profiler = cProfile.Profile()
    profiler.enable()
    report = run_benchmark(iterations=iterations)
    profiler.disable()
    if profile_out:
        profiler.dump_stats(profile_out)
        report["profile_out"] = profile_out
    report["profile_top_cumulative"] = _profile_top(profiler, limit=15)
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Measure repeated cache-candidate operations.")
    parser.add_argument("--iterations", type=int, default=1000)
    parser.add_argument(
        "--profile-out",
        default=None,
        help="Optional path for cProfile stats output.",
    )
    args = parser.parse_args(argv)
    report = profile_benchmark(iterations=args.iterations, profile_out=args.profile_out)
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


def _measure(fn: Callable[[], Any], iterations: int) -> dict[str, float]:
    first_start = perf_counter()
    fn()
    first_ms = (perf_counter() - first_start) * 1000.0
    samples: list[float] = []
    for _ in range(iterations):
        start = perf_counter()
        fn()
        samples.append((perf_counter() - start) * 1000.0)
    ordered = sorted(samples)
    return {
        "samples": len(ordered),
        "first_call_ms": first_ms,
        "avg_ms": mean(ordered),
        "total_ms": sum(samples),
        "min_ms": ordered[0],
        "p50_ms": _percentile(ordered, 0.50),
        "p95_ms": _percentile(ordered, 0.95),
        "p99_ms": _percentile(ordered, 0.99),
        "max_ms": ordered[-1],
    }


def _percentile(ordered: list[float], percentile: float) -> float:
    return ordered[min(math.ceil(len(ordered) * percentile) - 1, len(ordered) - 1)]


def _candidate_summary(measurements: dict[str, dict[str, float]]) -> list[dict[str, Any]]:
    ranked = sorted(
        measurements.items(),
        key=lambda item: item[1]["avg_ms"],
        reverse=True,
    )
    return [
        {
            "operation": name,
            "samples": values["samples"],
            "total_ms": values["total_ms"],
            "avg_ms": values["avg_ms"],
            "cache_when": _cache_guidance(name),
        }
        for name, values in ranked
    ]


def _cache_guidance(name: str) -> str:
    if name == "schema_load":
        return "Startup-only in production; keep uncached unless request-path traces change."
    if name == "schema_validation_rebuild":
        return "Reuse one checked validator per runtime instead of rebuilding per payload."
    if name == "schema_validator_load":
        return "Cached by resolved path, modification time, and size with explicit invalidation."
    if name == "schema_validation_reused":
        return "Already optimized through runtime-scoped compiled validator reuse."
    if name == "config_parse":
        return "Cache only per explicit config path; avoid stale config during tests/dev reloads."
    if name == "token_count":
        return "Cache only for repeated exact text+encoding pairs; bound size tightly."
    return "Usually cheap; cache only if profile_top_cumulative confirms it matters."


def _tokenizer_cache_info() -> dict[str, Any]:
    try:
        from memory.ingestion import tokenizer

        info = tokenizer._get_encoding.cache_info()
    except Exception:
        return {}
    return {
        "get_encoding_hits": info.hits,
        "get_encoding_misses": info.misses,
        "get_encoding_maxsize": info.maxsize,
        "get_encoding_currsize": info.currsize,
    }


def _profile_top(profiler: cProfile.Profile, *, limit: int) -> list[str]:
    stream = io.StringIO()
    stats = pstats.Stats(profiler, stream=stream).sort_stats("cumulative")
    stats.print_stats(limit)
    return [line for line in stream.getvalue().splitlines() if line.strip()]


if __name__ == "__main__":
    raise SystemExit(main())
