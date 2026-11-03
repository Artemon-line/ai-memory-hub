from __future__ import annotations

import json
from types import ModuleType

import pytest

from memory.benchmarks import (
    concurrency_profiles,
    embedding_batch_efficiency,
    fact_hydration_efficiency,
    ingestion_efficiency,
    reranker_efficiency,
    retrieval_amplification,
)


@pytest.mark.parametrize(
    "module",
    [
        concurrency_profiles,
        embedding_batch_efficiency,
        fact_hydration_efficiency,
        ingestion_efficiency,
        reranker_efficiency,
        retrieval_amplification,
    ],
)
def test_benchmark_threshold_failure_keeps_stdout_machine_readable(
    module: ModuleType, monkeypatch, capsys
) -> None:
    monkeypatch.setattr(module, "run_benchmark", lambda **_kwargs: {"status": "report"})

    def fail(_report) -> None:
        raise AssertionError("threshold failed")

    monkeypatch.setattr(module, "assert_thresholds", fail)

    exit_code = module.main() if module is reranker_efficiency else module.main([])
    captured = capsys.readouterr()

    assert exit_code == 1
    assert json.loads(captured.out) == {"status": "report"}
    assert captured.err.strip() == "threshold failed"
