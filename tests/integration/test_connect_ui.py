from __future__ import annotations

import base64
import hashlib
import re
import sqlite3
import time
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from fastapi.testclient import TestClient
from starlette.requests import Request

from memory.api.connect_service import mcp_url_for_config
from memory.api.server import create_app
from memory.backend.metadata_store import SQLiteMetadataStore
from memory.backend.vector_store import InMemoryVectorStore
from memory.config import parse_config
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


def _pkce_challenge(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")


def _mcp_oauth_token(client: TestClient, *, state: str = "test-state") -> str:
    redirect_uri = "http://127.0.0.1:49152/oauth/callback"
    verifier = f"{state}-pkce-verifier-abcdefghijklmnopqrstuvwxyz0123456789"
    register = client.post(
        "/oauth/register",
        json={
            "client_name": "Test MCP client",
            "redirect_uris": [redirect_uri],
            "token_endpoint_auth_method": "none",
            "grant_types": ["authorization_code"],
            "response_types": ["code"],
        },
    )
    assert register.status_code == 201
    client_id = register.json()["client_id"]
    authorize = client.get(
        "/oauth/authorize",
        params={
            "response_type": "code",
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "code_challenge": _pkce_challenge(verifier),
            "code_challenge_method": "S256",
            "state": state,
            "scope": "memory:read memory:write",
            "resource": "https://memory.example.com/mcp",
        },
        follow_redirects=False,
    )
    google_state = re.search(r"[?&]state=([^&]+)", authorize.headers["location"])
    assert authorize.status_code == 303
    assert google_state is not None

    browser = TestClient(client.app, base_url="https://memory.example.com")
    callback = browser.get(
        f"/auth/google/callback?code=fake-code&state={google_state.group(1)}",
        follow_redirects=False,
    )
    redirect = urlparse(callback.headers["location"])
    params = parse_qs(redirect.query)
    token = client.post(
        "/oauth/token",
        data={
            "grant_type": "authorization_code",
            "code": params["code"][0],
            "redirect_uri": redirect_uri,
            "client_id": client_id,
            "code_verifier": verifier,
        },
    )
    assert callback.status_code == 303
    assert params["state"] == [state]
    assert token.status_code == 200
    return str(token.json()["access_token"])


def test_mcp_url_uses_request_host_when_no_public_base_url() -> None:
    config = parse_config({"api": {"auth": "none", "host": "127.0.0.1", "port": 8000}})
    request = Request(
        {
            "type": "http",
            "scheme": "https",
            "server": ("preview.example.com", 443),
            "path": "/connect",
            "headers": [(b"host", b"preview.example.com")],
        }
    )

    assert mcp_url_for_config(config, request=request) == "https://preview.example.com/mcp"


def test_mcp_url_prefers_configured_resource_and_public_base_url() -> None:
    request = Request(
        {
            "type": "http",
            "scheme": "https",
            "server": ("preview.example.com", 443),
            "path": "/connect",
            "headers": [(b"host", b"preview.example.com")],
        }
    )
    public_config = parse_config(
        {"api": {"auth": "none", "public_base_url": "https://memory.example.com"}}
    )
    resource_config = parse_config(
        {
            "api": {
                "auth": "none",
                "public_base_url": "https://memory.example.com",
                "oauth": {"resource": "https://resource.example.net/custom-mcp"},
            }
        }
    )

    assert mcp_url_for_config(public_config, request=request) == "https://memory.example.com/mcp"
    assert mcp_url_for_config(resource_config, request=request) == (
        "https://resource.example.net/custom-mcp"
    )


def test_connect_routes_are_public_secret_free_and_use_configured_mcp_url(tmp_path, monkeypatch) -> None:
    client = _client(tmp_path, monkeypatch)

    root = client.get("/", follow_redirects=False)
    connect = client.get("/connect")
    css = client.get("/connect/static/connect.css")
    callback = client.get("/auth/google/callback")
    logout = client.post("/auth/logout", follow_redirects=False)

    assert root.status_code == 307
    assert connect.status_code == 200
    assert css.status_code == 200
    assert callback.status_code == 400
    assert logout.status_code == 303
    assert "https://memory.example.com/mcp" in connect.text
    assert "google-secret" not in connect.text
    assert "oauth-secret" not in connect.text
    assert "&lt;hub-token&gt;" not in connect.text
    assert "Authorization=Bearer" not in connect.text
    assert "Sign In With Google" not in connect.text
    assert "copilot mcp add --transport http" in connect.text
    assert "--header" not in connect.text
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
    assert 'href="/auth/google"' not in connect.text
    assert 'href="/auth/meta"' not in connect.text
    assert 'href="/auth/x"' not in connect.text
    passport_status = ready.json()["connect_ui"]["passport"]["providers"]
    assert passport_status["google"]["enabled"] is True
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
    with sqlite3.connect(tmp_path / "metadata.sqlite3") as conn:
        email = conn.execute("SELECT email FROM oauth_identities").fetchone()[0]

    assert callback.status_code == 200
    assert email == "meta@example.com"


def test_google_callback_creates_session_and_hub_token_for_memory_access(tmp_path, monkeypatch) -> None:
    client = _client(tmp_path, monkeypatch, allowed_domains=["example.com"])
    token = _mcp_oauth_token(client)
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


def test_google_callback_allows_client_launched_browser_without_session_cookie(
    tmp_path, monkeypatch
) -> None:
    client = _client(tmp_path, monkeypatch, allowed_domains=["example.com"])
    start = client.get("/auth/google", follow_redirects=False)
    state = re.search(r"[?&]state=([^&]+)", start.headers["location"])
    assert state is not None

    browser = TestClient(client.app, base_url="https://memory.example.com")
    callback = browser.get(f"/auth/google/callback?code=fake-code&state={state.group(1)}")
    with sqlite3.connect(tmp_path / "metadata.sqlite3") as conn:
        session_count = conn.execute("SELECT COUNT(*) FROM web_sessions").fetchone()[0]
        token_count = conn.execute("SELECT COUNT(*) FROM auth_tokens").fetchone()[0]

    assert callback.status_code == 200
    assert session_count == 1
    assert token_count == 1


def test_oauth_authorization_code_flow_issues_mcp_bearer_token(tmp_path, monkeypatch) -> None:
    client = _client(tmp_path, monkeypatch, allowed_domains=["example.com"])
    redirect_uri = "http://127.0.0.1:49152/oauth/callback"
    verifier = "copilot-local-pkce-verifier-abcdefghijklmnopqrstuvwxyz0123456789"
    register = client.post(
        "/oauth/register",
        json={
            "client_name": "Copilot CLI",
            "redirect_uris": [redirect_uri],
            "token_endpoint_auth_method": "none",
            "grant_types": ["authorization_code"],
            "response_types": ["code"],
        },
    )
    client_id = register.json()["client_id"]

    authorize = client.get(
        "/oauth/authorize",
        params={
            "response_type": "code",
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "code_challenge": _pkce_challenge(verifier),
            "code_challenge_method": "S256",
            "state": "copilot-state",
            "scope": "memory:read memory:write",
            "resource": "https://memory.example.com/mcp",
        },
        follow_redirects=False,
    )
    google_state = re.search(r"[?&]state=([^&]+)", authorize.headers["location"])
    assert register.status_code == 201
    assert authorize.status_code == 303
    assert google_state is not None

    browser = TestClient(client.app, base_url="https://memory.example.com")
    callback = browser.get(
        f"/auth/google/callback?code=fake-code&state={google_state.group(1)}",
        follow_redirects=False,
    )
    redirect = urlparse(callback.headers["location"])
    params = parse_qs(redirect.query)
    token = client.post(
        "/oauth/token",
        data={
            "grant_type": "authorization_code",
            "code": params["code"][0],
            "redirect_uri": redirect_uri,
            "client_id": client_id,
            "code_verifier": verifier,
        },
    )
    access_token = token.json()["access_token"]
    search = client.post(
        "/memory/search",
        headers={"Authorization": f"Bearer {access_token}"},
        json={"query": "anything"},
    )
    with sqlite3.connect(tmp_path / "metadata.sqlite3") as conn:
        token_count = conn.execute("SELECT COUNT(*) FROM auth_tokens").fetchone()[0]

    assert callback.status_code == 303
    assert f"{redirect.scheme}://{redirect.netloc}{redirect.path}" == redirect_uri
    assert params["state"] == ["copilot-state"]
    assert token.status_code == 200
    assert token.json()["token_type"] == "Bearer"
    assert token.json()["scope"] == "memory:read memory:write"
    assert token_count == 1
    assert search.status_code == 200


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


def test_oauth_authorization_code_is_single_use(tmp_path, monkeypatch) -> None:
    client = _client(tmp_path, monkeypatch)
    redirect_uri = "http://127.0.0.1:49153/oauth/callback"
    verifier = "single-use-pkce-verifier-abcdefghijklmnopqrstuvwxyz0123456789"
    register = client.post(
        "/oauth/register",
        json={
            "client_name": "Test MCP client",
            "redirect_uris": [redirect_uri],
            "token_endpoint_auth_method": "none",
            "grant_types": ["authorization_code"],
            "response_types": ["code"],
        },
    )
    client_id = register.json()["client_id"]
    authorize = client.get(
        "/oauth/authorize",
        params={
            "response_type": "code",
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "code_challenge": _pkce_challenge(verifier),
            "code_challenge_method": "S256",
            "state": "single-use-state",
            "scope": "memory:read memory:write",
            "resource": "https://memory.example.com/mcp",
        },
        follow_redirects=False,
    )
    google_state = re.search(r"[?&]state=([^&]+)", authorize.headers["location"])
    assert google_state is not None
    browser = TestClient(client.app, base_url="https://memory.example.com")
    callback = browser.get(
        f"/auth/google/callback?code=fake-code&state={google_state.group(1)}",
        follow_redirects=False,
    )
    code = parse_qs(urlparse(callback.headers["location"]).query)["code"][0]
    form = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": redirect_uri,
        "client_id": client_id,
        "code_verifier": verifier,
    }
    first = client.post("/oauth/token", data=form)
    second = client.post("/oauth/token", data=form)

    assert first.status_code == 200
    assert second.status_code == 400
    assert second.json()["detail"]["error"] == "invalid_grant"


def test_reauth_with_different_google_account_isolates_memory(tmp_path, monkeypatch) -> None:
    client = _client(tmp_path, monkeypatch)
    owner_a_token = _mcp_oauth_token(client, state="owner-a")
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
    owner_b_token = _mcp_oauth_token(client, state="owner-b")
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
