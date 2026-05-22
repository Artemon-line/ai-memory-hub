from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import jsonschema  # pyright: ignore[reportMissingModuleSource]

SCHEMA_PATH = Path(__file__).resolve().parents[1] / "schema" / "conversation.schema.json"
_ACTIVE_SCHEMA_PATH: Path = SCHEMA_PATH


def load_schema() -> dict[str, Any]:
    """Load the conversation JSON schema from disk."""
    with _ACTIVE_SCHEMA_PATH.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def set_schema_path(path: str | Path | None) -> None:
    global _ACTIVE_SCHEMA_PATH
    if path is None:
        _ACTIVE_SCHEMA_PATH = SCHEMA_PATH
        return
    _ACTIVE_SCHEMA_PATH = Path(path)


def validate_conversation(payload: dict[str, Any]) -> None:
    """Validate a conversation payload against the JSON schema.

    Raises jsonschema.ValidationError if invalid.
    """
    schema = load_schema()
    jsonschema.validate(instance=payload, schema=schema)
