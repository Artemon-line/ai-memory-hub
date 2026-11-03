from __future__ import annotations

from typing import Any

from memory.ingestion.fact_timeline import (
    FactField,
    FactTimelineProjector,
    TemporalFactField,
)


def test_fact_timeline_returns_latest_value_from_source_timestamp() -> None:
    facts = [
        _fact("fact-old", "alpha runner", "memory-old"),
        _fact("fact-new", "beta runner", "memory-new"),
    ]
    memories = {
        "memory-old": _memory("memory-old", source="codex", timestamp="2026-08-12T00:00:00Z"),
        "memory-new": _memory("memory-new", source="hermes", timestamp="2026-12-12T00:00:00Z"),
    }

    projection = FactTimelineProjector(memories.get).project(facts)

    latest = projection.latest_payload()
    assert latest is not None
    assert latest[TemporalFactField.VALUE.value] == "beta runner"
    assert latest[TemporalFactField.STORED_AT.value] == "2026-12-12T00:00:00Z"
    assert latest[TemporalFactField.AUTHOR.value] == "hermes"
    assert [entry.value for entry in projection.entries] == ["beta runner", "alpha runner"]
    assert projection.has_latest_conflict is False


def test_fact_timeline_reports_conflict_for_same_timestamp_values() -> None:
    facts = [
        _fact("fact-a", "lexical windows", "memory-a"),
        _fact("fact-b", "semantic chunks", "memory-b"),
    ]
    memories = {
        "memory-a": _memory("memory-a", source="codex", timestamp="2026-08-12T00:00:00Z"),
        "memory-b": _memory("memory-b", source="hermes", timestamp="2026-08-12T00:00:00Z"),
    }

    projection = FactTimelineProjector(memories.get).project(facts)

    latest = projection.latest_payload()
    assert latest is not None
    assert latest[TemporalFactField.VALUES.value] == ["semantic chunks", "lexical windows"]
    assert latest[TemporalFactField.STORED_AT.value] == "2026-08-12T00:00:00Z"
    assert latest[TemporalFactField.AUTHORS.value] == ["codex", "hermes"]
    assert projection.has_latest_conflict is True


def test_fact_timeline_uses_fact_timestamp_when_source_memory_is_missing() -> None:
    facts = [
        {
            **_fact("fact-a", "fallback value", "missing-memory"),
            FactField.UPDATED_AT.value: "2026-09-01T00:00:00Z",
        }
    ]

    projection = FactTimelineProjector(lambda _id: None).project(facts)

    latest = projection.latest_payload()
    assert latest is not None
    assert latest[TemporalFactField.VALUE.value] == "fallback value"
    assert latest[TemporalFactField.STORED_AT.value] == "2026-09-01T00:00:00Z"
    assert latest[TemporalFactField.AUTHOR.value] == "unknown"


def test_fact_timeline_history_does_not_depend_on_unique_fact_ids() -> None:
    facts = [
        _fact("", "old missing id", "memory-old"),
        _fact("", "new missing id", "memory-new"),
    ]
    memories = {
        "memory-old": _memory("memory-old", source="codex", timestamp="2026-01-01T00:00:00Z"),
        "memory-new": _memory("memory-new", source="codex", timestamp="2026-02-01T00:00:00Z"),
    }

    projection = FactTimelineProjector(memories.get).project(facts)

    assert [entry.value for entry in projection.latest_entries] == ["new missing id"]
    assert [entry.value for entry in projection.historical_entries] == ["old missing id"]


def _fact(fact_id: str, value: str, source_conversation_id: str) -> dict[str, Any]:
    return {
        FactField.ID.value: fact_id,
        FactField.SUBJECT.value: "project",
        FactField.PREDICATE.value: "command_name",
        FactField.OBJECT.value: value,
        FactField.OBJECT_NORMALIZED.value: value,
        FactField.SOURCE_CONVERSATION_ID.value: source_conversation_id,
        FactField.CREATED_AT.value: "2026-01-01T00:00:00Z",
        FactField.UPDATED_AT.value: "2026-01-01T00:00:00Z",
    }


def _memory(memory_id: str, *, source: str, timestamp: str) -> dict[str, Any]:
    return {
        "id": memory_id,
        "source": source,
        "timestamp": timestamp,
    }
