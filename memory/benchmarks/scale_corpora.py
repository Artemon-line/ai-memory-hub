from __future__ import annotations

import argparse
import hashlib
import json
from collections.abc import Iterator, Mapping
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import NAMESPACE_URL, uuid5

from memory.ingestion.mvp_ingestion import hash_ordered_messages, sha256_message

SCHEMA_VERSION = "1.0"
CORPUS_ID = "ai-memory-hub-efficiency-scale-v1"
PROFILE_SIZES: Mapping[str, int] = {
    "small": 128,
    "medium": 4_096,
    "large": 32_768,
}

_START = datetime(2026, 1, 1, tzinfo=UTC)
_SOURCES = ("codex", "manual", "mcp", "local-llm")
_RARE_INTERVAL = 128
_COMMON_INTERVAL = 2


def iter_conversations(profile: str) -> Iterator[dict[str, Any]]:
    """Yield a deterministic, schema-valid synthetic scale corpus."""
    size = _profile_size(profile)
    for index in range(size):
        yield _conversation(index)


def build_manifest(profile: str) -> dict[str, Any]:
    """Describe a corpus and fingerprint its canonical JSONL representation."""
    size = _profile_size(profile)
    digest = hashlib.sha256()
    byte_count = 0
    for conversation in iter_conversations(profile):
        encoded = _json_line(conversation)
        digest.update(encoded)
        byte_count += len(encoded)

    return {
        "schema_version": SCHEMA_VERSION,
        "corpus_id": CORPUS_ID,
        "data_classification": "synthetic",
        "profile": profile,
        "conversations": size,
        "messages": size * 2,
        "serialized_bytes": byte_count,
        "sha256": digest.hexdigest(),
        "generation": {
            "seed": "fixed-index-v1",
            "start_timestamp": _iso(_START),
            "rare_interval": _RARE_INTERVAL,
            "common_interval": _COMMON_INTERVAL,
        },
        "retrieval_cases": [
            {
                "name": "unfiltered",
                "query": "synthetic memory benchmark",
                "filters": {},
                "expected_matches": size,
                "selectivity": 1.0,
            },
            {
                "name": "low_selectivity_filter",
                "query": "synthetic memory benchmark",
                "filters": {"tags": ["scale-common"]},
                "expected_matches": _interval_count(size, _COMMON_INTERVAL),
                "selectivity": _interval_count(size, _COMMON_INTERVAL) / size,
            },
            {
                "name": "high_selectivity_filter",
                "query": "selective violet sentinel",
                "filters": {"tags": ["scale-rare"]},
                "expected_matches": _interval_count(size, _RARE_INTERVAL),
                "selectivity": _interval_count(size, _RARE_INTERVAL) / size,
            },
        ],
    }


def write_corpus(profile: str, output_dir: str | Path) -> dict[str, Any]:
    """Write a streamable JSONL corpus and its machine-readable manifest."""
    destination = Path(output_dir)
    destination.mkdir(parents=True, exist_ok=True)
    conversations_path = destination / f"{profile}.jsonl"
    manifest_path = destination / f"{profile}.manifest.json"

    with conversations_path.open("wb") as handle:
        for conversation in iter_conversations(profile):
            handle.write(_json_line(conversation))

    manifest = build_manifest(profile)
    manifest_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return manifest


def run_benchmark(*, profiles: tuple[str, ...] = tuple(PROFILE_SIZES)) -> dict[str, Any]:
    if not profiles:
        raise ValueError("at least one profile is required")
    return {
        "measurement": "deterministic_synthetic_scale_corpora",
        "corpus_id": CORPUS_ID,
        "data_classification": "synthetic",
        "profiles": [build_manifest(profile) for profile in profiles],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Describe or write deterministic synthetic efficiency corpora."
    )
    parser.add_argument(
        "--profile",
        choices=(*PROFILE_SIZES, "all"),
        default="all",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        help="Write JSONL corpus files and manifests to this directory.",
    )
    args = parser.parse_args(argv)
    profiles = tuple(PROFILE_SIZES) if args.profile == "all" else (args.profile,)
    if args.output_dir is not None:
        for profile in profiles:
            write_corpus(profile, args.output_dir)
    print(json.dumps(run_benchmark(profiles=profiles), indent=2, sort_keys=True))
    return 0


def _conversation(index: int) -> dict[str, Any]:
    timestamp = _iso(_START + timedelta(seconds=index))
    tags = ["efficiency-scale"]
    if index % _COMMON_INTERVAL == 0:
        tags.append("scale-common")
    rare = index % _RARE_INTERVAL == 0
    if rare:
        tags.append("scale-rare")

    user_text = (
        f"Synthetic memory benchmark record {index:05d} covers retrieval cohort "
        f"{index % 32:02d}."
    )
    assistant_text = (
        f"The selective violet sentinel belongs to record {index:05d}."
        if rare
        else f"Deterministic filler response for record {index:05d}."
    )
    messages = [
        {
            "role": "user",
            "text": user_text,
            "hash": sha256_message("user", user_text),
        },
        {
            "role": "assistant",
            "text": assistant_text,
            "hash": sha256_message("assistant", assistant_text),
        },
    ]
    return {
        "id": str(uuid5(NAMESPACE_URL, f"{CORPUS_ID}:{index}")),
        "source": _SOURCES[index % len(_SOURCES)],
        "timestamp": timestamp,
        "title": f"Synthetic scale record {index:05d}",
        "messages": messages,
        "metadata": {
            "imported_at": timestamp,
            "updated_at": timestamp,
            "conversation_hash": hash_ordered_messages(messages),
            "message_hashes": [message["hash"] for message in messages],
            "tags": tags,
            "project_id": f"synthetic-project-{index % 8}",
            "owner_id": f"synthetic-owner-{index % 4}",
            "source_client": "efficiency-benchmark",
        },
    }


def _profile_size(profile: str) -> int:
    try:
        return PROFILE_SIZES[profile]
    except KeyError as exc:
        choices = ", ".join(PROFILE_SIZES)
        raise ValueError(f"unknown profile {profile!r}; choose one of: {choices}") from exc


def _interval_count(size: int, interval: int) -> int:
    return ((size - 1) // interval) + 1


def _json_line(payload: dict[str, Any]) -> bytes:
    return (
        json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True) + "\n"
    ).encode("utf-8")


def _iso(value: datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")


if __name__ == "__main__":
    raise SystemExit(main())
