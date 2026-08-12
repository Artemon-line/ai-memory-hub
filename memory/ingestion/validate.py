from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import UUID

import jsonschema  # pyright: ignore[reportMissingModuleSource]

SCHEMA_PATH = Path(__file__).resolve().parents[1] / "schema" / "conversation.schema.json"
_REQUIRED_SCHEMA_FIELDS = ("id", "source", "timestamp", "messages", "metadata")
_REQUIRED_MESSAGE_FIELDS = ("role", "text", "hash")
_REQUIRED_METADATA_FIELDS = ("imported_at", "updated_at", "conversation_hash")
_FORMAT_CHECKER = jsonschema.FormatChecker()


@_FORMAT_CHECKER.checks("date-time", raises=ValueError)
def _is_date_time(value: object) -> bool:
    if not isinstance(value, str):
        return True
    datetime.fromisoformat(value.replace("Z", "+00:00"))
    return True


@_FORMAT_CHECKER.checks("uuid", raises=ValueError)
def _is_uuid(value: object) -> bool:
    if not isinstance(value, str):
        return True
    UUID(value)
    return True


def load_schema(path: str | Path | None = None) -> dict[str, Any]:
    """Load the conversation JSON schema from disk."""
    schema_path = SCHEMA_PATH if path is None else Path(path)
    with schema_path.open("r", encoding="utf-8") as handle:
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

    metadata = properties.get("metadata", {})
    if not isinstance(metadata, dict):
        raise ValueError("Conversation schema is incompatible: metadata property must be defined")

    metadata_required = metadata.get("required", [])
    if not isinstance(metadata_required, list):
        raise ValueError("Conversation schema is incompatible: metadata.required must be an array")

    missing_metadata_fields = [
        field for field in _REQUIRED_METADATA_FIELDS if field not in metadata_required
    ]
    if missing_metadata_fields:
        raise ValueError(
            "Conversation schema is incompatible with code expectations; "
            f"missing metadata fields: {', '.join(missing_metadata_fields)}"
        )


def validate_conversation(payload: dict[str, Any], schema: dict[str, Any] | None = None) -> None:
    """Validate a conversation payload against the JSON schema.

    Raises jsonschema.ValidationError if invalid.
    """
    active_schema = load_schema() if schema is None else schema
    jsonschema.Draft202012Validator.check_schema(active_schema)
    validator = jsonschema.Draft202012Validator(
        active_schema,
        format_checker=_FORMAT_CHECKER,
    )
    validator.validate(payload)
