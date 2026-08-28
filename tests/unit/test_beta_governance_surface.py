from __future__ import annotations

from pathlib import Path

from memory.api.server import create_app
from memory.ingestion import mvp_ingestion
from memory.ingestion.mvp_ingestion_agent import MVPIngestionAgent
from memory.interfaces.mcp_server import build_tool_handlers

EXPECTED_MEMORY_ROUTES = {
    ("POST", "/memory/insert"),
    ("POST", "/memory/search"),
    ("POST", "/memory/retrieve"),
    ("POST", "/memory/ask"),
    ("POST", "/memory/facts/search"),
    ("POST", "/memory/profile/get"),
    ("POST", "/memory/facts/supersede"),
    ("POST", "/memory/pending/approve"),
    ("POST", "/memory/pending/reject"),
    ("GET", "/memory/projects"),
    ("GET", "/memory/projects/default"),
    ("GET", "/memory/projects/{project_id}"),
}

EXPECTED_MCP_TOOLS = {
    "memory_validate",
    "memory_insert",
    "memory_search",
    "memory_retrieve",
    "memory_ask",
    "memory_fact_search",
    "memory_profile_get",
    "memory_fact_supersede",
    "memory_pending_approve",
    "memory_pending_reject",
    "memory_project_list",
    "memory_project_default_get",
    "memory_project_get",
}

FORBIDDEN_DESTRUCTIVE_TERMS = ("delete", "update", "archive", "restore")


class StubEmbedder:
    dimension = 32

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        return [[float(len(text))] for text in texts]


class StubMetadataStore:
    def insert(self, conversation_json: dict[str, object]) -> str:
        return str(conversation_json["id"])

    def is_fully_indexed(self, conversation_id: str) -> bool:
        _ = conversation_id
        return True

    def get(self, memory_id: str) -> dict[str, object] | None:
        _ = memory_id
        return None

    def get_many(self, ids: list[str]) -> dict[str, dict[str, object]]:
        _ = ids
        return {}


class StubVectorStore:
    def insert(
        self,
        metadata_id: str,
        embeddings: list[dict[str, object]],
        replace: bool = False,
    ) -> None:
        _ = metadata_id, embeddings, replace

    def search(self, query_vector: list[float], top_k: int = 5) -> list[dict[str, object]]:
        _ = query_vector, top_k
        return []


def _agent() -> MVPIngestionAgent:
    runtime = mvp_ingestion.RuntimeDependencies(
        embedding_provider=StubEmbedder(),  # type: ignore[arg-type]
        metadata_store=StubMetadataStore(),
        vector_store=StubVectorStore(),
        health_state={"mode": "ok", "vector_fallback_active": False},
    )
    return MVPIngestionAgent(
        config={"providers": {"agent": "mvp"}, "interfaces": {"api": True}},
        runtime=runtime,
    )


def test_beta_http_memory_surface_has_no_destructive_history_routes() -> None:
    app = create_app(
        config={"providers": {"embeddings": "local", "vector_db": "memory"}},
        ingestion_agent=_agent(),
    )
    routes = {
        (method, route.path)
        for route in app.routes
        for method in getattr(route, "methods", set())
    }

    assert EXPECTED_MEMORY_ROUTES <= routes
    assert not {
        (method, path)
        for method, path in routes
        if path.startswith("/memory/") and method in {"DELETE", "PATCH", "PUT"}
    }
    assert not {
        path
        for _, path in routes
        if path.startswith("/memory/")
        for term in FORBIDDEN_DESTRUCTIVE_TERMS
        if term in path
    }


def test_beta_mcp_tool_surface_has_no_destructive_history_tools() -> None:
    handlers = build_tool_handlers(_agent())

    assert set(handlers) == EXPECTED_MCP_TOOLS
    assert not {
        tool_name
        for tool_name in handlers
        for term in FORBIDDEN_DESTRUCTIVE_TERMS
        if term in tool_name
    }


def test_public_docs_keep_beta_history_append_only() -> None:
    public_docs = [
        Path("README.md"),
        Path("docs/agents.md"),
        Path("docs/architecture.md"),
        Path("docs/features.md"),
        Path("docs/first_release_readiness_plan.md"),
        Path("docs/release_promotion_assets.md"),
    ]
    text = "\n".join(path.read_text(encoding="utf-8") for path in public_docs)

    assert "append-only memory history" in text
    assert "destructive memory update/delete" in text
    assert "archive/restore does not ship in `v0.1.0-beta`" in text
    assert "review/delete" not in text
    assert "review/deletion" not in text
