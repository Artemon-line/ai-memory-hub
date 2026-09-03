from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

UNKNOWN_AUTHOR = "unknown"


class FactField(StrEnum):
    ID = "id"
    SUBJECT = "subject"
    PREDICATE = "predicate"
    OBJECT = "object"
    OBJECT_RAW = "object_raw"
    OBJECT_NORMALIZED = "object_normalized"
    QUALIFIERS = "qualifiers"
    CONFIDENCE = "confidence"
    CONFIDENCE_REASON = "confidence_reason"
    SOURCE_QUALITY = "source_quality"
    SOURCE_CONVERSATION_ID = "source_conversation_id"
    SOURCE_MESSAGE_INDEXES = "source_message_indexes"
    CREATED_AT = "created_at"
    UPDATED_AT = "updated_at"
    LAST_CONFIRMED_AT = "last_confirmed_at"
    SUPERSEDED_BY = "superseded_by"
    SUPERSEDED_AT = "superseded_at"
    DELETED_AT = "deleted_at"
    AUTHOR = "author"
    STORED_AT = "stored_at"


class MemoryField(StrEnum):
    ID = "id"
    SOURCE = "source"
    TIMESTAMP = "timestamp"


class TemporalFactField(StrEnum):
    VALUE = "value"
    VALUES = "values"
    STORED_AT = "stored_at"
    AUTHOR = "author"
    AUTHORS = "authors"
    FACT_ID = "fact_id"
    FACT_IDS = "fact_ids"
    SUBJECT = "subject"
    PREDICATE = "predicate"
    SOURCE_CONVERSATION_ID = "source_conversation_id"


MemoryLookup = Callable[[str], Mapping[str, Any] | None]


@dataclass(frozen=True, slots=True)
class FactTimelineEntry:
    fact: dict[str, Any]
    value: str
    stored_at: str | None
    author: str
    source_conversation_id: str | None
    parsed_stored_at: datetime | None

    @property
    def latest_group_key(self) -> str:
        if self.parsed_stored_at is not None:
            return self.parsed_stored_at.isoformat()
        return self.stored_at or ""

    @property
    def sort_key(self) -> tuple[int, datetime, str, str]:
        fallback_time = datetime.min.replace(tzinfo=UTC)
        return (
            1 if self.parsed_stored_at is not None else 0,
            self.parsed_stored_at or fallback_time,
            self.stored_at or "",
            str(self.fact.get(FactField.ID.value, "")),
        )

    def latest_payload(self) -> dict[str, Any]:
        return _drop_empty(
            {
                TemporalFactField.VALUE.value: self.value,
                TemporalFactField.STORED_AT.value: self.stored_at,
                TemporalFactField.AUTHOR.value: self.author,
                TemporalFactField.FACT_ID.value: self.fact.get(FactField.ID.value),
                TemporalFactField.SUBJECT.value: self.fact.get(FactField.SUBJECT.value),
                TemporalFactField.PREDICATE.value: self.fact.get(FactField.PREDICATE.value),
                TemporalFactField.SOURCE_CONVERSATION_ID.value: self.source_conversation_id,
            }
        )

    def timeline_payload(self) -> dict[str, Any]:
        payload = self.latest_payload()
        payload[TemporalFactField.FACT_ID.value] = self.fact.get(FactField.ID.value)
        return payload


@dataclass(frozen=True, slots=True)
class FactTimelineProjection:
    entries: tuple[FactTimelineEntry, ...]

    @property
    def latest_entries(self) -> tuple[FactTimelineEntry, ...]:
        if not self.entries:
            return ()
        latest_key = self.entries[0].latest_group_key
        return tuple(entry for entry in self.entries if entry.latest_group_key == latest_key)

    @property
    def unique_latest_entries(self) -> tuple[FactTimelineEntry, ...]:
        seen_values: set[str] = set()
        entries: list[FactTimelineEntry] = []
        for entry in self.latest_entries:
            if entry.value in seen_values:
                continue
            seen_values.add(entry.value)
            entries.append(entry)
        return tuple(entries)

    @property
    def has_latest_conflict(self) -> bool:
        return len(self.unique_latest_entries) > 1

    @property
    def historical_entries(self) -> tuple[FactTimelineEntry, ...]:
        latest_ids = {
            str(entry.fact.get(FactField.ID.value, "")) for entry in self.latest_entries
        }
        return tuple(
            entry
            for entry in self.entries
            if str(entry.fact.get(FactField.ID.value, "")) not in latest_ids
        )

    def latest_payload(self) -> dict[str, Any] | None:
        latest = self.unique_latest_entries
        if not latest:
            return None
        if len(latest) == 1:
            return latest[0].latest_payload()
        return _drop_empty(
            {
                TemporalFactField.VALUES.value: [entry.value for entry in latest],
                TemporalFactField.STORED_AT.value: latest[0].stored_at,
                TemporalFactField.AUTHORS.value: sorted({entry.author for entry in latest}),
                TemporalFactField.FACT_IDS.value: [
                    entry.fact.get(FactField.ID.value) for entry in latest
                ],
                TemporalFactField.SUBJECT.value: latest[0].fact.get(FactField.SUBJECT.value),
                TemporalFactField.PREDICATE.value: latest[0].fact.get(FactField.PREDICATE.value),
            }
        )

    def timeline_payload(self) -> list[dict[str, Any]]:
        return [entry.timeline_payload() for entry in self.entries]


class FactTimelineProjector:
    def __init__(self, memory_lookup: MemoryLookup | None = None) -> None:
        self._memory_lookup = memory_lookup

    def project(self, facts: Sequence[Mapping[str, Any]]) -> FactTimelineProjection:
        entries = [
            self._entry(fact)
            for fact in facts
            if fact.get(FactField.DELETED_AT.value) is None
        ]
        entries.sort(key=lambda entry: entry.sort_key, reverse=True)
        return FactTimelineProjection(entries=tuple(entries))

    def _entry(self, fact: Mapping[str, Any]) -> FactTimelineEntry:
        source_conversation_id = _optional_text(fact.get(FactField.SOURCE_CONVERSATION_ID.value))
        memory = self._source_memory(source_conversation_id)
        temporal = temporal_fact_source_metadata(fact, memory)
        stored_at = _optional_text(temporal.get(FactField.STORED_AT.value))
        enriched = dict(fact)
        enriched.update(temporal)
        return FactTimelineEntry(
            fact=enriched,
            value=_fact_value(enriched),
            stored_at=stored_at,
            author=str(temporal.get(FactField.AUTHOR.value) or UNKNOWN_AUTHOR),
            source_conversation_id=source_conversation_id,
            parsed_stored_at=parse_memory_timestamp(stored_at),
        )

    def _source_memory(self, source_conversation_id: str | None) -> Mapping[str, Any] | None:
        if self._memory_lookup is None or source_conversation_id is None:
            return None
        return self._memory_lookup(source_conversation_id)


def temporal_fact_source_metadata(
    fact: Mapping[str, Any],
    memory: Mapping[str, Any] | None,
) -> dict[str, Any]:
    stored_at = (
        _source_memory_timestamp(memory)
        or _optional_text(fact.get(FactField.STORED_AT.value))
        or _fact_timestamp(fact)
    )
    author = (
        _source_memory_author(memory)
        or _optional_text(fact.get(FactField.AUTHOR.value))
        or UNKNOWN_AUTHOR
    )
    return {
        FactField.STORED_AT.value: stored_at,
        FactField.AUTHOR.value: author,
    }


def parse_memory_timestamp(value: Any) -> datetime | None:
    text = _optional_text(value)
    if text is None:
        return None
    normalized = text[:-1] + "+00:00" if text.endswith("Z") else text
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _fact_value(fact: Mapping[str, Any]) -> str:
    for key in (
        FactField.OBJECT_NORMALIZED.value,
        FactField.OBJECT.value,
        FactField.OBJECT_RAW.value,
    ):
        value = _optional_text(fact.get(key))
        if value is not None:
            return value
    return ""


def _source_memory_timestamp(memory: Mapping[str, Any] | None) -> str | None:
    if memory is None:
        return None
    return _optional_text(memory.get(MemoryField.TIMESTAMP.value))


def _source_memory_author(memory: Mapping[str, Any] | None) -> str | None:
    if memory is None:
        return None
    return _optional_text(memory.get(MemoryField.SOURCE.value))


def _fact_timestamp(fact: Mapping[str, Any]) -> str | None:
    for key in (
        FactField.LAST_CONFIRMED_AT.value,
        FactField.UPDATED_AT.value,
        FactField.CREATED_AT.value,
    ):
        value = _optional_text(fact.get(key))
        if value is not None:
            return value
    return None


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _drop_empty(payload: Mapping[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in payload.items() if value is not None}
