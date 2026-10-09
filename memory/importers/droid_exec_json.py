from __future__ import annotations

import json
from collections import OrderedDict
from datetime import datetime
from pathlib import Path
from typing import Any

import jsonschema  # pyright: ignore[reportMissingModuleSource]

from memory.importers.base import ConversationImporter

_MAX_INPUT_BYTES = 20_000_000
_SCHEMA_PATH = Path(__file__).resolve().parents[1] / "schema" / "droid-exec-capture.schema.json"
_FORMAT = "ai-memory-hub.droid-exec-capture"
_VERSION = 1


def _load_document_validator() -> Any:
    with _SCHEMA_PATH.open("r", encoding="utf-8") as schema_handle:
        schema = json.load(schema_handle)
    jsonschema.Draft202012Validator.check_schema(schema)
    format_checker = jsonschema.FormatChecker()
    return jsonschema.Draft202012Validator(schema, format_checker=format_checker)


_DOCUMENT_VALIDATOR = _load_document_validator()


class DroidExecJsonImporter(ConversationImporter):
    """Parse versioned captures of supported Factory Droid Exec output."""

    name = "droid-exec-json"

    def import_text(
        self,
        text: str,
        *,
        source: str | None = None,
        title: str | None = None,
    ) -> list[dict[str, Any]]:
        if not isinstance(text, str):
            raise ValueError("Droid Exec capture input must be text")
        if len(text.encode("utf-8")) > _MAX_INPUT_BYTES:
            raise ValueError("Droid Exec capture input exceeds 20000000 bytes")
        try:
            document = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ValueError("Droid Exec capture input must be valid JSON") from exc

        error = next(_DOCUMENT_VALIDATOR.iter_errors(document), None)
        if error is not None:
            raise ValueError(
                "Droid Exec capture must use the version 1 ai-memory-hub capture envelope"
            )

        assert isinstance(document, dict)
        if document["kind"] == "one-shot":
            messages, provenance = _import_one_shot(document)
        else:
            messages, provenance = _import_stream(document)

        capture_metadata = document.get("metadata")
        metadata: dict[str, Any] = {
            "importer": self.name,
            "platform": "droid",
            "ingestion_method": "json-import",
            "capture_format": _FORMAT,
            "capture_version": _VERSION,
            "capture_kind": document["kind"],
            **provenance,
        }
        if isinstance(capture_metadata, dict):
            _copy_nonempty(capture_metadata, "cwd", metadata, "directory")
            _copy_nonempty(capture_metadata, "model", metadata, "model")
            duration = capture_metadata.get("duration_ms")
            if isinstance(duration, (int, float)) and not isinstance(duration, bool):
                metadata["duration_ms"] = duration

        resolved_title = title or _nonempty_string(
            capture_metadata.get("title") if isinstance(capture_metadata, dict) else None
        )
        if resolved_title:
            metadata["title"] = resolved_title

        payload: dict[str, Any] = {
            "source": source or "droid",
            "messages": messages,
            "metadata": metadata,
        }
        if resolved_title:
            payload["title"] = resolved_title
        timestamp = _timestamp(
            capture_metadata.get("timestamp") if isinstance(capture_metadata, dict) else None
        )
        if timestamp is not None:
            payload["timestamp"] = timestamp.isoformat()
        return [payload]


def _import_one_shot(document: dict[str, Any]) -> tuple[list[dict[str, str]], dict[str, Any]]:
    prompt = _nonempty_string(document["prompt"])
    if prompt is None:
        raise ValueError("Droid Exec one-shot capture must include the submitted prompt")
    result = document["result"]
    assert isinstance(result, dict)
    response = result["result"]
    if result["is_error"] or result["subtype"] != "success" or not response.strip():
        raise ValueError("Droid Exec one-shot capture does not contain a successful result")

    provenance: dict[str, Any] = {}
    _copy_nonempty(result, "session_id", provenance, "source_session_id")
    duration = result.get("duration_ms")
    if isinstance(duration, (int, float)) and not isinstance(duration, bool):
        provenance["duration_ms"] = duration
    turns = result.get("num_turns")
    if isinstance(turns, int) and not isinstance(turns, bool):
        provenance["reported_turn_count"] = turns
    return [
        {"role": "user", "text": prompt},
        {"role": "assistant", "text": response},
    ], provenance


def _import_stream(document: dict[str, Any]) -> tuple[list[dict[str, str]], dict[str, Any]]:
    frames = document["frames"]
    assert isinstance(frames, list)
    messages: list[dict[str, str]] = []
    provenance: dict[str, Any] = {}
    pending_user: str | None = None
    assistant_blocks: OrderedDict[tuple[str, int], list[str]] = OrderedDict()
    initialize_request_ids: set[str | int] = set()

    for index, frame in enumerate(frames, start=1):
        assert isinstance(frame, dict)
        direction = frame["direction"]
        message = frame["message"]
        assert isinstance(message, dict)
        method = message.get("method")

        if direction == "client-to-droid" and method == "droid.initialize_session":
            params = _params(message, index=index, method=method)
            request_id = message.get("id")
            if isinstance(request_id, (str, int)) and not isinstance(request_id, bool):
                initialize_request_ids.add(request_id)
            _copy_nonempty(params, "cwd", provenance, "directory")
            _copy_nonempty(params, "modelId", provenance, "model")
            _copy_nonempty(params, "sessionId", provenance, "source_session_id")
            continue

        if direction == "client-to-droid" and method == "droid.add_user_message":
            params = _params(message, index=index, method=method)
            user_text = _nonempty_string(params.get("text"))
            if user_text is None:
                raise ValueError(f"Droid Exec frame {index} has an invalid user message")
            pending_user = user_text
            assistant_blocks = OrderedDict()
            continue

        if direction != "droid-to-client":
            continue

        result = message.get("result")
        if message.get("id") in initialize_request_ids and isinstance(result, dict):
            _copy_nonempty(result, "sessionId", provenance, "source_session_id")
            settings = result.get("settings")
            if isinstance(settings, dict):
                _copy_nonempty(settings, "modelId", provenance, "model")
            if not provenance.get("directory"):
                _copy_nonempty(result, "cwd", provenance, "directory")

        if method != "droid.session_notification":
            continue
        notification = _notification(message, index=index)
        notification_type = notification.get("type")

        if notification_type == "assistant_text_delta":
            if pending_user is None:
                continue
            message_id = _nonempty_string(notification.get("messageId"))
            block_index = notification.get("blockIndex")
            delta = notification.get("textDelta")
            if (
                message_id is None
                or not isinstance(block_index, int)
                or isinstance(block_index, bool)
                or block_index < 0
                or not isinstance(delta, str)
            ):
                raise ValueError(
                    f"Droid Exec frame {index} has an invalid assistant text delta"
                )
            assistant_blocks.setdefault((message_id, block_index), []).append(delta)
            continue

        if notification_type == "agent_turn_completed":
            reason = notification.get("reason")
            if not isinstance(reason, str):
                raise ValueError(f"Droid Exec frame {index} has an invalid turn completion")
            if pending_user is not None and reason == "completed":
                assistant_text = _coalesced_text(assistant_blocks)
                if assistant_text:
                    messages.extend(
                        [
                            {"role": "user", "text": pending_user},
                            {"role": "assistant", "text": assistant_text},
                        ]
                    )
            pending_user = None
            assistant_blocks = OrderedDict()

    if not messages:
        raise ValueError("Droid Exec stream contains no completed conversational turns")
    return messages, provenance


def _params(message: dict[str, Any], *, index: int, method: str) -> dict[str, Any]:
    params = message.get("params")
    if not isinstance(params, dict):
        raise ValueError(f"Droid Exec frame {index} has invalid params for {method}")
    return params


def _notification(message: dict[str, Any], *, index: int) -> dict[str, Any]:
    params = _params(message, index=index, method="droid.session_notification")
    notification = params.get("notification")
    if not isinstance(notification, dict) or not isinstance(notification.get("type"), str):
        raise ValueError(f"Droid Exec frame {index} has an invalid session notification")
    return notification


def _coalesced_text(blocks: OrderedDict[tuple[str, int], list[str]]) -> str | None:
    text_blocks = ["".join(deltas) for deltas in blocks.values()]
    retained = [text for text in text_blocks if text.strip()]
    return "\n\n".join(retained) if retained else None


def _timestamp(value: Any) -> datetime | None:
    if value is None:
        return None
    assert isinstance(value, str)
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("Droid Exec capture timestamp is invalid") from exc


def _copy_nonempty(
    source: dict[str, Any], source_key: str, target: dict[str, Any], target_key: str
) -> None:
    value = _nonempty_string(source.get(source_key))
    if value is not None:
        target[target_key] = value


def _nonempty_string(value: Any) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None
