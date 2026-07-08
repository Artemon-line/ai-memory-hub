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
