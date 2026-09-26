from __future__ import annotations

from typing import Any

from memory.backend.log_safety import redact_secrets
from memory.handoff_models import StoredHandoffPacket

MAX_HANDOFF_ID_LENGTH = 128
MAX_HANDOFF_SEARCH_LIMIT = 100


def prepare_handoff_record(
    record: dict[str, Any],
    *,
    owner_id: str | None,
    project_id: str,
    supersedes_handoff_id: str | None = None,
) -> dict[str, Any]:
    payload = redact_handoff_value(dict(record))
    payload["owner_id"] = owner_id
    payload["project_id"] = project_id
    payload["supersedes_handoff_id"] = supersedes_handoff_id
    payload["superseded_by_handoff_id"] = None
    payload["status"] = str(payload.get("status") or "active")
    validated = StoredHandoffPacket.model_validate(payload)
    normalized = validated.model_dump(mode="json")
    validate_handoff_id(str(normalized["handoff_id"]))
    if supersedes_handoff_id is not None:
        validate_handoff_id(supersedes_handoff_id)
        if supersedes_handoff_id == normalized["handoff_id"]:
            raise ValueError("a handoff cannot supersede itself")
    return normalized


def public_handoff_record(
    payload: dict[str, Any], *, superseded_by_handoff_id: str | None
) -> dict[str, Any]:
    record = dict(payload)
    record["superseded_by_handoff_id"] = superseded_by_handoff_id
    if superseded_by_handoff_id is not None:
        record["status"] = "superseded"
    return StoredHandoffPacket.model_validate(record).model_dump(mode="json")


def validate_handoff_id(value: str) -> str:
    handoff_id = str(value).strip()
    if not handoff_id or len(handoff_id) > MAX_HANDOFF_ID_LENGTH:
        raise ValueError("handoff_id must contain 1 to 128 characters")
    return handoff_id


def validate_handoff_limit(value: int) -> int:
    limit = int(value)
    if limit < 1 or limit > MAX_HANDOFF_SEARCH_LIMIT:
        raise ValueError("limit must be between 1 and 100")
    return limit


def redact_handoff_value(value: Any) -> Any:
    if isinstance(value, str):
        return redact_secrets(value)
    if isinstance(value, dict):
        return {str(key): redact_handoff_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [redact_handoff_value(item) for item in value]
    return value
