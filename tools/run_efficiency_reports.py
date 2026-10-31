from __future__ import annotations

import argparse
import json
import os
import platform
import sys
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter
from typing import Any

_REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(_REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPOSITORY_ROOT))

from memory.benchmarks import (  # noqa: E402
    append_graph_efficiency,
    append_hash_efficiency,
    cache_candidates,
    concurrency_profiles,
    context_overlap_efficiency,
    embedding_batch_efficiency,
    fact_hydration_efficiency,
    ingestion_efficiency,
    quality_gate,
    reranker_efficiency,
    retrieval_amplification,
    scale_corpora,
    search_text_efficiency,
    token_budget,
)

Report = dict[str, Any]
ReportBuilder = Callable[[], Report]
ReportValidator = Callable[[Report], None]


@dataclass(frozen=True)
class ReportSpec:
    name: str
    build: ReportBuilder
    validate: ReportValidator | None = None


@dataclass(frozen=True)
class SuiteConfig:
    profile: str
    corpus_limit: int
    query_requests: int
    concurrency_levels: tuple[int, ...]
    cache_iterations: int
    token_iterations: int


def run_report_suite(*, output_dir: str | Path, config: SuiteConfig) -> Report:
    destination = Path(output_dir)
    destination.mkdir(parents=True, exist_ok=True)
    started_at = _utc_now()
    entries = [
        _run_report(spec, output_dir=destination)
        for spec in _report_specs(config)
    ]
    manifest = {
        "report_schema_version": "1.0",
        "suite": "efficiency-revival",
        "status": "passed"
        if all(entry["status"] == "passed" for entry in entries)
        else "failed",
        "started_at": started_at,
        "completed_at": _utc_now(),
        "source": {
            "commit_sha": os.environ.get("GITHUB_SHA", "local"),
            "run_id": os.environ.get("GITHUB_RUN_ID", "local"),
            "run_attempt": os.environ.get("GITHUB_RUN_ATTEMPT", "local"),
        },
        "runner": {
            "os": os.environ.get("RUNNER_OS", platform.system()),
            "architecture": os.environ.get("RUNNER_ARCH", platform.machine()),
            "python": platform.python_version(),
        },
        "configuration": {
            "profile": config.profile,
            "corpus_limit": config.corpus_limit,
            "query_requests": config.query_requests,
            "concurrency_levels": list(config.concurrency_levels),
            "cache_iterations": config.cache_iterations,
            "token_iterations": config.token_iterations,
        },
        "reports": entries,
    }
    _write_json(destination / "manifest.json", manifest)
    return manifest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Write the machine-readable efficiency report suite."
    )
    parser.add_argument("--output-dir", type=Path, default=Path("reports/efficiency"))
    parser.add_argument("--profile", choices=tuple(scale_corpora.PROFILE_SIZES), default="small")
    parser.add_argument("--corpus-limit", type=int, default=128)
    parser.add_argument("--query-requests", type=int, default=32)
    parser.add_argument("--concurrency", default="1,4,16,32")
    parser.add_argument("--cache-iterations", type=int, default=1_000)
    parser.add_argument("--token-iterations", type=int, default=200)
    args = parser.parse_args(argv)
    config = SuiteConfig(
        profile=args.profile,
        corpus_limit=args.corpus_limit,
        query_requests=args.query_requests,
        concurrency_levels=_parse_concurrency(args.concurrency),
        cache_iterations=args.cache_iterations,
        token_iterations=args.token_iterations,
    )
    manifest = run_report_suite(output_dir=args.output_dir, config=config)
    print(json.dumps(manifest, indent=2, sort_keys=True))
    return 0 if manifest["status"] == "passed" else 1


def _report_specs(config: SuiteConfig) -> tuple[ReportSpec, ...]:
    return (
        ReportSpec(
            "scale-corpora",
            lambda: scale_corpora.run_benchmark(profiles=(config.profile,)),
        ),
        ReportSpec(
            "cache-candidates",
            lambda: cache_candidates.run_benchmark(iterations=config.cache_iterations),
        ),
        ReportSpec(
            "token-budget",
            lambda: token_budget.run_benchmark(iterations=config.token_iterations),
            token_budget.assert_thresholds,
        ),
        ReportSpec("quality-gate", quality_gate.run_evaluation, quality_gate.assert_thresholds),
        ReportSpec(
            "ingestion-efficiency",
            ingestion_efficiency.run_benchmark,
            ingestion_efficiency.assert_thresholds,
        ),
        ReportSpec(
            "append-graph-efficiency",
            append_graph_efficiency.run_benchmark,
            append_graph_efficiency.assert_thresholds,
        ),
        ReportSpec(
            "append-hash-efficiency",
            append_hash_efficiency.run_benchmark,
            append_hash_efficiency.assert_thresholds,
        ),
        ReportSpec(
            "embedding-batch-efficiency",
            embedding_batch_efficiency.run_benchmark,
            embedding_batch_efficiency.assert_thresholds,
        ),
        ReportSpec(
            "fact-hydration-efficiency",
            fact_hydration_efficiency.run_benchmark,
            fact_hydration_efficiency.assert_thresholds,
        ),
        ReportSpec(
            "search-text-efficiency",
            search_text_efficiency.run_benchmark,
            search_text_efficiency.assert_thresholds,
        ),
        ReportSpec(
            "retrieval-amplification",
            retrieval_amplification.run_benchmark,
            retrieval_amplification.assert_thresholds,
        ),
        ReportSpec(
            "reranker-efficiency",
            reranker_efficiency.run_benchmark,
            reranker_efficiency.assert_thresholds,
        ),
        ReportSpec(
            "context-overlap-efficiency",
            context_overlap_efficiency.run_benchmark,
            context_overlap_efficiency.assert_thresholds,
        ),
        ReportSpec(
            "concurrency-profiles",
            lambda: concurrency_profiles.run_benchmark(
                profile=config.profile,
                concurrency_levels=config.concurrency_levels,
                query_requests=config.query_requests,
                corpus_limit=config.corpus_limit,
            ),
            concurrency_profiles.assert_thresholds,
        ),
    )


def _run_report(spec: ReportSpec, *, output_dir: Path) -> Report:
    started = perf_counter()
    report_path = output_dir / f"{spec.name}.json"
    try:
        report = spec.build()
    except Exception as exc:
        error_report = {
            "report_schema_version": "1.0",
            "status": "error",
            "error_type": type(exc).__name__,
        }
        _write_json(report_path, error_report)
        return _manifest_entry(
            spec.name,
            report_path,
            status="error",
            started=started,
            error_type=type(exc).__name__,
        )

    _write_json(report_path, report)
    if spec.validate is None:
        return _manifest_entry(spec.name, report_path, status="passed", started=started)
    try:
        spec.validate(report)
    except AssertionError as exc:
        return _manifest_entry(
            spec.name,
            report_path,
            status="threshold_failed",
            started=started,
            error_type=type(exc).__name__,
        )
    return _manifest_entry(spec.name, report_path, status="passed", started=started)


def _manifest_entry(
    name: str,
    report_path: Path,
    *,
    status: str,
    started: float,
    error_type: str | None = None,
) -> Report:
    entry: Report = {
        "name": name,
        "file": report_path.name,
        "status": status,
        "duration_ms": (perf_counter() - started) * 1_000,
    }
    if error_type is not None:
        entry["error_type"] = error_type
    return entry


def _parse_concurrency(raw: str) -> tuple[int, ...]:
    values = tuple(int(item) for item in raw.replace(",", " ").split())
    if not values or any(value < 1 for value in values):
        raise ValueError("concurrency must contain positive integers")
    if len(set(values)) != len(values):
        raise ValueError("concurrency values must be unique")
    return values


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


if __name__ == "__main__":
    raise SystemExit(main())
