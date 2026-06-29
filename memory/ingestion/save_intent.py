from __future__ import annotations

from enum import StrEnum
from typing import Any

from memory.config import InsertPolicy

SAVE_INTENT_REQUIRED_MESSAGE = (
    "memory_insert requires metadata.save_intent when "
    "memory.insert_policy is require_save_intent"
)


class SaveIntent(StrEnum):
    EXPLICIT_USER_REQUEST = "explicit_user_request"
    USER_CONFIRMED = "user_confirmed"
    CLIENT_AUTO_SAVE = "client_auto_save"


class SaveIntentError(ValueError):
    def __init__(self, message: str, *, error_code: str = "save_intent_required") -> None:
        super().__init__(message)
        self.error_code = error_code


def validate_insert_save_intent(conversation_json: dict[str, Any], *, insert_policy: str) -> None:
    if insert_policy == InsertPolicy.PERMISSIVE.value:
        return
    if insert_policy != InsertPolicy.REQUIRE_SAVE_INTENT.value:
        raise SaveIntentError("unknown memory.insert_policy", error_code="invalid_input")

    save_intent = _metadata_value(conversation_json, "save_intent")
    if save_intent is None:
        raise SaveIntentError(SAVE_INTENT_REQUIRED_MESSAGE)
    if str(save_intent) not in {item.value for item in SaveIntent}:
        raise SaveIntentError(
            "metadata.save_intent must be one of: "
            + ", ".join(item.value for item in SaveIntent),
            error_code="invalid_save_intent",
        )


def _metadata_value(conversation_json: dict[str, Any], key: str) -> Any:
    metadata = conversation_json.get("metadata")
    if not isinstance(metadata, dict):
        return None
    return metadata.get(key)
