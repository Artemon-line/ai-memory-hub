from __future__ import annotations

import argparse
import json
from typing import Any

from memory.ingestion.mvp_ingestion import _adaptive_candidate_limit


def run_benchmark(
    *,
    top_k: int = 5,
    candidate_multiplier: int = 3,
    keyword_limit: int = 50,
    fact_limit: int = 25,
) -> dict[str, Any]:
    """Report deterministic candidate-admission budgets before and after adaptation."""
    previous = {"lexical": keyword_limit, "fact": fact_limit}
    optimized = {
        "lexical": _adaptive_candidate_limit(
            configured_limit=keyword_limit,
            top_k=top_k,
            candidate_multiplier=candidate_multiplier,
            preserve_configured_limit=False,
        ),
        "fact": _adaptive_candidate_limit(
            configured_limit=fact_limit,
            top_k=top_k,
            candidate_multiplier=candidate_multiplier,
            preserve_configured_limit=False,
        ),
    }
    filtered = {
        "lexical": _adaptive_candidate_limit(
            configured_limit=keyword_limit,
            top_k=top_k,
            candidate_multiplier=candidate_multiplier,
            preserve_configured_limit=True,
        ),
        "fact": _adaptive_candidate_limit(
            configured_limit=fact_limit,
            top_k=top_k,
            candidate_multiplier=candidate_multiplier,
            preserve_configured_limit=True,
        ),
    }
    previous_total = sum(previous.values())
    optimized_total = sum(optimized.values())
    return {
        "measurement": "deterministic_candidate_admission_budget",
        "request": {
            "top_k": top_k,
            "candidate_multiplier": candidate_multiplier,
        },
        "previous_unfiltered_limits": {
            **previous,
            "total": previous_total,
        },
        "optimized_unfiltered_limits": {
            **optimized,
            "total": optimized_total,
        },
        "filtered_limits": {
            **filtered,
            "total": sum(filtered.values()),
        },
        "unfiltered_candidate_budget_reduction": _reduction(
            previous_total,
            optimized_total,
        ),
    }


def assert_thresholds(report: dict[str, Any]) -> None:
    previous = report["previous_unfiltered_limits"]
    optimized = report["optimized_unfiltered_limits"]
    filtered = report["filtered_limits"]
    if optimized["total"] >= previous["total"]:
        raise AssertionError("unfiltered candidate admission budget must decrease")
    if filtered["lexical"] != previous["lexical"] or filtered["fact"] != previous["fact"]:
        raise AssertionError("filtered searches must preserve configured candidate limits")
    if report["unfiltered_candidate_budget_reduction"] < 0.25:
        raise AssertionError("unfiltered candidate admission budget must fall by at least 25%")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Measure retrieval candidate admission budgets."
    )
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--candidate-multiplier", type=int, default=3)
    args = parser.parse_args(argv)
    report = run_benchmark(
        top_k=args.top_k,
        candidate_multiplier=args.candidate_multiplier,
    )
    print(json.dumps(report, indent=2, sort_keys=True))
    try:
        assert_thresholds(report)
    except AssertionError as exc:
        print(str(exc))
        return 1
    return 0


def _reduction(previous: int, optimized: int) -> float:
    if previous <= 0:
        return 0.0
    return 1.0 - (optimized / previous)


if __name__ == "__main__":
    raise SystemExit(main())
