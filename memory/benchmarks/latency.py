from __future__ import annotations

import math
from collections.abc import Sequence
from statistics import fmean
from typing import Any


def summarize_latency_ms(samples: Sequence[float]) -> dict[str, float | int]:
    """Return one stable nearest-rank latency distribution in milliseconds."""
    values = [float(sample) for sample in samples]
    if any(value < 0 for value in values):
        raise ValueError("latency samples must be non-negative")
    if not values:
        return {
            "samples": 0,
            "avg": 0.0,
            "total": 0.0,
            "min": 0.0,
            "p50": 0.0,
            "p95": 0.0,
            "p99": 0.0,
            "max": 0.0,
        }
    ordered = sorted(values)
    return {
        "samples": len(ordered),
        "avg": fmean(ordered),
        "total": sum(ordered),
        "min": ordered[0],
        "p50": percentile(ordered, 0.50),
        "p95": percentile(ordered, 0.95),
        "p99": percentile(ordered, 0.99),
        "max": ordered[-1],
    }


def cold_warm_latency_ms(
    *, cold_ms: float, warm_samples: Sequence[float]
) -> dict[str, Any]:
    """Separate first-call latency from a non-empty warm distribution."""
    if cold_ms < 0:
        raise ValueError("cold latency must be non-negative")
    if not warm_samples:
        raise ValueError("warm latency samples must not be empty")
    return {
        "cold_ms": float(cold_ms),
        "warm_ms": summarize_latency_ms(warm_samples),
    }


def percentile(ordered_samples: Sequence[float], quantile: float) -> float:
    """Return the nearest-rank percentile for an already ordered sample set."""
    if not 0.0 < quantile <= 1.0:
        raise ValueError("quantile must be greater than 0 and at most 1")
    if not ordered_samples:
        return 0.0
    index = min(
        math.ceil(len(ordered_samples) * quantile) - 1,
        len(ordered_samples) - 1,
    )
    return float(ordered_samples[index])
