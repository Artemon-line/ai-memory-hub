from __future__ import annotations

from memory.advanced_memory import extract_memory_graph


def test_extract_memory_graph_finds_entities_relationships_and_provenance() -> None:
    conversation = {
        "id": "conv-graph-1",
        "title": "Velvet Lantern",
        "timestamp": "2026-07-08T10:00:00Z",
        "messages": [
            {
                "role": "user",
                "text": "Velvet Lantern uses PGVector. The creator is Ada Lovelace.",
            }
        ],
    }

    graph = extract_memory_graph(conversation)

    assert {entity["normalized_name"] for entity in graph["entities"]} >= {
        "velvet lantern",
        "pgvector",
        "ada lovelace",
    }
    predicates = {relationship["predicate"] for relationship in graph["relationships"]}
    assert predicates >= {"uses_tool", "created_by"}
    relationship = next(row for row in graph["relationships"] if row["predicate"] == "uses_tool")
    assert relationship["provenance"] == [{"conversation_id": "conv-graph-1", "message_index": 0, "chunk_id": None, "fact_id": None, "summary_id": None}]


def test_extract_memory_graph_ignores_nearby_entities_without_relationship_pattern() -> None:
    conversation = {
        "id": "conv-graph-2",
        "timestamp": "2026-07-08T10:00:00Z",
        "messages": [{"role": "user", "text": "Codex and opencode were both discussed near PGVector."}],
    }

    graph = extract_memory_graph(conversation)

    assert {entity["normalized_name"] for entity in graph["entities"]} >= {"codex", "opencode", "pgvector"}
    assert graph["relationships"] == []


def test_extract_memory_graph_returns_empty_graph_when_conversation_id_is_missing() -> None:
    graph = extract_memory_graph(
        {"messages": [{"role": "user", "text": "Codex uses PGVector."}]}
    )

    assert graph == {"entities": [], "relationships": []}


def test_extract_memory_graph_preserves_indexes_for_selected_append_messages() -> None:
    conversation = {
        "id": "conv-graph-append",
        "title": "Velvet Lantern",
        "timestamp": "2026-07-08T10:00:00Z",
        "messages": [
            {"role": "user", "text": "Velvet Lantern uses PGVector."},
            {"role": "assistant", "text": "Recorded."},
            {"role": "user", "text": "Velvet Lantern changed to green."},
        ],
    }

    graph = extract_memory_graph(
        conversation,
        messages=conversation["messages"][2:],
        start_message_index=2,
    )

    assert {row["predicate"] for row in graph["relationships"]} == {"changed_to"}
    assert graph["relationships"][0]["provenance"][0]["message_index"] == 2


def test_extract_memory_graph_rejects_negative_start_message_index() -> None:
    conversation = {
        "id": "conv-graph-invalid-index",
        "messages": [{"role": "user", "text": "Velvet Lantern uses PGVector."}],
    }

    try:
        extract_memory_graph(
            conversation,
            messages=conversation["messages"],
            start_message_index=-1,
        )
    except ValueError as exc:
        assert str(exc) == "start_message_index must be non-negative"
    else:
        raise AssertionError("negative message indexes must be rejected")
