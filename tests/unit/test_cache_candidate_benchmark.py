from __future__ import annotations

import pytest

from memory.benchmarks.cache_candidates import profile_benchmark, run_benchmark


def test_cache_candidate_benchmark_reports_ranked_operations() -> None:
    report = run_benchmark(iterations=2)

    assert report["iterations"] == 2
    assert set(report["operations"]) == {
        "schema_load",
        "schema_compatibility",
        "config_parse",
        "token_count",
    }
    assert report["candidates"]
    assert "operation" in report["candidates"][0]
    assert "cache_when" in report["candidates"][0]


def test_cache_candidate_benchmark_rejects_invalid_iterations() -> None:
    with pytest.raises(ValueError, match="iterations"):
        run_benchmark(iterations=0)


def test_cache_candidate_profile_report_includes_top_cumulative() -> None:
    report = profile_benchmark(iterations=1, profile_out=None)

    assert report["profile_top_cumulative"]


def test_cache_candidate_profile_report_can_write_stats(tmp_path) -> None:
    profile_out = tmp_path / "cache-candidates.prof"

    report = profile_benchmark(iterations=1, profile_out=str(profile_out))

    assert report["profile_out"] == str(profile_out)
    assert profile_out.exists()
