from __future__ import annotations

import argparse
import json
from typing import Any

from memory.benchmarks.work_ledger import WorkLedger
from memory.ingestion import mvp_ingestion

_ENCODING = "cl100k_base"
_EXPECTED_TEXT = "alpha beta gamma delta epsilon zeta eta theta iota kappa"


def run_benchmark(*, minimum_reduction_percent: float = 1.0) -> dict[str, Any]:
    conversation = {
        "metadata": {
            "index_chunks": [
                {"chunk_index": 0, "message_hash": "sha256:benchmark-message"},
                {"chunk_index": 1, "message_hash": "sha256:benchmark-message"},
            ]
        }
    }
    matches = [
        {
            "id": "00000000-0000-4000-8000-000000000001",
            "chunk_index": 0,
            "score": 0.01,
            "text": "alpha beta gamma delta epsilon zeta",
            "conversation": conversation,
        },
        {
            "id": "00000000-0000-4000-8000-000000000001",
            "chunk_index": 1,
            "score": 0.02,
            "text": "epsilon zeta eta theta iota kappa",
            "conversation": conversation,
        },
    ]
    baseline_matches = [{**row, "conversation": None} for row in matches]
    baseline = _measure_context(baseline_matches)
    optimized = _measure_context(matches)
    tokens_avoided = baseline["tokens"] - optimized["tokens"]
    reduction_percent = (
        (tokens_avoided / baseline["tokens"]) * 100.0 if baseline["tokens"] else 0.0
    )
    unique_evidence_preserved = optimized["text"] == _EXPECTED_TEXT
    failures: list[str] = []
    if reduction_percent < minimum_reduction_percent:
        failures.append(
            f"context token reduction {reduction_percent:.1f}% was below "
            f"{minimum_reduction_percent:.1f}%"
        )
    if not unique_evidence_preserved:
        failures.append("optimized context did not preserve the unique evidence text")
    return {
        "before": baseline,
        "after": optimized,
        "tokens_avoided": tokens_avoided,
        "reduction_percent": reduction_percent,
        "unique_evidence_preserved": unique_evidence_preserved,
        "thresholds": {
            "minimum_reduction_percent": minimum_reduction_percent,
            "failures": failures,
        },
    }


def assert_thresholds(report: dict[str, Any]) -> None:
    failures = report.get("thresholds", {}).get("failures", [])
    if failures:
        raise AssertionError("; ".join(str(item) for item in failures))


def _measure_context(matches: list[dict[str, Any]]) -> dict[str, Any]:
    selected, _citations, _lines, tokens, dropped, truncated = (
        mvp_ingestion._select_ask_context(
            matches=matches,
            max_context_tokens=1_000,
            encoding=_ENCODING,
        )
    )
    ledger = WorkLedger(
        candidate_count=len(matches),
        filtered_candidates=dropped,
        selected_chunks=len(selected),
        context_tokens=tokens,
    )
    return {
        "tokens": tokens,
        "chunks": len(selected),
        "dropped": dropped,
        "truncated": truncated,
        "text": " ".join(str(row.get("text", "")).strip() for row in selected),
        "work": ledger.snapshot(),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Measure overlapping ask-context work.")
    parser.add_argument("--minimum-reduction-percent", type=float, default=1.0)
    args = parser.parse_args(argv)
    report = run_benchmark(minimum_reduction_percent=args.minimum_reduction_percent)
    print(json.dumps(report, indent=2, sort_keys=True))
    try:
        assert_thresholds(report)
    except AssertionError:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
