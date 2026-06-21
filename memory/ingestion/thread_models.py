from __future__ import annotations

from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ThreadMetadataKey(StrEnum):
    THREAD_ID = "thread_id"
    UPSTREAM_THREAD_ID = "upstream_thread_id"
    PARENT_CONVERSATION_ID = "parent_conversation_id"
    RELATED_CONVERSATION_IDS = "related_conversation_ids"


class ThreadResultKey(StrEnum):
    THREAD_ID = "thread_id"
    THREAD_CONVERSATION_IDS = "thread_conversation_ids"
    THREAD_CONVERSATION_COUNT = "thread_conversation_count"
    MATCHING_CONVERSATIONS = "matching_conversations"
    MATCHING_CHUNKS = "matching_chunks"
    EVIDENCE_CHUNKS = "evidence_chunks"
    USED_IN_ANSWER = "used_in_answer"


class SearchResultMode(StrEnum):
    CHUNKS = "chunks"
    COMPACT = "compact"
    CONVERSATIONS = "conversations"
    THREADS = "threads"


class ThreadMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid")

    thread_id: str | None = Field(default=None, max_length=512)
    upstream_thread_id: str | None = Field(default=None, max_length=256)
    parent_conversation_id: str | None = Field(default=None, max_length=128)
    related_conversation_ids: list[str] = Field(default_factory=list)

    @field_validator("thread_id", "upstream_thread_id", "parent_conversation_id", mode="before")
    @classmethod
    def _blank_strings_to_none(cls, value: object) -> object:
        if value is None:
            return None
        if not isinstance(value, str):
            return value
        stripped = value.strip()
        return stripped or None

    @field_validator("parent_conversation_id")
    @classmethod
    def _validate_parent_conversation_id(cls, value: str | None) -> str | None:
        if value is not None:
            _validate_uuid(value)
        return value

    @field_validator("related_conversation_ids", mode="before")
    @classmethod
    def _normalize_related_conversation_ids(cls, value: object) -> object:
        if value is None:
            return []
        return value

    @field_validator("related_conversation_ids")
    @classmethod
    def _validate_related_conversation_ids(cls, value: list[str]) -> list[str]:
        related: list[str] = []
        for item in value:
            if not isinstance(item, str):
                raise ValueError("metadata.related_conversation_ids must be a list of strings")
            related_id = item.strip()
            if not related_id:
                continue
            _validate_uuid(related_id)
            if related_id not in related:
                related.append(related_id)
        return related

    def to_metadata_update(self) -> dict[str, object]:
        update: dict[str, object] = {}
        if self.thread_id is not None:
            update[ThreadMetadataKey.THREAD_ID] = self.thread_id
        if self.upstream_thread_id is not None:
            update[ThreadMetadataKey.UPSTREAM_THREAD_ID] = self.upstream_thread_id
        if self.parent_conversation_id is not None:
            update[ThreadMetadataKey.PARENT_CONVERSATION_ID] = self.parent_conversation_id
        if self.related_conversation_ids:
            update[ThreadMetadataKey.RELATED_CONVERSATION_IDS] = self.related_conversation_ids
        return update


def thread_metadata_from_mapping(metadata: dict[str, object]) -> ThreadMetadata:
    return ThreadMetadata(
        thread_id=metadata.get(ThreadMetadataKey.THREAD_ID),
        upstream_thread_id=metadata.get(ThreadMetadataKey.UPSTREAM_THREAD_ID),
        parent_conversation_id=metadata.get(ThreadMetadataKey.PARENT_CONVERSATION_ID),
        related_conversation_ids=metadata.get(ThreadMetadataKey.RELATED_CONVERSATION_IDS),
    )


def result_mode_values() -> set[str]:
    return {mode.value for mode in SearchResultMode}


def result_mode_error_message() -> str:
    values = ", ".join(mode.value for mode in SearchResultMode)
    return f"result_mode must be one of: {values}"


def _validate_uuid(value: str) -> None:
    try:
        UUID(value)
    except ValueError as exc:
        raise ValueError("conversation reference ids must be valid UUIDs") from exc
