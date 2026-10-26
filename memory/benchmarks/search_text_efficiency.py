from __future__ import annotations

import argparse
import json
from typing import Any

from memory.ingestion import mvp_ingestion

_FACT_COUNT = 120
_CONVERSATION_COUNT = 3


def run_benchmark() -> dict[str, Any]:
    """Measure repeated source-conversation search-text construction."""
    source_conversations = {
        _memory_id(index): {
            "id": _memory_id(index),
            "source": "benchmark",
            "title": f"Benchmark conversation {index}",
            "messages": [
                {"role": "user", "text": f"Benchmark source record {index}."}
            ],
            "metadata": {"topics": ["benchmark", "efficiency"]},
        }
        for index in range(_CONVERSATION_COUNT)
    }
    facts = [
        {
            "id": f"fact-{index}",
            "subject": "benchmark",
            "predicate": "description",
            "object": f"benchmark record {index}",
            "source_conversation_id": _memory_id(index % _CONVERSATION_COUNT),
        }
        for index in range(_FACT_COUNT)
    ]
    conversation_search_texts: dict[int, str] = {}

    ranked = mvp_ingestion._rank_facts_by_text_query(
        facts,
        "benchmark record",
        source_conversations=source_conversations,
        conversation_search_texts=conversation_search_texts,
    )

    previous_constructions = _FACT_COUNT
    optimized_constructions = len(conversation_search_texts)
    return {
        "measurement": "deterministic_work_amplification",
        "fixture": {
            "facts": _FACT_COUNT,
            "unique_source_conversations": _CONVERSATION_COUNT,
            "results": len(ranked),
        },
        "previous_path": {
            "conversation_search_text_constructions": previous_constructions,
            "reason": "one source conversation text construction per ranked fact",
        },
        "optimized_path": {
            "conversation_search_text_constructions": optimized_constructions,
            "request_local_cache_entries": len(conversation_search_texts),
        },
        "reductions": {
            "conversation_search_text_constructions": _reduction(
                previous_constructions, optimized_constructions
            ),
        },
    }


def assert_thresholds(report: dict[str, Any]) -> None:
    fixture = report["fixture"]
    optimized = report["optimized_path"]
    if fixture["results"] != fixture["facts"]:
        raise AssertionError("search-text reuse must preserve every ranked fact")
    if (
        optimized["conversation_search_text_constructions"]
        != fixture["unique_source_conversations"]
    ):
        raise AssertionError("search text must be constructed once per source conversation")
    if report["reductions"]["conversation_search_text_constructions"] < 0.2:
        raise AssertionError("search-text construction work must fall by at least 20%")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Measure request-local conversation search-text reuse."
    )
    parser.parse_args(argv)
    report = run_benchmark()
    print(json.dumps(report, indent=2, sort_keys=True))
    try:
        assert_thresholds(report)
    except AssertionError as exc:
        print(str(exc))
        return 1
    return 0


def _memory_id(index: int) -> str:
    return f"00000000-0000-4000-8000-{index:012d}"


def _reduction(baseline: int, optimized: int) -> float:
    if baseline <= 0:
        return 0.0
    return 1.0 - (optimized / baseline)


if __name__ == "__main__":
    raise SystemExit(main())
