from __future__ import annotations

import json
from pathlib import Path

from memory.ingestion import validate


def _write_schema(path: Path, *, marker: str) -> None:
    path.write_text(
        json.dumps({"type": "object", "title": marker}),
        encoding="utf-8",
    )


def test_load_schema_returns_isolated_values(tmp_path: Path) -> None:
    schema_path = tmp_path / "conversation.schema.json"
    _write_schema(schema_path, marker="original")
    validate.clear_schema_cache()

    first = validate.load_schema(schema_path)
    first["title"] = "mutated"
    second = validate.load_schema(schema_path)

    assert second["title"] == "original"


def test_load_schema_invalidates_when_file_identity_changes(tmp_path: Path) -> None:
    schema_path = tmp_path / "conversation.schema.json"
    _write_schema(schema_path, marker="first")
    validate.clear_schema_cache()

    assert validate.load_schema(schema_path)["title"] == "first"
    _write_schema(schema_path, marker="second-version")

    assert validate.load_schema(schema_path)["title"] == "second-version"


def test_schema_cache_keeps_configured_paths_isolated(tmp_path: Path) -> None:
    first_path = tmp_path / "first.schema.json"
    second_path = tmp_path / "second.schema.json"
    _write_schema(first_path, marker="first")
    _write_schema(second_path, marker="second")
    validate.clear_schema_cache()

    assert validate.load_schema(first_path)["title"] == "first"
    assert validate.load_schema(second_path)["title"] == "second"


def test_compiled_schema_copies_do_not_share_caller_mutations(tmp_path: Path) -> None:
    schema_path = tmp_path / "conversation.schema.json"
    _write_schema(schema_path, marker="original")
    validate.clear_schema_cache()

    first_schema, first_validator = validate.load_compiled_conversation_schema(schema_path)
    first_schema["title"] = "mutated"
    second_schema, second_validator = validate.load_compiled_conversation_schema(schema_path)

    assert second_schema["title"] == "original"
    assert second_validator is first_validator


def test_clear_schema_cache_explicitly_invalidates_entries(tmp_path: Path) -> None:
    schema_path = tmp_path / "conversation.schema.json"
    _write_schema(schema_path, marker="schema")
    validate.clear_schema_cache()
    validate.load_conversation_validator(schema_path)

    validate.clear_schema_cache()

    assert validate.schema_cache_info()["currsize"] == 0
    assert validate.schema_cache_info()["hits"] == 0
    assert validate.schema_cache_info()["misses"] == 0


def test_validator_cache_invalidates_and_keeps_paths_isolated(tmp_path: Path) -> None:
    first_path = tmp_path / "first.schema.json"
    second_path = tmp_path / "second.schema.json"
    _write_schema(first_path, marker="first")
    _write_schema(second_path, marker="second")
    validate.clear_schema_cache()

    first = validate.load_conversation_validator(first_path)
    assert validate.load_conversation_validator(first_path) is first
    second = validate.load_conversation_validator(second_path)
    assert second is not first

    _write_schema(first_path, marker="first-version-two")
    replacement = validate.load_conversation_validator(first_path)

    assert replacement is not first
    assert validate.schema_cache_info() == {
        "hits": 1,
        "misses": 3,
        "maxsize": 16,
        "currsize": 3,
    }
