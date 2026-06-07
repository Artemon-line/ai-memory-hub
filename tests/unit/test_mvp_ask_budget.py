from __future__ import annotations

from memory.ingestion import mvp_ingestion


class _Embedder:
    dimension = 1

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        return [[1.0] for _ in texts]


def test_select_ask_context_prefers_ranked_chunks_and_drops_overflow(monkeypatch) -> None:
    def count_words(text: str, encoding: str) -> int:
        _ = encoding
        return len(text.split())

    def truncate_words(text: str, max_tokens: int, encoding: str) -> str:
        _ = encoding
        return " ".join(text.split()[:max_tokens])

    monkeypatch.setattr(mvp_ingestion, "count_tokens", count_words)
    monkeypatch.setattr(mvp_ingestion, "truncate_to_tokens", truncate_words)

    matches = [
        {
            "id": "memory-a",
            "chunk_index": 0,
            "score": 0.1,
            "text": "alpha beta gamma delta",
        },
        {
            "id": "memory-b",
            "chunk_index": 0,
            "score": 0.2,
            "text": "epsilon zeta",
        },
    ]

    selected, citations, lines, tokens_used, dropped = mvp_ingestion._select_ask_context(
        matches=matches,
        max_context_tokens=5,
        encoding="test",
    )

    assert len(selected) == 1
    assert selected[0]["id"] == "memory-a"
    assert selected[0]["text"] == "alpha beta gamma"
    assert citations == [
        {
            "id": "memory-a",
            "chunk_index": 0,
            "score": 0.1,
            "text": "alpha beta gamma",
        }
    ]
    assert lines == ["- [memory-a#0] alpha beta gamma"]
    assert tokens_used == 5
    assert dropped == 1


def test_ask_uses_config_budget_when_tokenizer_enabled(monkeypatch) -> None:
    monkeypatch.setattr(
        mvp_ingestion,
        "search",
        lambda query, top_k: {
            "status": "ok",
            "results": [
                {
                    "id": "memory-a",
                    "chunk_index": 0,
                    "score": 0.1,
                    "text": "alpha beta gamma delta",
                }
            ],
        },
    )
    monkeypatch.setattr(mvp_ingestion, "count_tokens", lambda text, encoding: len(text.split()))
    monkeypatch.setattr(
        mvp_ingestion,
        "truncate_to_tokens",
        lambda text, max_tokens, encoding: " ".join(text.split()[:max_tokens]),
    )
    mvp_ingestion.configure_runtime(
        runtime=mvp_ingestion.RuntimeDependencies(
            embedding_provider=_Embedder(),
            metadata_store=object(),
            vector_store=object(),
            health_state={"mode": "ok"},
            tokenizer_enabled=True,
            ask_max_context_tokens=5,
        )
    )

    result = mvp_ingestion.ask("question", top_k=1)

    assert result["status"] == "ok"
    assert result["context_tokens_used"] <= 5
    assert result["chunks_selected"] == 1
    assert result["citations"][0]["text"] == "alpha beta gamma"
