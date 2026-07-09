from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import jsonschema
import pytest

from memory.ingestion import mvp_ingestion
from memory.ingestion import validate as ingestion_validate
from memory.ingestion.summary_models import (
    GeneratedSummary,
    SummaryBasis,
    SummaryProvenanceStatus,
    SummaryType,
)
from memory.ingestion.thread_models import (
    SearchResultMode,
    ThreadMetadata,
    ThreadMetadataKey,
)
from memory.observability.metrics import metrics


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


class FakeEmbeddingHTTPResponse:
    def __init__(self, payload: dict[str, Any]):
        self._payload = payload

    def __enter__(self) -> "FakeEmbeddingHTTPResponse":
        return self

    def __exit__(self, *_args: Any) -> None:
        return None

    def read(self) -> bytes:
        return json.dumps(self._payload).encode("utf-8")

    def close(self) -> None:
        return None


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
    graph_enabled: bool = False,
    retrieval_graph_enabled: bool = False,
    retrieval_graph_quality_gate_passed: bool = False,
    retrieval_advanced_scoring_enabled: bool = False,
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
            graph_enabled=graph_enabled,
            retrieval_graph_enabled=retrieval_graph_enabled,
            retrieval_graph_quality_gate_passed=retrieval_graph_quality_gate_passed,
            retrieval_advanced_scoring_enabled=retrieval_advanced_scoring_enabled,
        )
    )
    return metadata, vectors


def test_generated_summary_model_rejects_unknown_keys() -> None:
    with pytest.raises(ValueError):
        GeneratedSummary(
            id="summary:test",
            type="profile",
            target_id="user",
            text="profile_name: Tyran",
            basis="active_facts",
            provenance_status="fact_ids",
            generated_at="2026-01-01T00:00:00Z",
            unsupported_key=True,
        )


def test_generated_summary_model_dumps_enums_as_json_strings() -> None:
    summary = GeneratedSummary(
        id="summary:test",
        type=SummaryType.PROFILE,
        target_id="user",
        text="profile_name: Tyran",
        basis=SummaryBasis.ACTIVE_FACTS,
        provenance_status=SummaryProvenanceStatus.FACT_IDS,
        generated_at="2026-01-01T00:00:00Z",
    )

    payload = summary.model_dump(mode="json")

    assert payload["type"] == "profile"
    assert payload["basis"] == "active_facts"
    assert payload["provenance_status"] == "fact_ids"


def test_generated_summary_json_schema_exports_enum_values() -> None:
    schema = GeneratedSummary.model_json_schema()
    definitions = schema.get("$defs", {})

    assert definitions["SummaryType"]["enum"] == [
        "profile",
        "conversation",
        "topic",
        "project",
    ]
    assert definitions["SummaryProvenanceStatus"]["enum"] == [
        "fact_ids",
        "conversation_ids",
        "mixed",
        "none",
    ]
    assert definitions["SummaryBasis"]["enum"] == [
        "active_facts",
        "conversation_messages",
        "topic_conversations",
        "project_conversations",
    ]


def test_thread_metadata_model_rejects_unknown_keys() -> None:
    with pytest.raises(ValueError):
        ThreadMetadata(thread_id="thread-a", unsupported_key=True)


def test_thread_metadata_model_normalizes_and_exports_typed_keys() -> None:
    metadata = ThreadMetadata(
        thread_id=" thread-a ",
        upstream_thread_id=" upstream-a ",
        parent_conversation_id="11111111-1111-4111-8111-111111111111",
        related_conversation_ids=[
            "22222222-2222-4222-8222-222222222222",
            "22222222-2222-4222-8222-222222222222",
        ],
    )

    payload = metadata.to_metadata_update()

    assert payload[ThreadMetadataKey.THREAD_ID] == "thread-a"
    assert payload[ThreadMetadataKey.UPSTREAM_THREAD_ID] == "upstream-a"
    assert payload[ThreadMetadataKey.RELATED_CONVERSATION_IDS] == [
        "22222222-2222-4222-8222-222222222222"
    ]
    assert SearchResultMode.THREADS.value == "threads"


def test_ingest_messages_success() -> None:
    metadata, vectors = _configure_stubs()

    result = mvp_ingestion.ingest_messages(_valid_conversation(), owner_id="owner-a")

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
    assert {row["owner_id"] for row in vectors.rows} == {"owner-a"}
    stored = metadata.by_id["d9fd4c95-9cb3-4fd5-b967-3027f8863210"]
    assert stored["messages"][0]["hash"].startswith("sha256:")
    assert stored["metadata"]["conversation_hash"].startswith("sha256:")
    assert stored["metadata"]["updated_at"].startswith("2026-")


def test_ingest_messages_stores_generated_conversation_topic_and_project_summaries() -> None:
    metadata, _ = _configure_stubs()
    conversation = _valid_conversation()
    conversation["title"] = "GPU rollout"
    conversation["messages"] = [
        {"role": "user", "text": "Build FastAPI endpoint with MCP and SQLite search."},
        {"role": "assistant", "text": "Use pytest for tests and add docker setup."},
    ]

    mvp_ingestion.ingest_messages(conversation, owner_id="owner-a", project_id="project-a")

    stored = metadata.by_id["d9fd4c95-9cb3-4fd5-b967-3027f8863210"]
    generated = stored["metadata"]["generated_summary"]
    summaries = metadata._generated_summaries
    conversation_summary = summaries[generated["id"]]
    topic_summaries = [
        summaries[summary_id]
        for summary_id in stored["metadata"]["generated_topic_summary_ids"]
    ]
    project_summary = summaries[stored["metadata"]["generated_project_summary_id"]]

    assert conversation_summary["type"] == "conversation"
    assert conversation_summary["basis"] == "conversation_messages"
    assert conversation_summary["target_id"] == stored["id"]
    assert conversation_summary["owner_id"] == "owner-a"
    assert conversation_summary["project_id"] == "project-a"
    assert conversation_summary["provenance_status"] == "conversation_ids"
    assert conversation_summary["provenance"][0]["conversation_id"] == stored["id"]
    assert conversation_summary["provenance"][0]["source_message_indexes"] == [0, 1]
    assert "GPU rollout" in conversation_summary["text"]
    assert generated["text"] == conversation_summary["text"]
    assert {summary["type"] for summary in topic_summaries} == {"topic"}
    assert {summary["basis"] for summary in topic_summaries} == {"topic_conversations"}
    assert "mcp" in {summary["target_id"] for summary in topic_summaries}
    assert project_summary["type"] == "project"
    assert project_summary["basis"] == "project_conversations"
    assert project_summary["target_id"] == "project-a"


def test_ingest_messages_generates_auto_tags_without_overwriting_manual_tags() -> None:
    metadata, _ = _configure_stubs()
    conversation = _valid_conversation()
    conversation["source"] = "codex"
    conversation["title"] = "Velvet Lantern"
    conversation["metadata"]["tags"] = ["manual", "mcp"]
    conversation["messages"] = [
        {"role": "user", "text": "Velvet Lantern uses MCP and FastAPI."},
        {"role": "assistant", "text": "The creator is Ada Lovelace."},
    ]

    mvp_ingestion.ingest_messages(conversation)

    stored_metadata = metadata.by_id[conversation["id"]]["metadata"]
    assert stored_metadata["tags"] == ["manual", "mcp"]
    assert "mcp" not in stored_metadata["auto_tags"]
    assert "source:codex" in stored_metadata["auto_tags"]
    assert "velvet-lantern" in stored_metadata["auto_tags"]
    assert "fact:creator" in stored_metadata["auto_tags"]
    assert stored_metadata["tag_sources"]["fact:creator"] == ["fact_predicate"]


def test_ingest_messages_promotes_upstream_thread_metadata() -> None:
    metadata, _ = _configure_stubs()
    conversation = _valid_conversation()
    conversation["source"] = "codex"
    conversation["metadata"]["upstream_thread_id"] = "session-42"
    conversation["metadata"]["parent_conversation_id"] = "11111111-1111-4111-8111-111111111111"
    conversation["metadata"]["related_conversation_ids"] = [
        "22222222-2222-4222-8222-222222222222",
        "22222222-2222-4222-8222-222222222222",
    ]

    mvp_ingestion.ingest_messages(conversation)

    stored_metadata = metadata.by_id[conversation["id"]]["metadata"]
    assert stored_metadata["thread_id"] == "codex:session-42"
    assert stored_metadata["upstream_thread_id"] == "session-42"
    assert stored_metadata["parent_conversation_id"] == "11111111-1111-4111-8111-111111111111"
    assert stored_metadata["related_conversation_ids"] == [
        "22222222-2222-4222-8222-222222222222"
    ]


def test_search_uses_generated_summary_metadata_without_returning_it_as_chunk_text() -> None:
    metadata, vectors = _configure_stubs(retrieval_vector_score_threshold=0.5)
    conversation = _valid_conversation()
    conversation["title"] = "Obsidian Quartz"
    conversation["messages"] = [{"role": "user", "text": "We discussed deployment notes."}]
    mvp_ingestion.ingest_messages(conversation)
    vectors.rows = []

    search_result = mvp_ingestion.search(query="Obsidian Quartz", top_k=5)

    assert search_result["results"][0]["id"] == conversation["id"]
    assert search_result["results"][0]["text"] == "We discussed deployment notes."
    generated = search_result["results"][0]["conversation"]["metadata"]["generated_summary"]
    assert generated["type"] == "conversation"
    assert "Obsidian Quartz" in generated["text"]


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
    generated = metadata.by_id["d9fd4c95-9cb3-4fd5-b967-3027f8863210"]["metadata"][
        "generated_summary"
    ]
    summary = metadata._generated_summaries[generated["id"]]
    assert summary["provenance"][0]["source_message_indexes"] == [0, 1, 2]
    assert "next" in summary["text"]


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


def test_trusted_append_continues_same_upstream_thread_with_new_id() -> None:
    metadata, vectors = _configure_stubs(allow_trusted_appends=True)
    base = _valid_conversation()
    base["source"] = "opencode"
    base["metadata"]["upstream_thread_id"] = "thread-alpha"
    base["messages"] = [{"role": "user", "text": "first thread note"}]
    mvp_ingestion.ingest_messages(base)

    continuation = _valid_conversation()
    continuation["id"] = "22222222-2222-4222-8222-222222222222"
    continuation["source"] = "opencode"
    continuation["metadata"]["upstream_thread_id"] = "thread-alpha"
    continuation["messages"] = [
        {"role": "user", "text": "first thread note"},
        {"role": "assistant", "text": "second thread note"},
    ]

    result = mvp_ingestion.ingest_messages(continuation)

    assert result["id"] == base["id"]
    assert result["appended_messages"] == 1
    stored = metadata.by_id[base["id"]]
    assert stored["metadata"]["thread_id"] == "opencode:thread-alpha"
    assert [message["text"] for message in stored["messages"]] == [
        "first thread note",
        "second thread note",
    ]
    assert [row["chunk_index"] for row in vectors.rows] == [0, 1]


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


def test_openai_compatible_embedding_provider_uses_http_endpoint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requests: list[Any] = []

    def fake_urlopen(request: Any, timeout: int) -> FakeEmbeddingHTTPResponse:
        requests.append((request, timeout))
        assert json.loads(request.data.decode("utf-8")) == {
            "model": "nomic-embed-text",
            "input": ["alpha", "beta"],
            "dimensions": 3,
        }
        return FakeEmbeddingHTTPResponse(
            {
                "data": [
                    {"embedding": [1, 2, 3]},
                    {"embedding": [4.5, 5.5, 6.5]},
                ]
            }
        )

    monkeypatch.setattr(mvp_ingestion.urllib.request, "urlopen", fake_urlopen)

    provider = mvp_ingestion.OpenAIEmbeddingProvider(
        embedding_model="nomic-embed-text",
        dimension=3,
        base_url="http://127.0.0.1:11434/v1/",
        api_key="test-key",
    )

    assert provider.embed_texts(["alpha", "beta"]) == [
        [1.0, 2.0, 3.0],
        [4.5, 5.5, 6.5],
    ]
    request, timeout = requests[0]
    assert request.full_url == "http://127.0.0.1:11434/v1/embeddings"
    assert request.headers["Authorization"] == "Bearer test-key"
    assert timeout == 60


def test_openai_compatible_embedding_provider_redacts_http_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_urlopen(_request: Any, timeout: int) -> Any:
        _ = timeout
        raise mvp_ingestion.urllib.error.HTTPError(
            url="http://127.0.0.1:11434/v1/embeddings",
            code=401,
            msg="Unauthorized",
            hdrs={},
            fp=FakeEmbeddingHTTPResponse({"error": "api_key=secret-key"}),
        )

    monkeypatch.setattr(mvp_ingestion.urllib.request, "urlopen", fake_urlopen)
    provider = mvp_ingestion.OpenAIEmbeddingProvider(dimension=3, api_key="secret-key")

    with pytest.raises(RuntimeError) as exc_info:
        provider.embed_texts(["alpha"])

    message = str(exc_info.value)
    assert "HTTP 401" in message
    assert "secret-key" not in message
    assert "***" in message


def test_validate_conversation_enforces_schema_formats() -> None:
    conversation = _valid_conversation()
    normalized = mvp_ingestion.normalize_conversation_json(conversation)

    normalized["id"] = "not-a-uuid"
    with pytest.raises(jsonschema.ValidationError, match="is not a 'uuid'"):
        ingestion_validate.validate_conversation(normalized)

    normalized = mvp_ingestion.normalize_conversation_json(conversation)
    normalized["metadata"]["updated_at"] = "not-a-date"
    with pytest.raises(jsonschema.ValidationError, match="is not a 'date-time'"):
        ingestion_validate.validate_conversation(normalized)


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


def test_search_metadata_rerank_uses_auto_tags() -> None:
    metadata, vectors = _configure_stubs(retrieval_metadata_weight=2.0)
    generic = _valid_conversation()
    generic["id"] = "11111111-1111-4111-8111-111111111111"
    generic["messages"] = [{"role": "user", "text": "general project note"}]
    tagged = _valid_conversation()
    tagged["id"] = "22222222-2222-4222-8222-222222222222"
    tagged["source"] = "codex"
    tagged["messages"] = [{"role": "user", "text": "driver troubleshooting note"}]
    tagged["metadata"]["auto_tags"] = ["gpu"]
    tagged["metadata"]["tag_sources"] = {"gpu": ["topic"]}
    metadata.insert(generic)
    metadata.insert(tagged)
    vectors.rows = [
        {
            "memory_id": generic["id"],
            "chunk_index": 0,
            "role": "user",
            "text": "general project note",
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


def test_graph_retrieval_disabled_preserves_search_shape() -> None:
    metadata, vectors = _configure_stubs(
        graph_enabled=True,
        retrieval_keyword_enabled=False,
        retrieval_graph_enabled=False,
    )
    conversation = _valid_conversation()
    conversation["messages"] = [{"role": "user", "text": "Velvet Lantern uses PGVector."}]
    mvp_ingestion.ingest_messages(conversation)
    vectors.rows = []

    result = mvp_ingestion.search("Velvet Lantern PGVector", top_k=5)

    assert result == {"status": "ok", "results": []}
    assert metadata._graph_relationships


def test_graph_retrieval_requires_quality_gate() -> None:
    _configure_stubs(
        graph_enabled=True,
        retrieval_keyword_enabled=False,
        retrieval_graph_enabled=True,
        retrieval_graph_quality_gate_passed=False,
    )
    conversation = _valid_conversation()
    conversation["messages"] = [{"role": "user", "text": "Velvet Lantern uses PGVector."}]
    mvp_ingestion.ingest_messages(conversation)

    result = mvp_ingestion.search("Velvet Lantern PGVector", top_k=5)

    assert "diagnostics" not in result


def test_graph_retrieval_expands_candidates_after_quality_gate() -> None:
    metadata, vectors = _configure_stubs(
        graph_enabled=True,
        retrieval_keyword_enabled=False,
        retrieval_graph_enabled=True,
        retrieval_graph_quality_gate_passed=True,
    )
    conversation = _valid_conversation()
    conversation["messages"] = [{"role": "user", "text": "Velvet Lantern uses PGVector."}]
    mvp_ingestion.ingest_messages(conversation)
    vectors.rows = []

    result = mvp_ingestion.search("Velvet Lantern PGVector", top_k=5)

    assert result["results"][0]["id"] == conversation["id"]
    assert result["diagnostics"]["graph"]["candidate_count"] == 1
    assert result["diagnostics"]["graph"]["influenced_ids"] == [conversation["id"]]
    assert "_graph_relationship_id" not in result["results"][0]
    assert metadata._graph_relationships


def test_graph_backed_ask_keeps_direct_memory_answer_shape() -> None:
    _configure_stubs(
        graph_enabled=True,
        retrieval_keyword_enabled=False,
        retrieval_graph_enabled=True,
        retrieval_graph_quality_gate_passed=True,
    )
    conversation = _valid_conversation()
    conversation["messages"] = [{"role": "user", "text": "Velvet Lantern uses PGVector."}]
    mvp_ingestion.ingest_messages(conversation)
    mvp_ingestion._runtime().vector_store.rows = []

    result = mvp_ingestion.ask("Which tool does Velvet Lantern use?", top_k=5)

    assert result["answer_basis"] == "direct_memory"
    assert result["provenance"]
    assert result["confidence_reason"] == "Answer built from ranked retrieved conversation chunks."


def test_advanced_scoring_can_rerank_with_bounded_explanation() -> None:
    metadata, vectors = _configure_stubs(
        retrieval_metadata_weight=0.0,
        retrieval_keyword_weight=0.0,
        retrieval_advanced_scoring_enabled=True,
    )
    pinned = _valid_conversation()
    pinned["id"] = "11111111-1111-4111-8111-111111111111"
    pinned["messages"] = [{"role": "user", "text": "gpu setup note"}]
    pinned["metadata"]["advanced_memory"] = {"pinned": True, "importance": 1.0, "access_count": 4}
    ordinary = _valid_conversation()
    ordinary["id"] = "22222222-2222-4222-8222-222222222222"
    ordinary["messages"] = [{"role": "user", "text": "gpu setup note"}]
    metadata.insert(pinned)
    metadata.insert(ordinary)
    vectors.search = lambda query_vector, top_k=5: [  # type: ignore[method-assign]
        {"memory_id": ordinary["id"], "chunk_index": 0, "role": "user", "text": "gpu setup note", "score": 0.2},
        {"memory_id": pinned["id"], "chunk_index": 0, "role": "user", "text": "gpu setup note", "score": 0.21},
    ][:top_k]

    result = mvp_ingestion.search("gpu setup", top_k=2)

    assert result["results"][0]["id"] == pinned["id"]
    explanation = result["results"][0]["ranking_explanation"]["advanced_memory"]
    assert explanation["pinned"] is True
    assert explanation["importance"] == 1.0


def test_invalid_advanced_scoring_metadata_does_not_break_search() -> None:
    metadata, vectors = _configure_stubs(retrieval_advanced_scoring_enabled=True)
    conversation = _valid_conversation()
    conversation["metadata"]["advanced_memory"] = {"raw_payload": "ignored"}
    metadata.insert(conversation)
    vectors.rows = [
        {"memory_id": conversation["id"], "chunk_index": 0, "role": "user", "text": "gpu note", "score": 0.1}
    ]

    result = mvp_ingestion.search("gpu", top_k=1)

    assert result["results"][0]["id"] == conversation["id"]
    assert "ranking_explanation" not in result["results"][0]


def test_search_filters_by_thread_id() -> None:
    _configure_stubs(retrieval_vector_score_threshold=0.0)
    first = _valid_conversation()
    first["id"] = "11111111-1111-4111-8111-111111111111"
    first["metadata"]["thread_id"] = "thread-a"
    first["messages"] = [{"role": "user", "text": "shared release note amber"}]
    second = _valid_conversation()
    second["id"] = "22222222-2222-4222-8222-222222222222"
    second["metadata"]["thread_id"] = "thread-b"
    second["messages"] = [{"role": "user", "text": "shared release note cobalt"}]
    mvp_ingestion.ingest_messages(first)
    mvp_ingestion.ingest_messages(second)

    search_result = mvp_ingestion.search("shared release", top_k=5, thread_id="thread-b")

    assert [row["id"] for row in search_result["results"]] == [second["id"]]
    assert "cobalt" in search_result["results"][0]["text"]


def test_search_threads_mode_groups_results_by_thread() -> None:
    _configure_stubs(retrieval_vector_score_threshold=0.0)
    first = _valid_conversation()
    first["id"] = "11111111-1111-4111-8111-111111111111"
    first["metadata"]["thread_id"] = "shared-thread"
    first["messages"] = [{"role": "user", "text": "gpu alpha"}]
    second = _valid_conversation()
    second["id"] = "22222222-2222-4222-8222-222222222222"
    second["metadata"]["thread_id"] = "shared-thread"
    second["messages"] = [{"role": "user", "text": "gpu beta"}]
    third = _valid_conversation()
    third["id"] = "33333333-3333-4333-8333-333333333333"
    third["metadata"]["thread_id"] = "other-thread"
    third["messages"] = [{"role": "user", "text": "gpu gamma"}]
    mvp_ingestion.ingest_messages(first)
    mvp_ingestion.ingest_messages(second)
    mvp_ingestion.ingest_messages(third)

    search_result = mvp_ingestion.search("gpu", top_k=5, result_mode="threads")

    assert [row["thread_id"] for row in search_result["results"]] == [
        "shared-thread",
        "other-thread",
    ]
    assert search_result["results"][0]["thread_conversation_ids"] == [
        first["id"],
        second["id"],
    ]
    assert search_result["results"][0]["thread_conversation_count"] == 2


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
    conversation["metadata"]["save_intent"] = "explicit_user_request"
    conversation["metadata"]["save_intent_source"] = "codex"
    conversation["metadata"]["save_intent_evidence"] = "User asked to remember this guitar."
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
    assert result["facts"][0]["save_intent"] == "explicit_user_request"
    assert result["facts"][0]["save_intent_source"] == "codex"
    assert result["facts"][0]["qualifiers"]["save_intent_evidence"] == "User asked to remember this guitar."
    assert result["evidence"][0]["type"] == "fact"
    assert result["evidence"][0]["used_in_answer"] is True
    assert result["evidence"][0]["source_quality"] == "direct_user_statement"
    assert result["evidence"][0]["save_intent"] == "explicit_user_request"
    assert result["structured_evidence"]["facts"] == result["evidence"]
    assert result["structured_evidence"]["results"] == []
    assert result["citations"][0]["source_quality"] == "direct_user_statement"
    assert result["citations"][0]["save_intent_source"] == "codex"
    assert result["citations"][0]["last_confirmed_at"] == result["facts"][0]["last_confirmed_at"]
    assert result["provenance"][0]["save_intents"] == ["explicit_user_request"]
    assert result["provenance"][0]["save_intent_sources"] == ["codex"]


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
    second["metadata"]["save_intent"] = "user_confirmed"
    second["metadata"]["save_intent_source"] = "codex"
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
        save_intent_source="codex",
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
    assert profile["summary"]["filters"]["save_intent_source"] == "codex"
    assert assistant_facts["results"][0]["predicate"] == "creator"


def test_fact_save_intent_filters_and_auto_save_confidence() -> None:
    _configure_stubs()
    explicit = _valid_conversation()
    explicit["id"] = "11111111-1111-4111-8111-111111111111"
    explicit["metadata"]["save_intent"] = "explicit_user_request"
    explicit["metadata"]["save_intent_source"] = "codex"
    explicit["messages"] = [{"role": "user", "text": "I own a Gibson Special, cherry."}]
    auto_saved = _valid_conversation()
    auto_saved["id"] = "22222222-2222-4222-8222-222222222222"
    auto_saved["metadata"]["save_intent"] = "client_auto_save"
    auto_saved["metadata"]["save_intent_source"] = "opencode"
    auto_saved["messages"] = [{"role": "user", "text": "My favorite holiday is midsummer."}]
    mvp_ingestion.ingest_messages(explicit)
    mvp_ingestion.ingest_messages(auto_saved)

    explicit_facts = mvp_ingestion.fact_search(save_intent="explicit_user_request")
    opencode_profile = mvp_ingestion.profile_get("user", save_intent_source="opencode")

    assert [fact["predicate"] for fact in explicit_facts["results"]] == ["owns_guitar"]
    assert opencode_profile["facts"][0]["predicate"] == "favorite_holiday"
    assert opencode_profile["facts"][0]["confidence"] == "medium"
    assert opencode_profile["facts"][0]["confidence_reason"] == (
        "Extracted from client auto-save memory, so confidence is reduced."
    )


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
    assert profile["summary"]["basis"] == "active_facts"
    assert "profile_name: Tyran" in profile["summary"]["text"]
    assert profile["summary"]["active_fact_count"] == len(profile["facts"])
    assert profile["summary"]["source_quality_counts"]["direct_user_statement"] == 2
    assert profile["summary"]["source_quality_counts"]["inferred_from_conversation"] == 4
    stored = getattr(mvp_ingestion._runtime().metadata_store, "_generated_summaries")
    assert stored[profile["summary"]["id"]]["type"] == "profile"
    assert stored[profile["summary"]["id"]]["provenance_status"] == "fact_ids"


def test_profile_summary_handles_empty_filtered_view() -> None:
    _configure_stubs()

    profile = mvp_ingestion.profile_get("user", predicate="profile_name")

    assert profile["facts"] == []
    assert profile["summary"]["text"] == "No active profile facts match the requested filters."
    assert profile["summary"]["active_fact_count"] == 0
    assert profile["summary"]["filters"] == {"predicate": "profile_name", "status": "active"}


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


def test_graph_extraction_is_opt_in_for_ingestion() -> None:
    metadata, _ = _configure_stubs(graph_enabled=False)
    conversation = _valid_conversation()
    conversation["messages"] = [{"role": "user", "text": "Velvet Lantern uses PGVector."}]

    result = mvp_ingestion.ingest_messages(conversation)

    assert "graph" not in result
    assert not hasattr(metadata, "_graph_relationships")


def test_graph_records_are_stored_when_enabled() -> None:
    metadata, _ = _configure_stubs(graph_enabled=True)
    conversation = _valid_conversation()
    conversation["title"] = "Velvet Lantern"
    conversation["messages"] = [
        {"role": "user", "text": "Velvet Lantern uses PGVector. The creator is Ada Lovelace."}
    ]

    result = mvp_ingestion.ingest_messages(conversation)
    entities = mvp_ingestion.graph_entity_search()["results"]
    relationships = mvp_ingestion.graph_relationship_search()["results"]

    assert result["graph"]["entities"] >= 3
    assert {entity["normalized_name"] for entity in entities} >= {"velvet lantern", "pgvector"}
    assert {relationship["predicate"] for relationship in relationships} >= {"uses_tool", "created_by"}
    assert all(relationship["provenance"] for relationship in relationships)
    assert metadata._graph_entities


def test_conflicting_graph_relationships_are_superseded() -> None:
    _configure_stubs(graph_enabled=True, allow_trusted_appends=True)
    conversation = _valid_conversation()
    conversation["messages"] = [{"role": "user", "text": "Velvet Lantern changed to blue."}]
    mvp_ingestion.ingest_messages(conversation)

    updated = _valid_conversation()
    updated["messages"] = [
        {"role": "user", "text": "Velvet Lantern changed to blue."},
        {"role": "user", "text": "Velvet Lantern changed to green."},
    ]
    mvp_ingestion.ingest_messages(updated)

    relationships = mvp_ingestion.graph_relationship_search(include_superseded=True)["results"]
    changed = [row for row in relationships if row["predicate"] == "changed_to"]

    assert {row["object"] for row in changed} == {"blue", "green"}
    assert any(row["superseded_by"] for row in changed if row["object"] == "blue")


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


def test_ingest_messages_records_stage_metrics_and_spans(monkeypatch) -> None:
    spans: list[tuple[str, dict[str, Any], dict[str, Any]]] = []

    class FakeSpan:
        def __init__(self, record: tuple[str, dict[str, Any], dict[str, Any]]) -> None:
            self._record = record

        def set_attribute(self, key: str, value: Any) -> None:
            self._record[2][key] = value

    class FakeSpanContext:
        def __init__(self, name: str, attributes: dict[str, Any] | None = None) -> None:
            self._record = (name, dict(attributes or {}), {})

        def __enter__(self) -> FakeSpan:
            spans.append(self._record)
            return FakeSpan(self._record)

        def __exit__(self, *_args: Any) -> None:
            return None

    def fake_span(name: str, *, attributes: dict[str, Any] | None = None) -> FakeSpanContext:
        return FakeSpanContext(name, attributes)

    monkeypatch.setattr(mvp_ingestion, "start_observability_span", fake_span)
    metrics.reset()
    _configure_stubs()

    conversation = _valid_conversation()
    conversation["title"] = "Velvet Lantern"
    conversation["messages"] = [
        {"role": "user", "text": "Velvet Lantern creator is Ada."}
    ]

    mvp_ingestion.ingest_messages(conversation)
    snapshot = metrics.snapshot()

    assert {
        "ingestion.normalize",
        "ingestion.validate_schema",
        "ingestion.dedupe_lookup",
        "ingestion.metadata_insert",
        "ingestion.chunk",
        "ingestion.embedding",
        "ingestion.vector_insert",
        "ingestion.fact_extract",
    }.issubset({name for name, _attributes, _set_attributes in spans})
    for stage in (
        "normalize",
        "validate_schema",
        "dedupe_lookup",
        "metadata_insert",
        "chunk",
        "embedding",
        "vector_insert",
        "fact_extract",
    ):
        assert (
            f"memory_ingestion_stage_duration_ms{{stage={stage}}}"
            in snapshot["histograms"]
        )
    assert "Velvet Lantern creator is Ada" not in str(spans)
    assert all("memory.duration_ms" in set_attributes for _, _, set_attributes in spans)


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
