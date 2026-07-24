from __future__ import annotations

import re
import sqlite3
import time
from pathlib import Path

from fastapi.testclient import TestClient

from memory.api.server import create_app
from memory.backend.metadata_store import SQLiteMetadataStore
from memory.backend.vector_store import InMemoryVectorStore
from memory.ingestion import mvp_ingestion
from memory.ingestion.mvp_ingestion_agent import MVPIngestionAgent


class StubEmbedder(mvp_ingestion.EmbeddingProvider):
    dimension: int = 32

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        return [[float(len(text))] * self.dimension for text in texts]


def _client(
    tmp_path: Path,
    monkeypatch,
    *,
    allowed_domains: list[str] | None = None,
    passport: dict[str, object] | None = None,
) -> TestClient:
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "google-client-id")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "google-client-secret")
    monkeypatch.setenv("META_CLIENT_ID", "meta-client-id")
    monkeypatch.setenv("META_CLIENT_SECRET", "meta-client-secret")
    monkeypatch.setenv("X_CLIENT_ID", "x-client-id")
    monkeypatch.setenv("X_CLIENT_SECRET", "x-client-secret")
    monkeypatch.setenv("AMH_SESSION_SECRET", "session-secret")
    connect_config: dict[str, object] = {
        "google": {
            "enabled": True,
            "allowed_domains": allowed_domains or [],
        }
    }
    if passport is not None:
        connect_config = {"passport": passport}
    store = SQLiteMetadataStore(tmp_path / "metadata.sqlite3")
    runtime = mvp_ingestion.RuntimeDependencies(
        embedding_provider=StubEmbedder(),
        metadata_store=store,
        vector_store=InMemoryVectorStore(dimension=32),
        health_state={
            "mode": "ok",
            "metadata_provider": "sqlite",
            "vector_provider": "memory",
            "vector_fallback_active": False,
            "embedding": {"provider": "local", "model": "local", "dimension": 32},
            "embedding_health": {"provider": "local", "status": "ok"},
        },
    )
    agent = MVPIngestionAgent(
        config={"providers": {"agent": "mvp"}, "interfaces": {"api": "true"}},
        runtime=runtime,
    )
    app = create_app(
        config={
            "api": {
                "auth": "oauth_resource_server",
                "public_base_url": "https://memory.example.com",
                "oauth": {
                    "authorization_servers": ["https://memory.example.com"],
                    "jwt_secret": "oauth-secret-for-connect-ui-tests",
                },
                "connect": connect_config,
            },
            "paths": {"data_dir": str(tmp_path / "data")},
            "providers": {"embeddings": "local", "vector_db": "in_memory"},
        },
        ingestion_agent=agent,
    )
    app.state.google_oauth_exchange = _google_exchange
    return TestClient(app, base_url="https://memory.example.com")


async def _google_exchange(**kwargs):
    _ = kwargs
    return {
        "iss": "https://accounts.google.com",
        "aud": "google-client-id",
        "sub": "google-subject-a",
        "email": "Alice@Example.com",
        "name": "Alice Example",
        "hd": "example.com",
        "exp": int(time.time()) + 300,
    }


async def _google_exchange_b(**kwargs):
    _ = kwargs
    return {
        "iss": "https://accounts.google.com",
        "aud": "google-client-id",
        "sub": "google-subject-b",
        "email": "bob@example.com",
        "name": "Bob Example",
        "hd": "example.com",
        "exp": int(time.time()) + 300,
    }


async def _meta_exchange(**kwargs):
    _ = kwargs
    return {
        "iss": "https://meta.example.com",
        "aud": "meta-client-id",
        "sub": "meta-subject-a",
        "email": "meta@example.com",
        "name": "Meta User",
        "exp": int(time.time()) + 300,
    }


def _login(client: TestClient) -> tuple[str, str]:
    start = client.get("/auth/google", follow_redirects=False)
    assert start.status_code == 303
    location = start.headers["location"]
    state = re.search(r"[?&]state=([^&]+)", location)
    assert state is not None
    callback = client.get(f"/auth/google/callback?code=fake-code&state={state.group(1)}")
    assert callback.status_code == 200
    token_match = re.search(r"<textarea readonly rows=\"5\">([^<]+)</textarea>", callback.text)
    assert token_match is not None
    csrf = client.cookies.get("amh_csrf")
    assert csrf is not None
    return token_match.group(1), csrf


def test_connect_routes_are_public_secret_free_and_use_configured_mcp_url(tmp_path, monkeypatch) -> None:
    client = _client(tmp_path, monkeypatch)

    root = client.get("/", follow_redirects=False)
    connect = client.get("/connect")
    callback = client.get("/auth/google/callback")
    logout = client.post("/auth/logout", follow_redirects=False)

    assert root.status_code == 307
    assert connect.status_code == 200
    assert callback.status_code == 400
    assert logout.status_code == 303
    assert "https://memory.example.com/mcp" in connect.text
    assert "google-secret" not in connect.text
    assert "oauth-secret" not in connect.text
    assert "Bearer &lt;hub-token&gt;" in connect.text
    assert "copilot mcp add --transport http" in connect.text
    assert "ai-memory-hub https://memory.example.com/mcp" in connect.text
    for client_name in ("Codex", "Copilot CLI", "Pi", "OpenCode", "Claude", "Gemini CLI"):
        assert client_name in connect.text
    assert "Unverified" in connect.text


def test_connect_ui_renders_configured_passport_providers(tmp_path, monkeypatch) -> None:
    client = _client(
        tmp_path,
        monkeypatch,
        passport={
            "providers": ["google", "meta", "x"],
            "google": {"enabled": True},
            "meta": {
                "enabled": True,
                "issuer": "https://meta.example.com",
                "authorization_url": "https://meta.example.com/oauth/authorize",
                "token_url": "https://meta.example.com/oauth/token",
            },
            "x": {"enabled": False},
        },
    )

    connect = client.get("/connect")
    ready = client.get("/ready")

    assert connect.status_code == 200
    assert 'href="/auth/google"' in connect.text
    assert 'href="/auth/meta"' in connect.text
    assert 'href="/auth/x"' not in connect.text
    passport_status = ready.json()["connect_ui"]["passport"]["providers"]
    assert passport_status["meta"]["enabled"] is True
    assert passport_status["x"]["enabled"] is False


def test_configured_meta_passport_route_uses_provider_urls(tmp_path, monkeypatch) -> None:
    client = _client(
        tmp_path,
        monkeypatch,
        passport={
            "providers": ["meta"],
            "meta": {
                "enabled": True,
                "issuer": "https://meta.example.com",
                "authorization_url": "https://meta.example.com/oauth/authorize",
                "token_url": "https://meta.example.com/oauth/token",
                "callback_url": "https://memory.example.com/auth/meta/callback",
            },
        },
    )

    start = client.get("/auth/meta", follow_redirects=False)

    assert start.status_code == 303
    assert start.headers["location"].startswith("https://meta.example.com/oauth/authorize?")
    assert "client_id=meta-client-id" in start.headers["location"]


def test_configured_meta_passport_callback_can_create_isolated_identity(
    tmp_path, monkeypatch
) -> None:
    client = _client(
        tmp_path,
        monkeypatch,
        passport={
            "providers": ["meta"],
            "meta": {
                "enabled": True,
                "issuer": "https://meta.example.com",
                "authorization_url": "https://meta.example.com/oauth/authorize",
                "token_url": "https://meta.example.com/oauth/token",
            },
        },
    )
    client.app.state.meta_oauth_exchange = _meta_exchange
    start = client.get("/auth/meta", follow_redirects=False)
    state = re.search(r"[?&]state=([^&]+)", start.headers["location"])
    assert state is not None

    callback = client.get(f"/auth/meta/callback?code=fake-code&state={state.group(1)}")

    assert callback.status_code == 200
    assert "meta@example.com" in callback.text


def test_google_callback_creates_session_and_hub_token_for_memory_access(tmp_path, monkeypatch) -> None:
    client = _client(tmp_path, monkeypatch, allowed_domains=["example.com"])
    token, _csrf = _login(client)
    with sqlite3.connect(tmp_path / "metadata.sqlite3") as conn:
        stored_session_hash, stored_csrf_hash = conn.execute(
            "SELECT session_id_hash, csrf_token_hash FROM web_sessions"
        ).fetchone()

    insert = client.post(
        "/memory/insert",
        headers={"Authorization": f"Bearer {token}"},
        json={
                "id": "82ceee02-0201-4d1f-9540-64709567f746",
            "source": "connect-ui-test",
            "timestamp": "2026-01-01T00:00:00Z",
            "messages": [{"role": "user", "text": "The connect UI phrase is blue cedar."}],
        },
    )
    search = client.post(
        "/memory/search",
        headers={"Authorization": f"Bearer {token}"},
        json={"query": "blue cedar"},
    )

    assert insert.status_code == 200
    assert search.status_code == 200
    assert search.json()["results"]
    assert stored_session_hash.startswith("pbkdf2-sha256:")
    assert stored_csrf_hash.startswith("pbkdf2-sha256:")


def test_google_callback_rejects_invalid_state_and_denied_domain(tmp_path, monkeypatch) -> None:
    client = _client(tmp_path, monkeypatch, allowed_domains=["example.org"])
    start = client.get("/auth/google", follow_redirects=False)
    state = re.search(r"[?&]state=([^&]+)", start.headers["location"])
    assert state is not None

    invalid_state = client.get("/auth/google/callback?code=fake-code&state=wrong")
    denied_domain = client.get(f"/auth/google/callback?code=fake-code&state={state.group(1)}")

    assert invalid_state.status_code == 400
    assert denied_domain.status_code == 403


def test_google_callback_rejects_wrong_audience_and_expired_token(tmp_path, monkeypatch) -> None:
    client = _client(tmp_path, monkeypatch)

    async def wrong_audience(**kwargs):
        claims = await _google_exchange(**kwargs)
        claims["aud"] = "other-client-id"
        return claims

    async def expired(**kwargs):
        claims = await _google_exchange(**kwargs)
        claims["exp"] = int(time.time()) - 1
        return claims

    client.app.state.google_oauth_exchange = wrong_audience
    wrong_start = client.get("/auth/google", follow_redirects=False)
    wrong_state = re.search(r"[?&]state=([^&]+)", wrong_start.headers["location"])
    assert wrong_state is not None
    wrong_response = client.get(f"/auth/google/callback?code=fake-code&state={wrong_state.group(1)}")

    client.app.state.google_oauth_exchange = expired
    expired_start = client.get("/auth/google", follow_redirects=False)
    expired_state = re.search(r"[?&]state=([^&]+)", expired_start.headers["location"])
    assert expired_state is not None
    expired_response = client.get(
        f"/auth/google/callback?code=fake-code&state={expired_state.group(1)}"
    )

    assert wrong_response.status_code == 403
    assert expired_response.status_code == 403


def test_logout_revokes_session_and_hub_token(tmp_path, monkeypatch) -> None:
    client = _client(tmp_path, monkeypatch)
    token, csrf = _login(client)

    before = client.post(
        "/memory/search",
        headers={"Authorization": f"Bearer {token}"},
        json={"query": "anything"},
    )
    logout = client.post("/auth/logout", data={"csrf_token": csrf}, follow_redirects=False)
    after = client.post(
        "/memory/search",
        headers={"Authorization": f"Bearer {token}"},
        json={"query": "anything"},
    )

    assert before.status_code == 200
    assert logout.status_code == 303
    assert after.status_code == 401


def test_reauth_with_different_google_account_isolates_memory(tmp_path, monkeypatch) -> None:
    client = _client(tmp_path, monkeypatch)
    owner_a_token, _csrf = _login(client)
    insert = client.post(
        "/memory/insert",
        headers={"Authorization": f"Bearer {owner_a_token}"},
        json={
            "id": "dc8508da-8875-47bd-a693-42f9f9a5620c",
            "source": "connect-ui-test",
            "timestamp": "2026-01-01T00:00:00Z",
            "messages": [{"role": "user", "text": "Alice-only recall phrase is silver pine."}],
        },
    )
    assert insert.status_code == 200

    client.app.state.google_oauth_exchange = _google_exchange_b
    owner_b_token, _csrf_b = _login(client)
    owner_b_search = client.post(
        "/memory/search",
        headers={"Authorization": f"Bearer {owner_b_token}"},
        json={"query": "silver pine"},
    )

    assert owner_b_search.status_code == 200
    assert owner_b_search.json()["results"] == []


def test_google_subjects_map_to_stable_distinct_users(tmp_path, monkeypatch) -> None:
    store = SQLiteMetadataStore(tmp_path / "metadata.sqlite3")
    alice = store.find_or_create_oauth_identity(
        provider="google",
        provider_subject="google-subject-a",
        email="Alice@Example.com",
        display_name="Alice",
    )
    alice_again = store.find_or_create_oauth_identity(
        provider="google",
        provider_subject="google-subject-a",
        email="alice@example.com",
        display_name="Alice Updated",
    )
    bob = store.find_or_create_oauth_identity(
        provider="google",
        provider_subject="google-subject-b",
        email="bob@example.com",
        display_name="Bob",
    )

    assert alice["user_id"] == alice_again["user_id"]
    assert alice["user_id"] != bob["user_id"]
    assert alice_again["email"] == "alice@example.com"
