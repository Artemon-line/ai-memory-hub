from __future__ import annotations

import json
import re
from copy import deepcopy
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from typing import Any
from uuid import UUID

import jsonschema  # pyright: ignore[reportMissingModuleSource]
from jsonschema.protocols import Validator  # pyright: ignore[reportMissingModuleSource]

SCHEMA_PATH = Path(__file__).resolve().parents[1] / "schema" / "conversation.schema.json"
_REQUIRED_SCHEMA_FIELDS = ("id", "source", "timestamp", "messages", "metadata")
_REQUIRED_MESSAGE_FIELDS = ("role", "text", "hash")
_REQUIRED_METADATA_FIELDS = ("imported_at", "updated_at", "conversation_hash")
_FORMAT_CHECKER = jsonschema.FormatChecker()
_RFC3339_DATE_TIME = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$"
)


@_FORMAT_CHECKER.checks("date-time", raises=ValueError)
def _is_date_time(value: object) -> bool:
    if not isinstance(value, str):
        return True
    if _RFC3339_DATE_TIME.fullmatch(value) is None:
        return False
    datetime.fromisoformat(value.replace("Z", "+00:00"))
    return True


@_FORMAT_CHECKER.checks("uuid", raises=ValueError)
def _is_uuid(value: object) -> bool:
    if not isinstance(value, str):
        return True
    UUID(value)
    return True


def _schema_identity(path: str | Path | None) -> tuple[str, int, int]:
    schema_path = (SCHEMA_PATH if path is None else Path(path)).resolve()
    stat = schema_path.stat()
    return str(schema_path), stat.st_mtime_ns, stat.st_size


def load_schema(path: str | Path | None = None) -> dict[str, Any]:
    """Load an isolated conversation JSON schema from disk."""
    schema_path = SCHEMA_PATH if path is None else Path(path)
    with schema_path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def clear_schema_cache() -> None:
    """Explicitly invalidate all cached compiled schemas."""
    _load_compiled_schema_cached.cache_clear()


def schema_cache_info() -> dict[str, int | None]:
    """Return privacy-safe process-local schema cache diagnostics."""
    info = _load_compiled_schema_cached.cache_info()
    return {
        "hits": info.hits,
        "misses": info.misses,
        "maxsize": info.maxsize,
        "currsize": info.currsize,
    }


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


def compile_conversation_validator(schema: dict[str, Any]) -> Validator:
    """Compile and check a conversation validator for runtime reuse."""
    jsonschema.Draft202012Validator.check_schema(schema)
    return jsonschema.Draft202012Validator(
        schema,
        format_checker=_FORMAT_CHECKER,
    )


@lru_cache(maxsize=16)
def _load_compiled_schema_cached(
    resolved_path: str,
    modified_ns: int,
    size: int,
) -> tuple[dict[str, Any], Validator]:
    del modified_ns, size
    schema = load_schema(resolved_path)
    return schema, compile_conversation_validator(schema)


def load_compiled_conversation_schema(
    path: str | Path | None = None,
) -> tuple[dict[str, Any], Validator]:
    """Load an isolated schema and a file-identity-cached checked validator."""
    schema, validator = _load_compiled_schema_cached(*_schema_identity(path))
    return deepcopy(schema), validator


def load_conversation_validator(path: str | Path | None = None) -> Validator:
    """Load a checked validator cached by resolved path and file identity."""
    return _load_compiled_schema_cached(*_schema_identity(path))[1]


def validate_conversation(
    payload: dict[str, Any],
    schema: dict[str, Any] | None = None,
    *,
    validator: Validator | None = None,
) -> None:
    """Validate a conversation payload against the JSON schema.

    Raises jsonschema.ValidationError if invalid.
    """
    active_validator = validator
    if active_validator is None:
        active_schema = load_schema() if schema is None else schema
        active_validator = compile_conversation_validator(active_schema)
    active_validator.validate(payload)
