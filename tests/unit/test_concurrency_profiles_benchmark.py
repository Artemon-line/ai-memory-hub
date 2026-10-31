from __future__ import annotations

import pytest

from memory.benchmarks.concurrency_profiles import assert_thresholds, run_benchmark


def test_concurrency_profiles_complete_insert_search_and_ask_without_credentials() -> None:
    report = run_benchmark(
        concurrency_levels=(1, 2),
        query_requests=4,
        corpus_limit=8,
    )

    assert report["measurement"] == "credential_free_concurrency_profiles"
    assert report["deployment_profile"] == "local"
    assert report["providers"] == {
        "metadata": "sqlite",
        "vector": "memory",
        "embedding": "deterministic-hash",
        "answer": "deterministic-fallback",
    }
    assert [profile["concurrency"] for profile in report["profiles"]] == [1, 2]
    for profile in report["profiles"]:
        assert profile["result_counts"]["stored_conversations"] == 8
        assert profile["result_counts"]["stored_after_failed_request"] == 0
        assert profile["result_counts"]["vector_rows"] == 16
        assert profile["result_counts"]["search_results"] >= 1
        assert profile["result_counts"]["ask_citations"] >= 1
        assert profile["operations"]["insert"]["requests"] == 8
        for operation in profile["operations"].values():
            assert operation["failed"] == 0
            assert operation["completed"] == operation["requests"]
            assert operation["throughput_per_second"] > 0
            assert operation["latency_ms"]["p95"] >= 0
    assert_thresholds(report)

    report["thresholds"]["max_failure_rate"] = 0.0
    report["profiles"][0]["operations"]["ask"]["completed"] -= 1
    report["profiles"][0]["operations"]["ask"]["failed"] = 1
    with pytest.raises(AssertionError, match="failure rate"):
        assert_thresholds(report)


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"concurrency_levels": ()}, "positive integers"),
        ({"concurrency_levels": (1, 1)}, "unique"),
        ({"query_requests": 0}, "query_requests"),
        ({"corpus_limit": 0}, "corpus_limit"),
        ({"corpus_limit": 129}, "corpus_limit"),
        ({"max_failure_rate": 1.1}, "max_failure_rate"),
    ],
)
def test_concurrency_profiles_reject_invalid_bounds(
    kwargs: dict[str, object], message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        run_benchmark(**kwargs)  # type: ignore[arg-type]
