from __future__ import annotations

import base64
import hashlib
import re
import sqlite3
import time
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi.testclient import TestClient
from joserfc import jwt
from joserfc.jwk import RSAKey
from starlette.requests import Request

from memory.api.connect_service import client_snippet_models, mcp_url_for_config
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
    api_auth: str = "oauth_resource_server",
    observability: dict[str, object] | None = None,
    health_state: dict[str, object] | None = None,
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
        health_state=health_state
        or {
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
                "auth": api_auth,
                "public_base_url": "https://memory.example.com",
                "oauth": {
                    "authorization_servers": ["https://memory.example.com"],
                    "jwt_secret": "oauth-secret-for-connect-ui-tests",
                },
                "connect": connect_config,
            },
            "observability": observability or {},
            "paths": {"data_dir": str(tmp_path / "data")},
            "providers": {"embeddings": "local", "vector_db": "in_memory"},
        },
        ingestion_agent=agent,
    )
    app.state.google_oauth_exchange = _google_exchange
    return TestClient(app, base_url="https://memory.example.com")


async def _google_exchange(**kwargs):
    return {
        "iss": "https://accounts.google.com",
        "aud": "google-client-id",
        "sub": "google-subject-a",
        "email": "Alice@Example.com",
        "name": "Alice Example",
        "hd": "example.com",
        "nonce": str(kwargs.get("nonce") or ""),
        "exp": int(time.time()) + 300,
        "iat": int(time.time()),
    }


async def _google_exchange_b(**kwargs):
    return {
        "iss": "https://accounts.google.com",
        "aud": "google-client-id",
        "sub": "google-subject-b",
        "email": "bob@example.com",
        "name": "Bob Example",
        "hd": "example.com",
        "nonce": str(kwargs.get("nonce") or ""),
        "exp": int(time.time()) + 300,
        "iat": int(time.time()),
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


class _OAuthHTTPResponse:
    def __init__(self, payload: dict[str, object], *, status_code: int = 200) -> None:
        self._payload = payload
        self.status_code = status_code

    def json(self) -> dict[str, object]:
        return self._payload


class _OAuthHTTPClient:
    token_requests: list[dict[str, object]] = []

    def __init__(
        self, *, id_token: str, jwks: dict[str, object] | None = None, post_status: int = 200
    ) -> None:
        self.id_token = id_token
        self.jwks = jwks or {"keys": []}
        self.post_status = post_status

    async def __aenter__(self) -> "_OAuthHTTPClient":
        return self

    async def __aexit__(self, *_args: object) -> None:
        return None

    async def post(
        self, _url: str, *, data: dict[str, object], headers: dict[str, str]
    ) -> _OAuthHTTPResponse:
        _ = headers
        self.token_requests.append(dict(data))
        return _OAuthHTTPResponse({"id_token": self.id_token}, status_code=self.post_status)

    async def get(self, url: str, *, headers: dict[str, str]) -> _OAuthHTTPResponse:
        _ = headers
        if url == "https://accounts.google.com/.well-known/openid-configuration":
            return _OAuthHTTPResponse(
                {
                    "issuer": "https://accounts.google.com",
                    "jwks_uri": "https://accounts.google.com/oauth2/v3/certs",
                }
            )
        if url == "https://accounts.google.com/oauth2/v3/certs":
            return _OAuthHTTPResponse(self.jwks)
        return _OAuthHTTPResponse({}, status_code=404)


def _disable_google_exchange_override(client: TestClient) -> None:
    client.app.state._state.pop("google_oauth_exchange", None)


def _install_oidc_httpx(monkeypatch, *, id_token: str, jwks: dict[str, object]) -> None:
    import httpx

    _OAuthHTTPClient.token_requests = []
    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda *args, **kwargs: _OAuthHTTPClient(id_token=id_token, jwks=jwks),
    )


def _signed_google_id_token(
    key: RSAKey, *, nonce: str, claims: dict[str, object] | None = None
) -> str:
    now = int(time.time())
    payload = {
        "iss": "https://accounts.google.com",
        "aud": "google-client-id",
        "sub": "google-subject-a",
        "email": "Alice@Example.com",
        "name": "Alice Example",
        "hd": "example.com",
        "nonce": nonce,
        "exp": now + 300,
        "iat": now,
    }
    if claims:
        payload.update(claims)
    return jwt.encode({"alg": "RS256", "kid": "google-key-a"}, payload, key)


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
    assert f"{redirect.scheme}://{redirect.netloc}{redirect.path}" == redirect_uri
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


def test_copilot_cli_snippet_is_verified() -> None:
    snippets = client_snippet_models(mcp_url="https://memory.example.com/mcp")
    copilot = next(snippet for snippet in snippets if snippet["name"] == "Copilot CLI")

    assert copilot["status"] == "Verified"
    assert copilot["snippet"] == (
        "copilot mcp add --transport http ai-memory-hub https://memory.example.com/mcp"
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
    assert "codex mcp add ai-memory-hub-local --url https://memory.example.com/mcp" in connect.text
    assert (
        "openclaw mcp add ai-memory-hub-local --url https://memory.example.com/mcp "
        "--transport streamable-http --auth oauth"
    ) in connect.text
    assert "openclaw mcp login ai-memory-hub-local" in connect.text
    assert "openclaw mcp login ai-memory-hub-local --code &lt;code&gt;" in connect.text
    assert "copilot mcp add --transport http" in connect.text
    assert "--header" not in connect.text
    for client_name in ("Codex", "Copilot CLI", "Pi", "OpenCode", "Claude", "OpenClaw", "Gemini CLI"):
        assert client_name in connect.text
    assert "OpenShell" not in connect.text
    assert "Unverified" in connect.text
    assert "OAuth resource server" in connect.text
    assert "Diagnostics" in connect.text
    assert "Metadata store" in connect.text
    assert "sqlite" in connect.text
    assert "Vector store" in connect.text
    assert "memory" in connect.text
    assert "Embeddings" in connect.text
    assert "local / local" in connect.text
    assert "OpenTelemetry traces" in connect.text
    assert "disabled" in connect.text


def test_connect_oauth_rejects_known_multi_worker_state(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("WEB_CONCURRENCY", "2")

    with pytest.raises(RuntimeError, match="process-local state"):
        _client(tmp_path, monkeypatch)


def test_connect_no_auth_allows_multi_worker_env_hint(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("WEB_CONCURRENCY", "2")

    client = _client(tmp_path, monkeypatch, api_auth="none")
    connect = client.get("/connect")

    assert connect.status_code == 200


def test_logout_rejects_oversized_form_without_revoking_session(tmp_path, monkeypatch) -> None:
    client = _client(tmp_path, monkeypatch, allowed_domains=["example.com"])
    start = client.get("/auth/google", follow_redirects=False)
    state = parse_qs(urlparse(start.headers["location"]).query)["state"][0]
    callback = client.get(f"/auth/google/callback?code=fake-code&state={state}")
    csrf_token = client.cookies.get("amh_csrf")
    oversized_body = f"csrf_token={csrf_token}&padding={'x' * 5000}"

    response = client.post(
        "/auth/logout",
        content=oversized_body,
        headers={"content-type": "application/x-www-form-urlencoded"},
        follow_redirects=False,
    )
    with sqlite3.connect(tmp_path / "metadata.sqlite3") as conn:
        revoked_at = conn.execute("SELECT revoked_at FROM web_sessions").fetchone()[0]

    assert start.status_code == 303
    assert callback.status_code == 200
    assert response.status_code == 413
    assert revoked_at is None


def test_logout_accepts_bounded_form_and_revokes_session(tmp_path, monkeypatch) -> None:
    client = _client(tmp_path, monkeypatch, allowed_domains=["example.com"])
    start = client.get("/auth/google", follow_redirects=False)
    state = parse_qs(urlparse(start.headers["location"]).query)["state"][0]
    callback = client.get(f"/auth/google/callback?code=fake-code&state={state}")
    csrf_token = client.cookies.get("amh_csrf")

    response = client.post(
        "/auth/logout",
        data={"csrf_token": csrf_token},
        follow_redirects=False,
    )
    with sqlite3.connect(tmp_path / "metadata.sqlite3") as conn:
        revoked_at = conn.execute("SELECT revoked_at FROM web_sessions").fetchone()[0]

    assert start.status_code == 303
    assert callback.status_code == 200
    assert response.status_code == 303
    assert revoked_at is not None


def test_logout_rejects_malformed_form_encoding(tmp_path, monkeypatch) -> None:
    client = _client(tmp_path, monkeypatch)

    response = client.post(
        "/auth/logout",
        content=b"csrf_token=\xff",
        headers={"content-type": "application/x-www-form-urlencoded"},
        follow_redirects=False,
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "Invalid logout form encoding"


def test_connect_ui_renders_local_no_auth_mode_without_hiding_setup(
    tmp_path, monkeypatch
) -> None:
    client = _client(
        tmp_path,
        monkeypatch,
        api_auth="none",
        observability={
            "tracing": {"enabled": True, "endpoint": "http://otel.example.local:4317"},
            "metrics": {"enabled": True, "endpoint": "http://otel.example.local:4317"},
        },
    )

    connect = client.get("/connect")

    assert connect.status_code == 200
    assert "Local no-auth mode" in connect.text
    assert "loopback or trusted networks" in connect.text
    assert "codex mcp add ai-memory-hub-local --url https://memory.example.com/mcp" in connect.text
    assert "Sign In With Google" not in connect.text
    assert 'href="/auth/google"' not in connect.text
    assert "google-secret" not in connect.text
    assert "oauth-secret" not in connect.text
    assert "OpenTelemetry traces" in connect.text
    assert "OpenTelemetry metrics" in connect.text
    assert "enabled" in connect.text
    assert "http://otel.example.local:4317" not in connect.text


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


def test_oauth_token_rejects_unconfigured_admin_scope(tmp_path, monkeypatch) -> None:
    client = _client(tmp_path, monkeypatch, allowed_domains=["example.com"])
    redirect_uri = "http://127.0.0.1:49152/oauth/callback"
    verifier = "admin-scope-pkce-verifier-abcdefghijklmnopqrstuvwxyz0123456789"
    register = client.post(
        "/oauth/register",
        json={
            "client_name": "Admin scope client",
            "redirect_uris": [redirect_uri],
            "token_endpoint_auth_method": "none",
            "grant_types": ["authorization_code"],
            "response_types": ["code"],
        },
    )
    authorize = client.get(
        "/oauth/authorize",
        params={
            "response_type": "code",
            "client_id": register.json()["client_id"],
            "redirect_uri": redirect_uri,
            "code_challenge": _pkce_challenge(verifier),
            "code_challenge_method": "S256",
            "scope": "memory:read memory:write memory:admin",
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
    token = client.post(
        "/oauth/token",
        data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri,
            "client_id": register.json()["client_id"],
            "code_verifier": verifier,
        },
    )

    assert token.status_code == 400
    assert token.json()["detail"]["error"] == "invalid_scope"


def test_oauth_register_rejects_malformed_json(tmp_path, monkeypatch) -> None:
    client = _client(tmp_path, monkeypatch)

    response = client.post(
        "/oauth/register",
        content="{",
        headers={"content-type": "application/json"},
    )

    assert response.status_code == 400
    assert response.json()["detail"]["error"] == "invalid_client_metadata"


@pytest.mark.parametrize(
    "redirect_uri",
    [
        None,
        42,
        {"uri": "http://127.0.0.1/callback"},
        "/oauth/callback",
        "javascript:alert(1)",
        "http://memory.example.com/callback",
        "https://memory.example.com/callback",
        "http://127.0.0.1/callback#fragment",
        " http://127.0.0.1/callback",
        "http://user:pass@127.0.0.1/callback",
        "http://[::1",
    ],
)
def test_oauth_register_rejects_untrusted_redirect_uris(
    tmp_path, monkeypatch, redirect_uri: object
) -> None:
    client = _client(tmp_path, monkeypatch)

    response = client.post(
        "/oauth/register",
        json={
            "client_name": "Untrusted client",
            "redirect_uris": [redirect_uri],
        },
    )

    assert response.status_code == 400
    assert response.json()["detail"]["error"] == "invalid_client_metadata"


@pytest.mark.parametrize(
    "redirect_uri",
    [
        "http://localhost/oauth/callback",
        "http://localhost:49152/oauth/callback",
        "http://127.0.0.1:49152/oauth/callback",
        "http://127.1.2.3:49152/oauth/callback",
        "http://[::1]:49152/oauth/callback",
        "https://localhost/oauth/callback",
    ],
)
def test_oauth_register_accepts_loopback_redirect_uris(
    tmp_path, monkeypatch, redirect_uri: str
) -> None:
    client = _client(tmp_path, monkeypatch)

    response = client.post(
        "/oauth/register",
        json={
            "client_name": "Local MCP client",
            "redirect_uris": [redirect_uri],
        },
    )

    assert response.status_code == 201
    assert response.json()["redirect_uris"] == [redirect_uri]


def test_logged_in_user_must_approve_registered_loopback_client(tmp_path, monkeypatch) -> None:
    client = _client(tmp_path, monkeypatch, allowed_domains=["example.com"])
    login = client.get("/auth/google", follow_redirects=False)
    login_state = parse_qs(urlparse(login.headers["location"]).query)["state"][0]
    callback = client.get(f"/auth/google/callback?code=fake-code&state={login_state}")
    csrf_token = client.cookies.get("amh_csrf")
    redirect_uri = "http://127.0.0.1:49152/oauth/callback"
    verifier = "logged-in-pkce-verifier-abcdefghijklmnopqrstuvwxyz0123456789"
    register = client.post(
        "/oauth/register",
        json={
            "client_name": "Local MCP client",
            "redirect_uris": [redirect_uri],
        },
    )

    authorize = client.get(
        "/oauth/authorize",
        params={
            "response_type": "code",
            "client_id": register.json()["client_id"],
            "redirect_uri": redirect_uri,
            "code_challenge": _pkce_challenge(verifier),
            "code_challenge_method": "S256",
            "state": "local-client-state",
            "scope": "memory:read memory:write",
            "resource": "https://memory.example.com/mcp",
        },
        follow_redirects=False,
    )
    connect = client.get(authorize.headers["location"])
    approve = client.post(
        "/oauth/authorize/approve",
        data={"csrf_token": csrf_token},
        follow_redirects=False,
    )
    redirect = urlparse(approve.headers["location"])
    params = parse_qs(redirect.query)

    assert login.status_code == 303
    assert callback.status_code == 200
    assert register.status_code == 201
    assert authorize.status_code == 303
    assert authorize.headers["location"] == "/connect"
    assert connect.status_code == 200
    assert "Authorize local client" in connect.text
    assert "Local MCP client" in connect.text
    assert redirect_uri in connect.text
    assert approve.status_code == 303
    assert f"{redirect.scheme}://{redirect.netloc}{redirect.path}" == redirect_uri
    assert params["state"] == ["local-client-state"]
    assert params["code"][0].startswith("amh_code_")


def test_oauth_authorize_approve_rejects_missing_csrf(tmp_path, monkeypatch) -> None:
    client = _client(tmp_path, monkeypatch, allowed_domains=["example.com"])
    login = client.get("/auth/google", follow_redirects=False)
    login_state = parse_qs(urlparse(login.headers["location"]).query)["state"][0]
    callback = client.get(f"/auth/google/callback?code=fake-code&state={login_state}")
    register = client.post(
        "/oauth/register",
        json={"client_name": "Local MCP client", "redirect_uris": ["http://127.0.0.1/cb"]},
    )
    authorize = client.get(
        "/oauth/authorize",
        params={
            "response_type": "code",
            "client_id": register.json()["client_id"],
            "redirect_uri": "http://127.0.0.1/cb",
            "code_challenge": _pkce_challenge(
                "csrf-pkce-verifier-abcdefghijklmnopqrstuvwxyz0123456789"
            ),
            "code_challenge_method": "S256",
        },
        follow_redirects=False,
    )

    approve = client.post("/oauth/authorize/approve", data={}, follow_redirects=False)

    assert callback.status_code == 200
    assert authorize.status_code == 303
    assert approve.status_code == 403


def test_oauth_token_rejects_non_ascii_pkce_verifier_as_invalid_grant(
    tmp_path, monkeypatch
) -> None:
    client = _client(tmp_path, monkeypatch)
    client.app.state.oauth_authorization_codes = {
        "amh_code_non_ascii": {
            "client_id": "amh_client_test",
            "redirect_uri": "http://127.0.0.1:49152/oauth/callback",
            "code_challenge": _pkce_challenge(
                "valid-pkce-verifier-abcdefghijklmnopqrstuvwxyz0123456789"
            ),
            "owner_id": "owner-a",
            "expires_at": int(time.time()) + 300,
            "resource": "https://memory.example.com/mcp",
        }
    }

    response = client.post(
        "/oauth/token",
        data={
            "grant_type": "authorization_code",
            "code": "amh_code_non_ascii",
            "redirect_uri": "http://127.0.0.1:49152/oauth/callback",
            "client_id": "amh_client_test",
            "code_verifier": "invalid-pkce-verifier-éabcdefghijklmnopqrstuvwxyz0123456789",
        },
    )

    assert response.status_code == 400
    assert response.json()["detail"]["error"] == "invalid_grant"


def test_oauth_token_sweeps_expired_authorization_codes(tmp_path, monkeypatch) -> None:
    client = _client(tmp_path, monkeypatch)
    client.app.state.oauth_authorization_codes = {
        "amh_code_expired": {"expires_at": int(time.time()) - 1},
        "amh_code_live": {"expires_at": int(time.time()) + 300},
    }

    response = client.post(
        "/oauth/token",
        data={"grant_type": "authorization_code", "code": "amh_code_expired"},
    )

    assert response.status_code == 400
    assert response.json()["detail"]["error"] == "invalid_grant"
    assert "amh_code_expired" not in client.app.state.oauth_authorization_codes
    assert "amh_code_live" in client.app.state.oauth_authorization_codes


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


def test_google_callback_verifies_signed_id_token_and_matching_nonce(
    tmp_path, monkeypatch
) -> None:
    client = _client(tmp_path, monkeypatch, allowed_domains=["example.com"])
    _disable_google_exchange_override(client)
    key = RSAKey.generate_key(2048, parameters={"kid": "google-key-a", "alg": "RS256"})
    start = client.get("/auth/google", follow_redirects=False)
    redirect = urlparse(start.headers["location"])
    params = parse_qs(redirect.query)
    id_token = _signed_google_id_token(key, nonce=params["nonce"][0])
    _install_oidc_httpx(
        monkeypatch,
        id_token=id_token,
        jwks={"keys": [key.as_dict(private=False)]},
    )

    callback = client.get(f"/auth/google/callback?code=fake-code&state={params['state'][0]}")

    assert start.status_code == 303
    assert callback.status_code == 200


def test_google_login_uses_provider_pkce_s256_and_token_verifier(
    tmp_path, monkeypatch
) -> None:
    client = _client(tmp_path, monkeypatch, allowed_domains=["example.com"])
    _disable_google_exchange_override(client)
    key = RSAKey.generate_key(2048, parameters={"kid": "google-key-a", "alg": "RS256"})
    start = client.get("/auth/google", follow_redirects=False)
    params = parse_qs(urlparse(start.headers["location"]).query)
    id_token = _signed_google_id_token(key, nonce=params["nonce"][0])
    _install_oidc_httpx(
        monkeypatch,
        id_token=id_token,
        jwks={"keys": [key.as_dict(private=False)]},
    )

    callback = client.get(f"/auth/google/callback?code=fake-code&state={params['state'][0]}")
    token_request = _OAuthHTTPClient.token_requests[0]
    code_verifier = str(token_request["code_verifier"])

    assert start.status_code == 303
    assert callback.status_code == 200
    assert params["code_challenge_method"] == ["S256"]
    assert params["code_challenge"] == [_pkce_challenge(code_verifier)]
    assert code_verifier not in start.headers["location"]
    assert client.app.state.oauth_provider_states == {}


def test_google_callback_rejects_unsigned_id_token(tmp_path, monkeypatch) -> None:
    client = _client(tmp_path, monkeypatch)
    _disable_google_exchange_override(client)
    key = RSAKey.generate_key(2048, parameters={"kid": "google-key-a", "alg": "RS256"})
    start = client.get("/auth/google", follow_redirects=False)
    state = parse_qs(urlparse(start.headers["location"]).query)["state"][0]
    _install_oidc_httpx(
        monkeypatch,
        id_token="not-a-signed-token",
        jwks={"keys": [key.as_dict(private=False)]},
    )

    response = client.get(f"/auth/google/callback?code=fake-code&state={state}")

    assert response.status_code == 403


def test_google_callback_rejects_wrong_nonce_and_replayed_state(tmp_path, monkeypatch) -> None:
    client = _client(tmp_path, monkeypatch)
    _disable_google_exchange_override(client)
    key = RSAKey.generate_key(2048, parameters={"kid": "google-key-a", "alg": "RS256"})
    start = client.get("/auth/google", follow_redirects=False)
    state = parse_qs(urlparse(start.headers["location"]).query)["state"][0]
    id_token = _signed_google_id_token(key, nonce="attacker-nonce")
    _install_oidc_httpx(
        monkeypatch,
        id_token=id_token,
        jwks={"keys": [key.as_dict(private=False)]},
    )

    wrong_nonce = client.get(f"/auth/google/callback?code=fake-code&state={state}")
    replay = client.get(f"/auth/google/callback?code=fake-code&state={state}")

    assert wrong_nonce.status_code == 403
    assert replay.status_code == 400


def test_google_callback_rejects_expired_state(tmp_path, monkeypatch) -> None:
    client = _client(tmp_path, monkeypatch)
    start = client.get("/auth/google", follow_redirects=False)
    state = parse_qs(urlparse(start.headers["location"]).query)["state"][0]
    client.app.state.oauth_provider_states[state]["created_at"] = int(time.time()) - 601

    response = client.get(f"/auth/google/callback?code=fake-code&state={state}")

    assert response.status_code == 400


def test_google_oauth_state_store_is_capped(tmp_path, monkeypatch) -> None:
    client = _client(tmp_path, monkeypatch)

    for _ in range(140):
        response = client.get("/auth/google", follow_redirects=False)
        assert response.status_code == 303

    assert len(client.app.state.oauth_provider_states) <= 128


def test_oauth_registered_client_store_is_capped_and_expires_records(
    tmp_path, monkeypatch
) -> None:
    client = _client(tmp_path, monkeypatch)

    expired = client.post(
        "/oauth/register",
        json={"client_name": "expired", "redirect_uris": ["http://127.0.0.1/expired"]},
    )
    expired_client_id = expired.json()["client_id"]
    client.app.state.oauth_registered_clients[expired_client_id]["expires_at"] = (
        int(time.time()) - 1
    )
    for index in range(140):
        response = client.post(
            "/oauth/register",
            json={
                "client_name": f"client-{index}",
                "redirect_uris": [f"http://127.0.0.1:{49152 + index}/callback"],
            },
        )
        assert response.status_code == 201

    assert expired_client_id not in client.app.state.oauth_registered_clients
    assert len(client.app.state.oauth_registered_clients) <= 128


def test_oauth_authorization_code_store_is_capped(tmp_path, monkeypatch) -> None:
    client = _client(tmp_path, monkeypatch)
    redirect_uri = "http://127.0.0.1:49152/oauth/callback"
    client.app.state.oauth_registered_clients = {
        "amh_client_test": {
            "client_id": "amh_client_test",
            "client_name": "Test MCP client",
            "redirect_uris": [redirect_uri],
            "created_at": int(time.time()),
            "expires_at": int(time.time()) + 300,
        }
    }
    client.app.state.oauth_authorization_codes = {}
    for index in range(140):
        client.app.state.oauth_authorization_codes[f"amh_code_old_{index}"] = {
            "created_at": index,
            "expires_at": int(time.time()) + 300,
        }

    token = _mcp_oauth_token(client, state="cap-state")

    assert token
    assert len(client.app.state.oauth_authorization_codes) <= 128


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
    assert callback.status_code == 303
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
