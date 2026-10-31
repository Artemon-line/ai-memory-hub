from __future__ import annotations

import pytest

from memory.benchmarks.latency import cold_warm_latency_ms, summarize_latency_ms


def test_latency_summary_uses_stable_nearest_rank_percentiles() -> None:
    assert summarize_latency_ms([9.0, 1.0, 5.0, 3.0]) == {
        "samples": 4,
        "avg": 4.5,
        "total": 18.0,
        "min": 1.0,
        "p50": 3.0,
        "p95": 9.0,
        "p99": 9.0,
        "max": 9.0,
    }


def test_latency_summary_has_explicit_empty_distribution() -> None:
    assert summarize_latency_ms([]) == {
        "samples": 0,
        "avg": 0.0,
        "total": 0.0,
        "min": 0.0,
        "p50": 0.0,
        "p95": 0.0,
        "p99": 0.0,
        "max": 0.0,
    }


def test_cold_warm_summary_separates_first_call_from_warm_samples() -> None:
    report = cold_warm_latency_ms(cold_ms=12.0, warm_samples=[2.0, 1.0])

    assert report["cold_ms"] == 12.0
    assert report["warm_ms"]["samples"] == 2
    assert report["warm_ms"]["p50"] == 1.0
    assert report["warm_ms"]["p99"] == 2.0


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"cold_ms": -1.0, "warm_samples": [1.0]}, "cold"),
        ({"cold_ms": 1.0, "warm_samples": []}, "warm"),
    ],
)
def test_cold_warm_summary_rejects_invalid_measurements(
    kwargs: dict[str, object], message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        cold_warm_latency_ms(**kwargs)  # type: ignore[arg-type]


def test_latency_summary_rejects_negative_samples() -> None:
    with pytest.raises(ValueError, match="non-negative"):
        summarize_latency_ms([1.0, -1.0])
