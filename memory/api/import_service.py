from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from fastapi import Request
from starlette.datastructures import FormData, UploadFile
from starlette.formparsers import MultiPartException

from memory.importers.registry import get_importer
from memory.importers.schema_override import (
    MAX_SCHEMA_BYTES,
    parse_schema_override,
    stamp_import_provenance,
    validate_schema_override,
)

MAX_IMPORT_FILE_BYTES = 20_000_000
MAX_IMPORT_REQUEST_BYTES = 20_250_000
MAX_IMPORT_CONVERSATIONS = 100
MAX_IMPORT_SOURCE_LENGTH = 128
MAX_IMPORT_TITLE_LENGTH = 512
_ALLOWED_FIELDS = {"file", "parser", "schema", "source", "title"}
_SUPPORTED_FILE_MEDIA_TYPES = {
    "application/csv",
    "application/json",
    "application/jsonl",
    "application/octet-stream",
    "application/x-ndjson",
    "text/csv",
    "text/plain",
}
_SUPPORTED_SCHEMA_MEDIA_TYPES = {
    "application/json",
    "application/octet-stream",
    "text/plain",
}


@dataclass(frozen=True)
class PreparedImport:
    parser: str
    payloads: list[dict[str, Any]]
    schema_applied: bool
    schema_version: int | None
    schema_fingerprint: str | None


class ImportRequestError(ValueError):
    def __init__(self, status_code: int, message: str):
        super().__init__(message)
        self.status_code = status_code


async def prepare_import_request(request: Request) -> PreparedImport:
    _require_multipart(request)
    body = await _read_bounded_body(request)
    form = await _parse_form(request, body)
    fields = _unique_fields(form)

    parser_name = _required_text_field(fields, "parser", max_length=128)
    source = _optional_text_field(
        fields, "source", max_length=MAX_IMPORT_SOURCE_LENGTH
    )
    title = _optional_text_field(
        fields, "title", max_length=MAX_IMPORT_TITLE_LENGTH
    )
    file = fields.get("file")
    if not isinstance(file, UploadFile):
        raise ImportRequestError(400, "file must be supplied as a multipart file part")
    _validate_media_type(file.content_type, allowed=_SUPPORTED_FILE_MEDIA_TYPES, field="file")
    raw_file = await file.read(MAX_IMPORT_FILE_BYTES + 1)
    if len(raw_file) > MAX_IMPORT_FILE_BYTES:
        raise ImportRequestError(
            413, f"file exceeds {MAX_IMPORT_FILE_BYTES} bytes"
        )
    if not raw_file:
        raise ImportRequestError(400, "file must not be empty")
    try:
        text = raw_file.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ImportRequestError(400, "file must be valid UTF-8 text") from exc

    schema = await _optional_schema(fields.get("schema"))
    try:
        importer = get_importer(parser_name)
        if schema is not None:
            if importer.schema_override_schema is None:
                raise ImportRequestError(
                    400, f"{parser_name} does not support schema overrides"
                )
            try:
                validate_schema_override(
                    schema, importer.schema_override_schema, parser=parser_name
                )
            except ValueError as exc:
                raise ImportRequestError(
                    400, "schema is invalid for the selected parser"
                ) from exc
        payloads = importer.import_text_with_schema(
            text, schema=schema, source=source, title=title
        )
    except ImportRequestError:
        raise
    except ValueError as exc:
        raise ImportRequestError(400, str(exc)) from exc
    if not payloads:
        raise ImportRequestError(
            400, f"{parser_name} importer returned no conversations"
        )
    if len(payloads) > MAX_IMPORT_CONVERSATIONS:
        raise ImportRequestError(
            400,
            f"import exceeds {MAX_IMPORT_CONVERSATIONS} conversations",
        )
    stamp_import_provenance(payloads, parser=parser_name, schema=schema)
    fingerprint = None
    metadata = payloads[0].get("metadata")
    if isinstance(metadata, dict):
        value = metadata.get("import_schema_fingerprint")
        fingerprint = str(value) if value is not None else None
    return PreparedImport(
        parser=parser_name,
        payloads=payloads,
        schema_applied=schema is not None,
        schema_version=(
            schema.get("version")
            if schema is not None and isinstance(schema.get("version"), int)
            else None
        ),
        schema_fingerprint=fingerprint,
    )


def import_openapi_request_body() -> dict[str, Any]:
    return {
        "requestBody": {
            "required": True,
            "content": {
                "multipart/form-data": {
                    "schema": {
                        "type": "object",
                        "properties": {
                            "file": {"type": "string", "format": "binary"},
                            "parser": {"type": "string"},
                            "schema": {
                                "oneOf": [
                                    {"type": "string"},
                                    {"type": "string", "format": "binary"},
                                ]
                            },
                            "source": {"type": "string"},
                            "title": {"type": "string"},
                        },
                        "required": ["file", "parser"],
                    }
                }
            },
        }
    }


def _require_multipart(request: Request) -> None:
    media_type = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    if media_type != "multipart/form-data":
        raise ImportRequestError(415, "content type must be multipart/form-data")
    content_length = request.headers.get("content-length")
    if content_length is None:
        return
    try:
        parsed = int(content_length)
    except ValueError as exc:
        raise ImportRequestError(400, "content-length must be an integer") from exc
    if parsed < 0:
        raise ImportRequestError(400, "content-length must be non-negative")
    if parsed > MAX_IMPORT_REQUEST_BYTES:
        raise ImportRequestError(
            413, f"request exceeds {MAX_IMPORT_REQUEST_BYTES} bytes"
        )


async def _read_bounded_body(request: Request) -> bytes:
    chunks: list[bytes] = []
    size = 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > MAX_IMPORT_REQUEST_BYTES:
            raise ImportRequestError(
                413, f"request exceeds {MAX_IMPORT_REQUEST_BYTES} bytes"
            )
        chunks.append(chunk)
    return b"".join(chunks)


async def _parse_form(request: Request, body: bytes) -> FormData:
    sent = False

    async def receive() -> dict[str, Any]:
        nonlocal sent
        if sent:
            return {"type": "http.request", "body": b"", "more_body": False}
        sent = True
        return {"type": "http.request", "body": body, "more_body": False}

    bounded_request = Request(request.scope, receive)
    try:
        return await bounded_request.form(
            max_files=2,
            max_fields=4,
            max_part_size=MAX_SCHEMA_BYTES,
        )
    except (MultiPartException, ValueError) as exc:
        raise ImportRequestError(400, "malformed multipart body") from exc


def _unique_fields(form: FormData) -> dict[str, Any]:
    fields: dict[str, Any] = {}
    for key, value in form.multi_items():
        if key not in _ALLOWED_FIELDS:
            raise ImportRequestError(400, f"unknown multipart field: {key}")
        if key in fields:
            raise ImportRequestError(400, f"duplicate multipart field: {key}")
        fields[key] = value
    return fields


def _required_text_field(
    fields: dict[str, Any], name: str, *, max_length: int
) -> str:
    value = fields.get(name)
    if not isinstance(value, str) or not value.strip():
        raise ImportRequestError(400, f"{name} is required")
    parsed = value.strip()
    if len(parsed) > max_length:
        raise ImportRequestError(400, f"{name} exceeds {max_length} characters")
    return parsed


def _optional_text_field(
    fields: dict[str, Any], name: str, *, max_length: int
) -> str | None:
    if name not in fields:
        return None
    value = fields[name]
    if not isinstance(value, str):
        raise ImportRequestError(400, f"{name} must be a text form field")
    parsed = value.strip()
    if not parsed:
        raise ImportRequestError(400, f"{name} must not be empty")
    if len(parsed) > max_length:
        raise ImportRequestError(400, f"{name} exceeds {max_length} characters")
    return parsed


async def _optional_schema(value: Any) -> dict[str, Any] | None:
    if value is None:
        return None
    if isinstance(value, UploadFile):
        _validate_media_type(
            value.content_type, allowed=_SUPPORTED_SCHEMA_MEDIA_TYPES, field="schema"
        )
        raw = await value.read(MAX_SCHEMA_BYTES + 1)
    elif isinstance(value, str):
        raw = value.encode("utf-8")
    else:
        raise ImportRequestError(400, "schema must be JSON text or a file part")
    try:
        return parse_schema_override(raw)
    except ValueError as exc:
        status = 413 if "exceeds" in str(exc) and "bytes" in str(exc) else 400
        message = (
            f"schema exceeds {MAX_SCHEMA_BYTES} bytes"
            if status == 413
            else "schema must be valid, bounded JSON"
        )
        raise ImportRequestError(status, message) from exc


def _validate_media_type(
    value: str | None, *, allowed: set[str], field: str
) -> None:
    media_type = (value or "application/octet-stream").split(";", 1)[0].strip().lower()
    if media_type not in allowed:
        raise ImportRequestError(
            415, f"unsupported {field} media type: {media_type or 'missing'}"
        )
