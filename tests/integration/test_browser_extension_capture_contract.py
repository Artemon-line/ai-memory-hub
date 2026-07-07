from __future__ import annotations

from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from memory.api.server import create_app
from memory.backend.metadata_store import SQLiteMetadataStore
from memory.backend.vector_store import InMemoryVectorStore
from memory.config import ensure_token_hash_secret, parse_config
from memory.ingestion import mvp_ingestion
from memory.ingestion.mvp_ingestion_agent import MVPIngestionAgent

EXTENSION_ORIGIN = "chrome-extension://abcdefghijklmnopabcdefghijklmnop"


def _client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("AMH_TOKEN_HASH_SECRET", "browser-extension-test-secret")
    config = parse_config(
        {
            "api": {
                "auth": "bearer_token",
                "cors_allow_origins": [
                    EXTENSION_ORIGIN,
                    "http://127.0.0.1:5173",
                ],
            },
            "interfaces": {"api": True, "mcp": False},
            "paths": {"data_dir": str(tmp_path / "data")},
            "providers": {
                "embeddings": "local",
                "metadata_db": "sqlite",
                "vector_db": "in_memory",
            },
            "storage": {"allow_trusted_appends": True},
        }
    )
    ensure_token_hash_secret(config)
    store = SQLiteMetadataStore(tmp_path / "metadata.sqlite3")
    store.create_auth_token(
        owner_id="extension-user",
        token="token-write",
        scopes=["memory:write"],
    )
    store.create_auth_token(
        owner_id="extension-user",
        token="token-full",
        scopes=["memory:read", "memory:write"],
    )
    runtime = mvp_ingestion.RuntimeDependencies(
        embedding_provider=mvp_ingestion.LocalEmbeddingProvider(),
        metadata_store=store,
        vector_store=InMemoryVectorStore(dimension=32),
        health_state={
            "mode": "ok",
            "metadata_provider": "sqlite",
            "vector_provider": "memory",
            "vector_fallback_active": False,
            "embedding": {
                "provider": "local",
                "model": "local-deterministic-hash",
                "dimension": 32,
                "options": {},
            },
        },
        allow_trusted_appends=True,
    )
    agent = MVPIngestionAgent(config=config, runtime=runtime)
    return TestClient(create_app(config=config, ingestion_agent=agent))


def _headers(token: str = "token-write") -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "Origin": EXTENSION_ORIGIN,
    }


def _extension_payload(
    *,
    source: str,
    upstream_thread_id: str | None = None,
    messages: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    thread_id = upstream_thread_id or f"{source}-thread-{uuid4()}"
    return {
        "id": str(uuid4()),
        "source": source,
        "title": f"{source} browser capture",
        "timestamp": "2026-07-07T08:00:00Z",
        "messages": messages
        or [
            {"role": "user", "text": f"Remember the {source} browser capture contract."},
            {"role": "assistant", "text": "Captured as normalized conversation JSON."},
        ],
        "metadata": {
            "imported_at": "2026-07-07T08:01:00Z",
            "capture_client": "browser_extension",
            "capture_client_version": "0.1.0",
            "upstream_thread_id": thread_id,
            "platform": source,
            "url": f"https://example.invalid/{source}/threads/{thread_id}",
            "save_intent": "user_confirmed",
            "save_intent_source": "browser_extension",
            "tags": ["browser-extension", source],
        },
    }


@pytest.mark.parametrize(
    "source",
    ["chatgpt", "microsoft-copilot", "claude", "gemini"],
)
def test_browser_extension_payloads_use_memory_insert_contract(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, source: str
) -> None:
    client = _client(tmp_path, monkeypatch)
    payload = _extension_payload(source=source)

    response = client.post("/memory/insert", json=payload, headers=_headers())

    assert response.status_code == 200, response.text
    assert response.headers["access-control-allow-origin"] == EXTENSION_ORIGIN
    body = response.json()
    assert body["status"] == "ok"
    assert body["id"] == payload["id"]
    assert body["deduplicated"] is False


def test_browser_extension_cors_preflight_allows_configured_extension_origin(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = _client(tmp_path, monkeypatch)

    response = client.options(
        "/memory/insert",
        headers={
            "Origin": EXTENSION_ORIGIN,
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "Authorization, Content-Type",
        },
    )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == EXTENSION_ORIGIN
    assert "Authorization" in response.headers["access-control-allow-headers"]


def test_browser_extension_raw_capture_artifacts_are_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = _client(tmp_path, monkeypatch)
    payload = _extension_payload(source="chatgpt")
    payload["metadata"]["selector_evidence"] = {"message": "[data-testid='conversation']"}
    payload["raw_html"] = "<main>raw chat page</main>"

    response = client.post("/memory/insert", json=payload, headers=_headers())

    assert response.status_code == 400
    assert "raw browser capture artifacts" in response.json()["detail"]
    assert "$.raw_html" in response.json()["detail"]
    assert "$.metadata.selector_evidence" in response.json()["detail"]


def test_browser_extension_repeated_capture_appends_by_source_and_upstream_thread(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = _client(tmp_path, monkeypatch)
    upstream_thread_id = "chatgpt-thread-browser-extension-append"
    first = _extension_payload(
        source="chatgpt",
        upstream_thread_id=upstream_thread_id,
        messages=[{"role": "user", "text": "First browser extension capture."}],
    )
    second = _extension_payload(
        source="chatgpt",
        upstream_thread_id=upstream_thread_id,
        messages=[
            {"role": "user", "text": "First browser extension capture."},
            {"role": "assistant", "text": "Second visible reply was appended."},
        ],
    )
    second.pop("id")

    first_response = client.post("/memory/insert", json=first, headers=_headers())
    second_response = client.post("/memory/insert", json=second, headers=_headers())
    retrieved = client.post(
        "/memory/retrieve",
        json={"id": first_response.json()["id"]},
        headers=_headers("token-full"),
    )

    assert first_response.status_code == 200, first_response.text
    assert second_response.status_code == 200, second_response.text
    assert second_response.json()["id"] == first_response.json()["id"]
    assert second_response.json()["appended_messages"] == 1
    assert [
        message["text"] for message in retrieved.json()["memory"]["messages"]
    ] == [
        "First browser extension capture.",
        "Second visible reply was appended.",
    ]
