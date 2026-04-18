from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import jsonschema  # pyright: ignore[reportMissingModuleSource]

SCHEMA_PATH = Path(__file__).resolve().parents[1] / "schema" / "conversation.schema.json"


def load_schema() -> dict[str, Any]:
    """Load the conversation JSON schema from disk."""
    with SCHEMA_PATH.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def validate_conversation(payload: dict[str, Any]) -> None:
    """Validate a conversation payload against the JSON schema.

    Raises jsonschema.ValidationError if invalid.
    """
    schema = load_schema()
    jsonschema.validate(instance=payload, schema=schema)
