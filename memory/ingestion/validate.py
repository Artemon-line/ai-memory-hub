from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import jsonschema  # pyright: ignore[reportMissingModuleSource]

SCHEMA_PATH = Path(__file__).resolve().parents[1] / "schema" / "conversation.schema.json"
_ACTIVE_SCHEMA_PATH: Path = SCHEMA_PATH
_REQUIRED_SCHEMA_FIELDS = ("id", "source", "timestamp", "messages", "metadata")
_REQUIRED_MESSAGE_FIELDS = ("role", "text")


def load_schema() -> dict[str, Any]:
    """Load the conversation JSON schema from disk."""
    with _ACTIVE_SCHEMA_PATH.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def validate_schema_compatibility(schema: dict[str, Any]) -> None:
    required = schema.get("required", [])
    if not isinstance(required, list):
        raise ValueError("Schema required field list must be an array")

    missing = [field for field in _REQUIRED_SCHEMA_FIELDS if field not in required]
    if missing:
        raise ValueError(
            "Conversation schema is incompatible with code expectations; "
            f"missing required fields: {', '.join(missing)}"
        )

    properties = schema.get("properties", {})
    if not isinstance(properties, dict):
        raise ValueError("Schema properties must be an object")

    messages = properties.get("messages", {})
    if not isinstance(messages, dict):
        raise ValueError("Conversation schema is incompatible: messages property must be defined")

    items = messages.get("items", {})
    if not isinstance(items, dict):
        raise ValueError("Conversation schema is incompatible: messages.items must be defined")

    message_required = items.get("required", [])
    if not isinstance(message_required, list):
        raise ValueError("Conversation schema is incompatible: messages.items.required must be an array")

    missing_message_fields = [field for field in _REQUIRED_MESSAGE_FIELDS if field not in message_required]
    if missing_message_fields:
        raise ValueError(
            "Conversation schema is incompatible with code expectations; "
            f"missing message fields: {', '.join(missing_message_fields)}"
        )


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
