from __future__ import annotations

import json

import pytest

from memory.benchmarks import scale_corpora
from memory.ingestion.validate import load_conversation_validator


def test_scale_profiles_are_deterministic_and_cover_filter_selectivity() -> None:
    report = scale_corpora.run_benchmark()

    assert report["measurement"] == "deterministic_synthetic_scale_corpora"
    assert report["data_classification"] == "synthetic"
    assert report["profiles"][0]["sha256"] == (
        "515bd045c2a16a7c49bd547402435f68650ab51dbd983e40b775da9d7b546999"
    )
    assert [profile["conversations"] for profile in report["profiles"]] == [
        128,
        4_096,
        32_768,
    ]
    for profile in report["profiles"]:
        cases = {case["name"]: case for case in profile["retrieval_cases"]}
        assert cases["unfiltered"]["selectivity"] == 1.0
        assert cases["low_selectivity_filter"]["selectivity"] == 0.5
        assert cases["high_selectivity_filter"]["selectivity"] == pytest.approx(
            1 / 128
        )
        assert len(profile["sha256"]) == 64


def test_generated_conversations_are_schema_valid_and_stable() -> None:
    first_run = list(scale_corpora.iter_conversations("small"))
    second_run = list(scale_corpora.iter_conversations("small"))
    validator = load_conversation_validator()

    assert first_run == second_run
    assert len(first_run) == 128
    for conversation in (first_run[0], first_run[1], first_run[-1]):
        validator.validate(conversation)
    assert first_run[0]["metadata"]["tags"] == [
        "efficiency-scale",
        "scale-common",
        "scale-rare",
    ]
    assert first_run[1]["metadata"]["tags"] == ["efficiency-scale"]


def test_write_corpus_emits_jsonl_and_matching_manifest(tmp_path) -> None:
    manifest = scale_corpora.write_corpus("small", tmp_path)
    jsonl_path = tmp_path / "small.jsonl"
    persisted_manifest = json.loads(
        (tmp_path / "small.manifest.json").read_text(encoding="utf-8")
    )
    rows = [json.loads(line) for line in jsonl_path.read_text(encoding="utf-8").splitlines()]

    assert persisted_manifest == manifest
    assert len(rows) == manifest["conversations"]
    assert rows[0] == next(scale_corpora.iter_conversations("small"))
    assert jsonl_path.stat().st_size == manifest["serialized_bytes"]


def test_unknown_profile_is_rejected() -> None:
    with pytest.raises(ValueError, match="unknown profile"):
        list(scale_corpora.iter_conversations("enormous"))
