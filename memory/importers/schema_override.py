from __future__ import annotations

import hashlib
import json
from typing import Any

import jsonschema  # pyright: ignore[reportMissingModuleSource]

MAX_SCHEMA_BYTES = 65_536
MAX_SCHEMA_DEPTH = 8
MAX_SCHEMA_PROPERTIES = 64
MAX_SCHEMA_ARRAY_ITEMS = 64
MAX_SCHEMA_STRING_LENGTH = 512
_FORBIDDEN_KEYS = {
    "$ref",
    "code",
    "expression",
    "file",
    "filesystem",
    "path",
    "query",
    "ref",
    "regex",
    "remote",
    "script",
    "template",
    "uri",
    "url",
}


def parse_schema_override(raw: bytes | str) -> dict[str, Any]:
    if isinstance(raw, bytes):
        if len(raw) > MAX_SCHEMA_BYTES:
            raise ValueError(f"schema exceeds {MAX_SCHEMA_BYTES} bytes")
        try:
            text = raw.decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            raise ValueError("schema must be valid UTF-8 JSON") from exc
    else:
        text = raw
        if len(text.encode("utf-8")) > MAX_SCHEMA_BYTES:
            raise ValueError(f"schema exceeds {MAX_SCHEMA_BYTES} bytes")
    try:
        value = json.loads(text, object_pairs_hook=_unique_object)
    except json.JSONDecodeError as exc:
        raise ValueError("schema must be valid JSON") from exc
    if not isinstance(value, dict):
        raise ValueError("schema must be a JSON object")
    _validate_bounds(value)
    return value


def validate_schema_override(
    schema: dict[str, Any], contract: dict[str, Any] | None, *, parser: str
) -> None:
    if contract is None:
        raise ValueError(f"{parser} does not support schema overrides")
    error = next(jsonschema.Draft202012Validator(contract).iter_errors(schema), None)
    if error is None:
        return
    path = ".".join(str(part) for part in error.absolute_path)
    location = f" at {path}" if path else ""
    raise ValueError(
        f"schema is invalid for {parser}{location}: {error.message}"
    )


def schema_fingerprint(schema: dict[str, Any]) -> str:
    canonical = json.dumps(
        schema, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(canonical).hexdigest()


def stamp_import_provenance(
    payloads: list[dict[str, Any]],
    *,
    parser: str,
    schema: dict[str, Any] | None,
) -> None:
    fingerprint = schema_fingerprint(schema) if schema is not None else None
    version = schema.get("version") if schema is not None else None
    for payload in payloads:
        metadata = payload.get("metadata")
        if not isinstance(metadata, dict):
            metadata = {}
            payload["metadata"] = metadata
        metadata["import_parser"] = parser
        metadata["import_schema_applied"] = schema is not None
        if fingerprint is not None:
            metadata["import_schema_fingerprint"] = fingerprint
        if isinstance(version, int):
            metadata["import_schema_version"] = version


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"schema contains duplicate property {key!r}")
        result[key] = value
    return result


def _validate_bounds(value: Any, *, depth: int = 0) -> None:
    if depth > MAX_SCHEMA_DEPTH:
        raise ValueError(f"schema nesting exceeds {MAX_SCHEMA_DEPTH} levels")
    if isinstance(value, dict):
        if len(value) > MAX_SCHEMA_PROPERTIES:
            raise ValueError(
                f"schema object exceeds {MAX_SCHEMA_PROPERTIES} properties"
            )
        for key, child in value.items():
            if key.casefold() in _FORBIDDEN_KEYS:
                raise ValueError(f"schema property {key!r} is not allowed")
            _validate_bounds(child, depth=depth + 1)
        return
    if isinstance(value, list):
        if len(value) > MAX_SCHEMA_ARRAY_ITEMS:
            raise ValueError(
                f"schema array exceeds {MAX_SCHEMA_ARRAY_ITEMS} items"
            )
        for child in value:
            _validate_bounds(child, depth=depth + 1)
        return
    if isinstance(value, str):
        if len(value) > MAX_SCHEMA_STRING_LENGTH:
            raise ValueError(
                f"schema string exceeds {MAX_SCHEMA_STRING_LENGTH} characters"
            )
        lowered = value.casefold()
        if (
            "://" in value
            or value.startswith(("/", "\\"))
            or ".." in value
            or "{{" in value
            or "{%" in value
            or "javascript:" in lowered
        ):
            raise ValueError("schema contains a forbidden reference or template value")
