from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import jsonschema
import pytest

from memory.ingestion import mvp_ingestion
from memory.ingestion import validate as ingestion_validate


class StubEmbedder:
    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        return [[float(len(text)), float(index)] for index, text in enumerate(texts)]


class StubMetadataStore:
    def __init__(self):
        self.by_id: dict[str, dict[str, Any]] = {}

    def insert(self, conversation_json: dict[str, Any]) -> str:
        memory_id = str(conversation_json["id"])
        self.by_id[memory_id] = conversation_json
        return memory_id

    def is_fully_indexed(self, conversation_id: str) -> bool:
        # For tests, if it's in by_id, consider it indexed
        return conversation_id in self.by_id

    def get(self, memory_id: str):
        return self.by_id.get(memory_id)

    def get_many(self, ids: list[str]):
        return {id_: self.by_id[id_] for id_ in ids if id_ in self.by_id}


class StubVectorStore:
    def __init__(self):
        self.rows: list[dict[str, Any]] = []

    def insert(self, metadata_id: str, embeddings: list[dict[str, Any]], replace: bool = False) -> None:
        if replace:
            # Avoid duplicates if re-indexing
            self.rows = [row for row in self.rows if row["memory_id"] != metadata_id]
        for item in embeddings:
            self.rows.append({"memory_id": metadata_id, **item})

    def search(self, query_vector: list[float], top_k: int = 5):
        _ = query_vector
        return [
            {
                "memory_id": row["memory_id"],
                "chunk_index": row["chunk_index"],
                "role": row["role"],
                "text": row["text"],
                "score": float(index),
            }
            for index, row in enumerate(self.rows[:top_k])
        ]


def _valid_conversation() -> dict[str, Any]:
    return {
        "id": "d9fd4c95-9cb3-4fd5-b967-3027f8863210",
        "source": "manual",
        "timestamp": "2026-01-01T00:00:00Z",
        "messages": [
            {"role": "user", "text": "hello"},
            {"role": "assistant", "text": "world"},
        ],
        "metadata": {
            "imported_at": "2026-01-01T00:00:00Z",
        },
    }


def _configure_stubs(
    *,
    allow_trusted_appends: bool = False,
    chunking_strategy: str = "message",
    chunking_max_tokens: int = 800,
    chunking_overlap_tokens: int = 80,
    retrieval_vector_score_threshold: float = 7.5,
    retrieval_keyword_enabled: bool = True,
    retrieval_keyword_weight: float = 0.25,
    retrieval_metadata_weight: float = 0.15,
) -> tuple[StubMetadataStore, StubVectorStore]:
    metadata = StubMetadataStore()
    vectors = StubVectorStore()
    mvp_ingestion.configure_runtime(
        runtime=mvp_ingestion.RuntimeDependencies(
            embedding_provider=StubEmbedder(),  # type: ignore
            metadata_store=metadata,
            vector_store=vectors,
            health_state={"mode": "ok", "vector_fallback_active": False},
            allow_trusted_appends=allow_trusted_appends,
            chunking_strategy=chunking_strategy,
            chunking_max_tokens=chunking_max_tokens,
            chunking_overlap_tokens=chunking_overlap_tokens,
            retrieval_vector_score_threshold=retrieval_vector_score_threshold,
            retrieval_keyword_enabled=retrieval_keyword_enabled,
            retrieval_keyword_weight=retrieval_keyword_weight,
            retrieval_metadata_weight=retrieval_metadata_weight,
        )
    )
    return metadata, vectors


def test_ingest_messages_success() -> None:
    metadata, vectors = _configure_stubs()

    result = mvp_ingestion.ingest_messages(_valid_conversation())

    assert result == {
        "status": "ok",
        "id": "d9fd4c95-9cb3-4fd5-b967-3027f8863210",
        "deduplicated": False,
        "appended_messages": 0,
        "embedded_chunks": 2,
        "chunks": 2,
    }
    assert "d9fd4c95-9cb3-4fd5-b967-3027f8863210" in metadata.by_id
    assert len(vectors.rows) == 2
    stored = metadata.by_id["d9fd4c95-9cb3-4fd5-b967-3027f8863210"]
    assert stored["messages"][0]["hash"].startswith("sha256:")
    assert stored["metadata"]["conversation_hash"].startswith("sha256:")
    assert stored["metadata"]["updated_at"].startswith("2026-")


def test_ingest_messages_preserves_bounded_metadata_summary() -> None:
    metadata, _ = _configure_stubs()
    conversation = _valid_conversation()
    conversation["metadata"]["summary"] = "  Discussed search recall\nand profile context.  "

    mvp_ingestion.ingest_messages(conversation)

    stored = metadata.by_id["d9fd4c95-9cb3-4fd5-b967-3027f8863210"]
    assert stored["metadata"]["summary"] == "Discussed search recall and profile context."


def test_ingest_messages_rejects_non_string_metadata_summary() -> None:
    _configure_stubs()
    conversation = _valid_conversation()
    conversation["metadata"]["summary"] = ["not", "a", "summary"]

    with pytest.raises(ValueError, match="metadata.summary must be a string"):
        mvp_ingestion.ingest_messages(conversation)


def test_ingest_messages_rejects_long_metadata_summary() -> None:
    _configure_stubs()
    conversation = _valid_conversation()
    conversation["metadata"]["summary"] = "x" * 2001

    with pytest.raises(ValueError, match="metadata.summary exceeds max length"):
        mvp_ingestion.ingest_messages(conversation)


def test_ingest_messages_deduplicates_exact_content_before_embedding() -> None:
    metadata, vectors = _configure_stubs()

    first = mvp_ingestion.ingest_messages(_valid_conversation())
    duplicate = _valid_conversation()
    duplicate["id"] = "a2ea9782-cb05-4b93-9ce7-2edcfa3c6461"
    second = mvp_ingestion.ingest_messages(duplicate)

    assert second == {
        "status": "ok",
        "id": first["id"],
        "deduplicated": True,
        "appended_messages": 0,
        "embedded_chunks": 2,
        "chunks": 2,
    }
    assert len(metadata.by_id) == 1
    assert len(vectors.rows) == 2


def test_ingest_messages_appends_only_new_same_id_messages() -> None:
    metadata, vectors = _configure_stubs(allow_trusted_appends=True)
    base = _valid_conversation()
    mvp_ingestion.ingest_messages(base)
    longer = _valid_conversation()
    longer["messages"] = [
        {"role": "user", "text": "hello"},
        {"role": "assistant", "text": "world"},
        {"role": "user", "text": "next"},
    ]

    result = mvp_ingestion.ingest_messages(longer)

    assert result["deduplicated"] is False
    assert result["appended_messages"] == 1
    assert result["embedded_chunks"] == 1
    assert len(metadata.by_id["d9fd4c95-9cb3-4fd5-b967-3027f8863210"]["messages"]) == 3
    assert [row["chunk_index"] for row in vectors.rows] == [0, 1, 2]


def test_trusted_append_extracts_facts_only_from_new_messages() -> None:
    metadata, _ = _configure_stubs(allow_trusted_appends=True)
    base = _valid_conversation()
    base["messages"] = [
        {"role": "user", "text": "hello"},
    ]
    mvp_ingestion.ingest_messages(base)

    appended = _valid_conversation()
    appended["messages"] = [
        {"role": "user", "text": "hello"},
        {"role": "user", "text": "I own a Gibson Special with P90 pickups, cherry."},
    ]
    mvp_ingestion.ingest_messages(appended)

    facts = metadata._facts
    assert len(facts) == 1
    assert facts[0]["predicate"] == "owns_guitar"
    assert facts[0]["source_message_indexes"] == [1]


def test_ingest_messages_rejects_same_id_append_without_trust() -> None:
    _configure_stubs()
    mvp_ingestion.ingest_messages(_valid_conversation())
    longer = _valid_conversation()
    longer["messages"] = [
        {"role": "user", "text": "hello"},
        {"role": "assistant", "text": "world"},
        {"role": "user", "text": "next"},
    ]

    with pytest.raises(ValueError, match="unauthorized_update"):
        mvp_ingestion.ingest_messages(longer)


def test_ingest_messages_rejects_conflicting_same_id_history() -> None:
    _configure_stubs(allow_trusted_appends=True)
    mvp_ingestion.ingest_messages(_valid_conversation())
    conflict = _valid_conversation()
    conflict["messages"][0]["text"] = "edited"

    with pytest.raises(ValueError, match="duplicate_conflict"):
        mvp_ingestion.ingest_messages(conflict)


def test_normalize_rejects_bad_client_hash() -> None:
    _configure_stubs()
    conversation = _valid_conversation()
    conversation["messages"][0]["hash"] = "sha256:" + ("0" * 64)

    with pytest.raises(ValueError, match="hash does not match"):
        mvp_ingestion.ingest_messages(conversation)


def test_ingest_messages_rejects_invalid_timestamp_before_storage() -> None:
    _configure_stubs()
    conversation = _valid_conversation()
    conversation["timestamp"] = "not-a-date"

    with pytest.raises(ValueError, match="timestamp must be an ISO-8601 datetime"):
        mvp_ingestion.ingest_messages(conversation)


def test_ingest_messages_rejects_non_string_source() -> None:
    _configure_stubs()
    conversation = _valid_conversation()
    conversation["source"] = {"bad": "source"}

    with pytest.raises(ValueError, match="source must be a string"):
        mvp_ingestion.ingest_messages(conversation)


def test_strict_raw_transcript_ingestion() -> None:
    metadata, vectors = _configure_stubs()

    result = mvp_ingestion.ingest_messages(
        "User: Remember the GPU plan.\nAssistant: Stored.",
        strict_transcript=True,
    )

    assert result["status"] == "ok"
    assert result["embedded_chunks"] == 2
    stored = metadata.by_id[result["id"]]
    assert stored["messages"][0]["role"] == "user"
    assert len(vectors.rows) == 2


def test_token_chunking_splits_long_messages_with_overlap() -> None:
    metadata, vectors = _configure_stubs(
        chunking_strategy="token",
        chunking_max_tokens=3,
        chunking_overlap_tokens=1,
    )
    conversation = _valid_conversation()
    conversation["messages"] = [
        {"role": "user", "text": "alpha beta gamma delta epsilon"},
    ]

    result = mvp_ingestion.ingest_messages(conversation)

    assert result["embedded_chunks"] == 2
    assert result["chunks"] == 2
    assert [row["chunk_index"] for row in vectors.rows] == [0, 1]
    assert [row["text"] for row in vectors.rows] == [
        "alpha beta gamma",
        "gamma delta epsilon",
    ]
    stored = metadata.by_id["d9fd4c95-9cb3-4fd5-b967-3027f8863210"]
    assert [chunk["chunk_index"] for chunk in stored["metadata"]["index_chunks"]] == [
        0,
        1,
    ]


def test_token_chunking_append_continues_chunk_indexes() -> None:
    metadata, vectors = _configure_stubs(
        allow_trusted_appends=True,
        chunking_strategy="token",
        chunking_max_tokens=3,
        chunking_overlap_tokens=1,
    )
    base = _valid_conversation()
    base["messages"] = [
        {"role": "user", "text": "alpha beta gamma delta epsilon"},
    ]
    mvp_ingestion.ingest_messages(base)

    longer = _valid_conversation()
    longer["messages"] = [
        {"role": "user", "text": "alpha beta gamma delta epsilon"},
        {"role": "assistant", "text": "zeta eta theta iota kappa"},
    ]
    result = mvp_ingestion.ingest_messages(longer)

    assert result["embedded_chunks"] == 2
    assert [row["chunk_index"] for row in vectors.rows] == [0, 1, 2, 3]
    stored = metadata.by_id["d9fd4c95-9cb3-4fd5-b967-3027f8863210"]
    assert [chunk["chunk_index"] for chunk in stored["metadata"]["index_chunks"]] == [
        0,
        1,
        2,
        3,
    ]


def test_ingest_messages_invalid_json_raises() -> None:
    _configure_stubs()
    invalid = _valid_conversation()
    del invalid["messages"]

    try:
        mvp_ingestion.ingest_messages(invalid)
        assert False, "Expected validation error"
    except (jsonschema.ValidationError, ValueError):
        assert True


def test_search_and_retrieve() -> None:
    _configure_stubs()
    conversation = _valid_conversation()
    mvp_ingestion.ingest_messages(conversation)

    search_result = mvp_ingestion.search(query="hello", top_k=5)
    retrieved = mvp_ingestion.retrieve("d9fd4c95-9cb3-4fd5-b967-3027f8863210")

    assert search_result["status"] == "ok"
    assert len(search_result["results"]) >= 1
    assert retrieved is not None
    assert retrieved["id"] == "d9fd4c95-9cb3-4fd5-b967-3027f8863210"


def test_search_filters_low_confidence_vector_matches() -> None:
    _configure_stubs(retrieval_vector_score_threshold=0.5, retrieval_keyword_enabled=False)
    conversation = _valid_conversation()
    mvp_ingestion.ingest_messages(conversation)

    search_result = mvp_ingestion.search(query="unrelated", top_k=5)

    assert [row["score"] for row in search_result["results"]] == [0.0]


def test_search_recovers_exact_keyword_matches_without_vector_candidate() -> None:
    metadata, vectors = _configure_stubs(retrieval_vector_score_threshold=0.5)
    conversation = _valid_conversation()
    conversation["messages"] = [{"role": "user", "text": "rareterm deployment note"}]
    metadata.insert(conversation)
    vectors.rows = []

    search_result = mvp_ingestion.search(query="rareterm", top_k=5)

    assert search_result["results"][0]["id"] == conversation["id"]
    assert search_result["results"][0]["text"] == "rareterm deployment note"


def test_search_uses_metadata_summary_without_returning_summary_as_chunk_text() -> None:
    metadata, vectors = _configure_stubs(retrieval_vector_score_threshold=0.5)
    conversation = _valid_conversation()
    conversation["messages"] = [{"role": "user", "text": "We discussed deployment notes."}]
    conversation["metadata"]["summary"] = "Includes the phosphor-scheduler rollout decision."
    metadata.insert(conversation)
    vectors.rows = []

    search_result = mvp_ingestion.search(query="phosphor-scheduler", top_k=5)

    assert search_result["results"][0]["id"] == conversation["id"]
    assert search_result["results"][0]["text"] == "We discussed deployment notes."
    assert search_result["results"][0]["conversation"]["metadata"]["summary"] == (
        "Includes the phosphor-scheduler rollout decision."
    )


def test_search_metadata_rerank_prefers_matching_tags() -> None:
    metadata, vectors = _configure_stubs(retrieval_metadata_weight=2.0)
    generic = _valid_conversation()
    generic["id"] = "11111111-1111-4111-8111-111111111111"
    generic["messages"] = [{"role": "user", "text": "general hardware note"}]
    tagged = _valid_conversation()
    tagged["id"] = "22222222-2222-4222-8222-222222222222"
    tagged["messages"] = [{"role": "user", "text": "driver troubleshooting note"}]
    tagged["metadata"]["tags"] = ["gpu"]
    metadata.insert(generic)
    metadata.insert(tagged)
    vectors.rows = [
        {
            "memory_id": generic["id"],
            "chunk_index": 0,
            "role": "user",
            "text": "general hardware note",
        },
        {
            "memory_id": tagged["id"],
            "chunk_index": 0,
            "role": "user",
            "text": "driver troubleshooting note",
        },
    ]

    search_result = mvp_ingestion.search(query="gpu", top_k=2)

    assert search_result["results"][0]["id"] == tagged["id"]
    assert "_ranking_score" not in search_result["results"][0]


def test_ask_applies_conversation_filters_before_answering() -> None:
    _configure_stubs(retrieval_vector_score_threshold=0.0)
    codex = _valid_conversation()
    codex["source"] = "codex"
    codex["messages"] = [{"role": "user", "text": "The release codename is Amber."}]
    codex["metadata"]["tags"] = ["release"]
    opencode = _valid_conversation()
    opencode["id"] = "22222222-2222-4222-8222-222222222222"
    opencode["source"] = "opencode"
    opencode["timestamp"] = "2026-01-02T00:00:00Z"
    opencode["messages"] = [{"role": "user", "text": "The release codename is Cobalt."}]
    opencode["metadata"]["tags"] = ["release", "shared"]
    mvp_ingestion.ingest_messages(codex)
    mvp_ingestion.ingest_messages(opencode)

    result = mvp_ingestion.ask(
        "What is the release codename?",
        top_k=5,
        source="opencode",
        date_from="2026-01-02T00:00:00Z",
        tags=["shared"],
    )

    assert result["status"] == "ok"
    assert [row["id"] for row in result["results"]] == [opencode["id"]]
    assert "Cobalt" in result["answer"]
    assert "Amber" not in result["answer"]


def test_group_conversation_results_keeps_nearby_chunks_together() -> None:
    rows = [
        {"id": "conversation-a", "score": 0.01, "chunk_index": 0, "text": "a0"},
        {"id": "conversation-b", "score": 0.05, "chunk_index": 0, "text": "b0"},
        {"id": "conversation-a", "score": 0.20, "chunk_index": 1, "text": "a1"},
    ]

    grouped = mvp_ingestion.group_conversation_results(rows)

    assert [row["id"] for row in grouped] == [
        "conversation-a",
        "conversation-a",
        "conversation-b",
    ]
    assert grouped[0]["conversation_score"] == 0.01
    assert grouped[1]["conversation_score"] == 0.01
    assert grouped[0]["conversation_match_count"] == 2
    assert grouped[1]["conversation_match_count"] == 2


def test_group_conversation_results_keeps_distant_chunks_in_score_order() -> None:
    rows = [
        {"id": "conversation-a", "score": 0.01, "chunk_index": 0, "text": "a0"},
        {"id": "conversation-b", "score": 0.05, "chunk_index": 0, "text": "b0"},
        {"id": "conversation-a", "score": 0.50, "chunk_index": 1, "text": "a1"},
    ]

    grouped = mvp_ingestion.group_conversation_results(rows)

    assert [row["id"] for row in grouped] == [
        "conversation-a",
        "conversation-b",
        "conversation-a",
    ]
    assert grouped[2]["conversation_score"] == 0.01
    assert grouped[2]["conversation_match_count"] == 2


def test_search_compact_mode_groups_repeated_chunks_by_conversation() -> None:
    metadata, vectors = _configure_stubs()
    first = _valid_conversation()
    first["id"] = "11111111-1111-4111-8111-111111111111"
    first["messages"] = [{"role": "user", "text": "gpu alpha"}]
    second = _valid_conversation()
    second["id"] = "22222222-2222-4222-8222-222222222222"
    second["messages"] = [{"role": "user", "text": "gpu beta"}]
    metadata.insert(first)
    metadata.insert(second)
    vectors.rows = [
        {"memory_id": first["id"], "chunk_index": 0, "role": "user", "text": "gpu alpha"},
        {"memory_id": first["id"], "chunk_index": 1, "role": "user", "text": "gpu alpha detail"},
        {"memory_id": second["id"], "chunk_index": 0, "role": "user", "text": "gpu beta"},
    ]

    result = mvp_ingestion.search("gpu", top_k=2, result_mode="compact")

    assert [row["id"] for row in result["results"]] == [first["id"], second["id"]]
    assert result["results"][0]["matching_chunks"] == 2
    assert len(result["results"][0]["evidence_chunks"]) == 2


def test_ask_direct_memory_returns_structured_chunk_evidence() -> None:
    _configure_stubs()
    conversation = _valid_conversation()
    conversation["messages"] = [{"role": "user", "text": "rareterm deployment note"}]
    mvp_ingestion.ingest_messages(conversation)

    result = mvp_ingestion.ask("rareterm", top_k=1)

    assert result["answer_basis"] == "direct_memory"
    assert result["confidence_reason"] == "Answer built from ranked retrieved conversation chunks."
    assert result["evidence"][0]["type"] == "chunk"
    assert result["evidence"][0]["conversation_id"] == conversation["id"]
    assert result["structured_evidence"]["facts"] == []
    assert result["structured_evidence"]["results"] == result["results"]


def test_ask_no_hit_returns_empty_structured_evidence() -> None:
    _configure_stubs(retrieval_vector_score_threshold=999.0, retrieval_keyword_enabled=False)

    result = mvp_ingestion.ask("unmatched query", top_k=1)

    assert result["answer_basis"] == "not_found"
    assert result["confidence"] == "none"
    assert result["confidence_reason"] == "No matching memory or facts were found."
    assert result["evidence"] == []
    assert result["structured_evidence"] == {"facts": [], "results": []}


def test_ask_answers_direct_guitar_question_from_fact_layer() -> None:
    _configure_stubs()
    conversation = _valid_conversation()
    conversation["messages"] = [
        {"role": "user", "text": "I own a Gibson Special with P90 pickups, cherry."}
    ]
    mvp_ingestion.ingest_messages(conversation)

    result = mvp_ingestion.ask("What guitar do I own?", top_k=5)

    assert result["answer_basis"] == "fact_layer"
    assert result["confidence"] == "high"
    assert "Gibson Special" in result["answer"]
    assert result["facts"][0]["predicate"] == "owns_guitar"
    assert result["confidence_reason"] == "Extracted from a direct user statement."
    assert result["facts"][0]["source_quality"] == "direct_user_statement"
    assert result["facts"][0]["object_raw"] == result["facts"][0]["object"]
    assert result["facts"][0]["object_normalized"] == result["facts"][0]["object"]
    assert result["facts"][0]["last_confirmed_at"] == result["facts"][0]["updated_at"]
    assert result["evidence"][0]["type"] == "fact"
    assert result["evidence"][0]["used_in_answer"] is True
    assert result["evidence"][0]["source_quality"] == "direct_user_statement"
    assert result["structured_evidence"]["facts"] == result["evidence"]
    assert result["structured_evidence"]["results"] == []
    assert result["citations"][0]["source_quality"] == "direct_user_statement"
    assert result["citations"][0]["last_confirmed_at"] == result["facts"][0]["last_confirmed_at"]


def test_fact_from_assistant_statement_exposes_source_quality() -> None:
    _configure_stubs()
    conversation = _valid_conversation()
    conversation["title"] = "Velvet Lantern"
    conversation["messages"] = [{"role": "assistant", "text": "Velvet Lantern creator is Ada."}]
    mvp_ingestion.ingest_messages(conversation)

    result = mvp_ingestion.ask("Who created Velvet Lantern?", top_k=5)

    assert result["answer_basis"] == "fact_layer"
    assert result["facts"][0]["source_quality"] == "assistant_statement"
    assert result["facts"][0]["confidence_reason"] == "Extracted from an assistant statement."


def test_fact_answer_uses_normalized_object_without_rewriting_raw_value() -> None:
    _configure_stubs()
    conversation = _valid_conversation()
    conversation["messages"] = [{"role": "user", "text": "My favorite holiday is aniversary."}]
    mvp_ingestion.ingest_messages(conversation)

    result = mvp_ingestion.ask("What is my favorite holiday?", top_k=5)

    assert result["answer"] == "anniversary"
    assert result["facts"][0]["object_raw"] == "aniversary"
    assert result["facts"][0]["object_normalized"] == "anniversary"
    normalization = result["facts"][0]["qualifiers"]["normalization"]
    assert normalization["object_raw"] == "aniversary"
    assert normalization["object_normalized"] == "anniversary"
    assert normalization["spelling_corrections"] == [
        {"raw": "aniversary", "normalized": "anniversary"}
    ]


def test_fact_normalization_records_name_casing_qualifier() -> None:
    _configure_stubs()
    conversation = _valid_conversation()
    conversation["messages"] = [{"role": "user", "text": "My name is aDA lOVELACE."}]
    mvp_ingestion.ingest_messages(conversation)

    result = mvp_ingestion.ask("What is my name?", top_k=5)

    assert result["answer"] == "Ada Lovelace"
    assert result["facts"][0]["object_raw"] == "aDA lOVELACE"
    assert result["facts"][0]["object_normalized"] == "Ada Lovelace"
    assert result["facts"][0]["qualifiers"]["normalization"]["casing"] == {
        "strategy": "title_case_name",
        "raw": "aDA lOVELACE",
        "normalized": "Ada Lovelace",
    }


def test_fact_normalization_records_date_qualifier() -> None:
    _configure_stubs()
    conversation = _valid_conversation()
    conversation["messages"] = [{"role": "user", "text": "My favorite anniversary is june 16th 2026."}]
    mvp_ingestion.ingest_messages(conversation)

    result = mvp_ingestion.ask("What is my favorite anniversary?", top_k=5)

    assert result["answer"] == "June 16, 2026"
    assert result["facts"][0]["object_raw"] == "june 16th 2026"
    assert result["facts"][0]["object_normalized"] == "June 16, 2026"
    assert result["facts"][0]["qualifiers"]["normalization"]["date"] == {
        "raw": "june 16th 2026",
        "normalized": "June 16, 2026",
        "precision": "day",
    }


def test_fact_correction_supersedes_old_fact() -> None:
    _configure_stubs()
    first = _valid_conversation()
    first["id"] = "11111111-1111-4111-8111-111111111111"
    first["messages"] = [
        {"role": "user", "text": "I own a Gibson Special with P90 pickups, cherry."}
    ]
    second = _valid_conversation()
    second["id"] = "22222222-2222-4222-8222-222222222222"
    second["messages"] = [
        {"role": "user", "text": "Actually, my Gibson Special is TV yellow, not cherry."}
    ]
    mvp_ingestion.ingest_messages(first)
    mvp_ingestion.ingest_messages(second)

    result = mvp_ingestion.ask("What guitar do I own?", top_k=5)
    audit = mvp_ingestion.fact_search(
        subject="user", predicate="owns_guitar", include_superseded=True
    )

    assert result["answer_basis"] == "fact_layer"
    assert "TV yellow" in result["answer"]
    assert "cherry" not in result["answer"]
    assert any(fact["superseded_by"] for fact in audit["results"])
    assert any(fact["superseded_at"] for fact in audit["results"] if fact["superseded_by"])
    assert result["facts"][0]["source_quality"] == "corrected_by_user"
    assert result["facts"][0]["confidence_reason"] == "Extracted from a direct user correction."
    assert result["evidence"][0]["source_quality"] == "corrected_by_user"


def test_fact_and_profile_filters_cover_status_quality_and_freshness() -> None:
    _configure_stubs()
    first = _valid_conversation()
    first["id"] = "11111111-1111-4111-8111-111111111111"
    first["messages"] = [
        {"role": "user", "text": "I own a Gibson Special with P90 pickups, cherry."}
    ]
    second = _valid_conversation()
    second["id"] = "22222222-2222-4222-8222-222222222222"
    second["timestamp"] = "2026-01-02T00:00:00Z"
    second["messages"] = [
        {"role": "user", "text": "Actually, my Gibson Special is TV yellow, not cherry."}
    ]
    third = _valid_conversation()
    third["id"] = "33333333-3333-4333-8333-333333333333"
    third["source"] = "assistant"
    third["timestamp"] = "2026-01-03T00:00:00Z"
    third["messages"] = [{"role": "assistant", "text": "The creator is Ada Lovelace."}]
    mvp_ingestion.ingest_messages(first)
    mvp_ingestion.ingest_messages(second)
    mvp_ingestion.ingest_messages(third)

    superseded = mvp_ingestion.fact_search(
        subject="user", predicate="owns_guitar", status="superseded"
    )
    profile = mvp_ingestion.profile_get(
        "user",
        predicate="owns_guitar",
        source_quality="corrected_by_user",
        freshness_from="2026-01-02T00:00:00Z",
    )
    assistant_facts = mvp_ingestion.fact_search(
        source="assistant",
        source_quality="assistant_statement",
        date_from="2026-01-03T00:00:00Z",
    )

    assert len(superseded["results"]) == 1
    assert superseded["results"][0]["superseded_by"]
    assert [fact["object"] for fact in profile["facts"]] == ["Gibson Special is TV yellow"]
    assert assistant_facts["results"][0]["predicate"] == "creator"


def test_conflicting_active_facts_return_conflict_basis() -> None:
    _configure_stubs()
    first = _valid_conversation()
    first["id"] = "11111111-1111-4111-8111-111111111111"
    first["messages"] = [{"role": "user", "text": "I own a red Gibson guitar."}]
    second = _valid_conversation()
    second["id"] = "22222222-2222-4222-8222-222222222222"
    second["messages"] = [{"role": "user", "text": "I own a black Fender guitar."}]
    mvp_ingestion.ingest_messages(first)
    mvp_ingestion.ingest_messages(second)

    result = mvp_ingestion.ask("What guitar do I own?", top_k=5)

    assert result["answer_basis"] == "conflict"
    assert result["confidence"] == "low"
    assert result["confidence_reason"] == "Multiple active facts match the question but disagree."
    assert "red Gibson" in result["answer"]
    assert "black Fender" in result["answer"]


def test_fact_answer_can_include_retrieval_context_as_mixed() -> None:
    _configure_stubs()
    conversation = _valid_conversation()
    conversation["messages"] = [
        {"role": "user", "text": "I own a Gibson Special with P90 pickups, cherry."},
        {"role": "assistant", "text": "We discussed using that guitar for studio tracking."},
    ]
    mvp_ingestion.ingest_messages(conversation)

    result = mvp_ingestion.ask("What guitar do I own and what context/source discussed it?", top_k=2)

    assert result["answer_basis"] == "mixed"
    assert "Gibson Special" in result["answer"]
    assert "Context from memory" in result["answer"]
    assert result["results"]


def test_profile_and_recurring_topic_facts_are_extracted() -> None:
    _configure_stubs()
    conversation = _valid_conversation()
    conversation["messages"] = [
        {"role": "user", "text": "My name is Tyran. I work as a backend engineer."},
        {"role": "assistant", "text": "We discussed FastAPI, MCP, SQLite, and pytest."},
    ]
    mvp_ingestion.ingest_messages(conversation)

    profile = mvp_ingestion.profile_get("user")
    predicates = {fact["predicate"] for fact in profile["facts"]}
    topic_fact = next(fact for fact in profile["facts"] if fact["predicate"] == "recurring_topic")

    assert "profile_name" in predicates
    assert "profile_role" in predicates
    assert "recurring_topic" in predicates
    assert topic_fact["source_quality"] == "inferred_from_conversation"
    assert topic_fact["confidence_reason"] == "Inferred from recurring conversation topics."


def test_project_fact_questions_match_named_subjects() -> None:
    _configure_stubs()
    conversation = _valid_conversation()
    conversation["title"] = "Velvet Lantern"
    conversation["messages"] = [
        {"role": "user", "text": "Velvet Lantern creator is Ada."}
    ]
    mvp_ingestion.ingest_messages(conversation)

    result = mvp_ingestion.ask("Who created Velvet Lantern?", top_k=5)

    assert result["answer_basis"] == "fact_layer"
    assert result["answer"] == "Ada"


def test_external_fact_extractor_hook_adds_normalized_facts() -> None:
    metadata, vectors = _configure_stubs()
    runtime = mvp_ingestion._runtime()
    runtime.fact_extractor = lambda conversation, messages: [
        {"subject": "user", "predicate": "profile_identity", "object": "a tester"}
    ]
    conversation = _valid_conversation()
    conversation["messages"] = [{"role": "user", "text": "no deterministic profile fact"}]

    mvp_ingestion.ingest_messages(conversation)

    assert metadata._facts[0]["object"] == "a tester"


def test_ingest_messages_enriches_topics() -> None:
    metadata, _ = _configure_stubs()
    conversation = _valid_conversation()
    conversation["messages"] = [
        {"role": "user", "text": "Build FastAPI endpoint with MCP and SQLite search"},
        {"role": "assistant", "text": "Use pytest for tests and add docker setup"},
    ]

    mvp_ingestion.ingest_messages(conversation)
    stored = metadata.by_id["d9fd4c95-9cb3-4fd5-b967-3027f8863210"]
    topics = stored["metadata"].get("topics", [])

    assert "mcp" in topics
    assert "backend" in topics
    assert "sql" in topics
    assert "testing" in topics
    assert "docker" in topics


def test_ingest_messages_preserves_existing_topics() -> None:
    metadata, _ = _configure_stubs()
    conversation = _valid_conversation()
    conversation["metadata"]["topics"] = ["custom-topic", "mcp"]
    conversation["messages"] = [
        {"role": "user", "text": "MCP API design"},
    ]

    mvp_ingestion.ingest_messages(conversation)
    stored = metadata.by_id["d9fd4c95-9cb3-4fd5-b967-3027f8863210"]
    topics = stored["metadata"]["topics"]

    assert topics[0] == "custom-topic"
    assert topics.count("mcp") == 1


def test_schema_file_from_config_is_used(tmp_path: Path) -> None:
    schema_path = tmp_path / "conversation.custom.schema.json"
    schema = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "properties": {
            "id": {"type": "string"},
            "source": {"type": "string"},
            "timestamp": {"type": "string"},
            "messages": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "role": {"type": "string"},
                        "text": {"type": "string"},
                        "hash": {"type": "string"},
                    },
                    "required": ["role", "text", "hash"],
                },
            },
            "metadata": {
                "type": "object",
                "required": ["imported_at", "updated_at", "conversation_hash"],
            },
            "must_exist": {"type": "string"},
        },
        "required": ["id", "source", "timestamp", "messages", "metadata", "must_exist"],
        "additionalProperties": True,
    }
    schema_path.write_text(json.dumps(schema), encoding="utf-8")

    try:
        mvp_ingestion.build_runtime(
            {
                "providers": {"embeddings": "local", "vector_db": "in_memory"},
                "paths": {"data_dir": str(tmp_path / "data")},
                "schema": {"file": str(schema_path)},
            }
        )
        with pytest.raises(jsonschema.ValidationError):
            mvp_ingestion.validate_json(_valid_conversation())
    finally:
        ingestion_validate.set_schema_path(None)


def test_schema_missing_code_required_fields_fails_startup(tmp_path: Path) -> None:
    schema_path = tmp_path / "conversation.incompatible.schema.json"
    schema = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "properties": {
            "id": {"type": "string"},
            "source": {"type": "string"},
        },
        "required": ["id", "source"],
        "additionalProperties": True,
    }
    schema_path.write_text(json.dumps(schema), encoding="utf-8")

    with pytest.raises(ValueError, match="incompatible with code expectations"):
        mvp_ingestion.build_runtime(
            {
                "providers": {"embeddings": "local", "vector_db": "in_memory"},
                "paths": {"data_dir": str(tmp_path / "data")},
                "schema": {"file": str(schema_path)},
            }
        )
