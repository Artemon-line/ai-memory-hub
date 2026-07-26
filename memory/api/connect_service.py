from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import time
from datetime import UTC, datetime, timedelta
from typing import Any, TypedDict

from fastapi import HTTPException, Request

from memory.auth import READ_SCOPE, WRITE_SCOPE
from memory.backend.redaction import redact_content_hashes
from memory.config import HubConfig
from memory.ingestion.base_agent import BaseIngestionAgent

SECRET_HASH_ITERATIONS = 210_000
CLIENT_MATRIX: tuple[dict[str, str], ...] = (
    {
        "name": "Codex",
        "status": "Verified",
        "snippet": "codex mcp add ai-memory-hub-local --url {mcp_url}",
    },
    {
        "name": "Copilot CLI",
        "status": "Unverified",
        "snippet": 'copilot mcp add --transport http ai-memory-hub {mcp_url}',
    },
    {
        "name": "Pi",
        "status": "Verified",
        "snippet": "pi install npm:pi-mcp-adapter",
    },
    {
        "name": "OpenCode",
        "status": "Verified",
        "snippet": "opencode mcp add ai-memory-hub-local --url {mcp_url}",
    },
    {
        "name": "Claude",
        "status": "Verified",
        "snippet": "claude mcp add --transport http ai-memory-hub-local {mcp_url}",
    },
    {
        "name": "Hermes",
        "status": "Verified",
        "snippet": "hermes mcp add ai-memory-hub-local --url {mcp_url} --auth oauth",
    },
    {"name": "OpenShell", "status": "Unverified", "snippet": "MCP URL: {mcp_url}"},
    {"name": "OpenClaw", "status": "Unverified", "snippet": "MCP URL: {mcp_url}"},
    {
        "name": "Gemini CLI",
        "status": "Verified",
        "snippet": "gemini mcp add ai-memory-hub-local {mcp_url} -t http",
    },
)


class ConnectLoginResult(TypedDict):
    identity: dict[str, object]
    session_id: str
    csrf_token: str
    issued_token: str
    issued_token_id: str


class _Sentinel:
    value = object()


class ConnectService:
    def __init__(self, *, agent: BaseIngestionAgent, config: HubConfig) -> None:
        self.agent = agent
        self.config = config

    async def session_from_request(self, request: Request) -> dict[str, object] | None:
        session_id = request.cookies.get(self.config.api.connect.session_cookie_name)
        if not session_id:
            return None
        return await self.agent.web_session_for_hash(
            hash_secret(session_id, self.config, purpose="connect-session")
        )

    async def page_model(
        self,
        request: Request,
        *,
        signed_in: dict[str, object] | None | object = _Sentinel.value,
        issued_token: str | None = None,
        csrf_token: str | None | object = _Sentinel.value,
    ) -> dict[str, object]:
        sentinel = _Sentinel.value
        session = (
            await self.session_from_request(request)
            if signed_in is sentinel
            else signed_in
        )
        csrf = (
            _csrf_token_from_cookie(request, config=self.config)
            if csrf_token is sentinel
            else csrf_token
        )
        identity = str(session.get("email") or session.get("user_id")) if isinstance(session, dict) else ""
        mcp_url = mcp_url_for_config(self.config, request=request)
        health_state = redact_content_hashes(await self.agent.health())
        auth_mode = access_mode_model(self.config)
        return {
            "request": request,
            "signed_in": session,
            "auth_label": "Signed in" if session else "Not signed in",
            "auth_mode": auth_mode,
            "identity": identity,
            "issued_token": issued_token,
            "csrf_token": csrf,
            "mcp_url": mcp_url,
            "providers": enabled_passport_provider_models(self.config),
            "client_snippets": client_snippet_models(mcp_url=mcp_url),
            "diagnostics": diagnostics_model(config=self.config, health_state=health_state),
        }

    async def complete_login(
        self, *, provider: str, claims: dict[str, object], issue_token: bool = True
    ) -> ConnectLoginResult:
        validate_provider_claims(claims, self.config, provider=provider)
        identity = await self.agent.find_or_create_oauth_identity(
            provider=provider,
            provider_subject=str(claims["sub"]),
            email=str(claims.get("email") or ""),
            display_name=str(claims.get("name") or claims.get("email") or ""),
        )
        session_id = secrets.token_urlsafe(32)
        csrf_token = secrets.token_urlsafe(32)
        await self.agent.create_web_session(
            session_id_hash=hash_secret(session_id, self.config, purpose="connect-session"),
            user_id=str(identity["user_id"]),
            csrf_token_hash=hash_secret(csrf_token, self.config, purpose="connect-csrf"),
            expires_at=utc_after(self.config.api.connect.session_ttl_seconds),
        )
        issued_token = ""
        if issue_token:
            issued_token = issue_hub_token(config=self.config, owner_id=str(identity["user_id"]))
            await self.agent.create_auth_token(
                owner_id=str(identity["user_id"]),
                token=issued_token,
                token_display_name=f"{provider.title()} Connect UI",
                expires_at=utc_after(self.config.api.connect.token_ttl_seconds),
                scopes=[READ_SCOPE, WRITE_SCOPE],
            )
        return {
            "identity": identity,
            "session_id": session_id,
            "csrf_token": csrf_token,
            "issued_token": issued_token,
            "issued_token_id": str(jwt_payload(issued_token).get("jti") or ""),
        }

    async def logout(self, request: Request, *, csrf_token: str) -> None:
        session_id = request.cookies.get(self.config.api.connect.session_cookie_name)
        token_id = request.cookies.get("amh_token_id")
        session = await self.session_from_request(request)
        if session is not None and not csrf_matches(session, csrf_token, config=self.config):
            raise HTTPException(status_code=403, detail="Invalid CSRF token")
        if session_id:
            await self.agent.revoke_web_session(
                hash_secret(session_id, self.config, purpose="connect-session")
            )
        if token_id:
            await self.agent.revoke_auth_token(token_id)


def connect_status(config: HubConfig) -> dict[str, object]:
    providers = {
        provider: provider_status(config, provider)
        for provider in config.api.connect.passport.providers
    }
    return {
        "enabled": config.api.connect.enabled,
        "mcp_url": mcp_url_for_config(config),
        "passport": {"providers": providers},
        "google_oauth": providers.get("google", {}),
    }


def access_mode_model(config: HubConfig) -> dict[str, str]:
    if config.api.auth == "none":
        return {
            "value": "none",
            "label": "Local no-auth mode",
            "tone": "pending",
            "description": "Use on loopback or trusted networks only. No bearer token is required.",
        }
    if config.api.auth == "bearer_token":
        return {
            "value": "bearer_token",
            "label": "Bearer/API key mode",
            "tone": "ok",
            "description": "Clients must send the configured secret in an Authorization or API-key header.",
        }
    return {
        "value": "oauth_resource_server",
        "label": "OAuth resource server",
        "tone": "ok",
        "description": "MCP clients own sign-in, reauthentication, and token storage.",
    }


def diagnostics_model(
    *, config: HubConfig, health_state: dict[str, Any]
) -> dict[str, list[dict[str, str]]]:
    return {
        "server": [
            _diagnostic_row("Ready mode", health_state.get("mode")),
            _diagnostic_row("Metadata store", health_state.get("metadata_provider")),
            _diagnostic_row("Vector store", health_state.get("vector_provider")),
            _diagnostic_row("Embeddings", _embedding_label(config=config, health_state=health_state)),
            _diagnostic_row(
                "Vector fallback",
                "active" if health_state.get("vector_fallback_active") else "inactive",
                tone="pending" if health_state.get("vector_fallback_active") else "ok",
            ),
        ],
        "observability": [
            _diagnostic_row(
                "Structured logs",
                "enabled" if config.observability.logging.enabled else "disabled",
                tone="ok" if config.observability.logging.enabled else "pending",
            ),
            _diagnostic_row(
                "OpenTelemetry traces",
                "enabled" if config.observability.tracing.enabled else "disabled",
                tone="ok" if config.observability.tracing.enabled else "pending",
            ),
            _diagnostic_row(
                "OpenTelemetry metrics",
                "enabled" if config.observability.metrics.enabled else "disabled",
                tone="ok" if config.observability.metrics.enabled else "pending",
            ),
            _diagnostic_row(
                "OTLP endpoint",
                "configured"
                if config.observability.tracing.enabled or config.observability.metrics.enabled
                else "not used",
            ),
        ],
    }


def _diagnostic_row(label: str, value: object, *, tone: str = "neutral") -> dict[str, str]:
    text = str(value or "unknown")
    return {"label": label, "value": text, "tone": tone}


def _embedding_label(*, config: HubConfig, health_state: dict[str, Any]) -> str:
    embedding = health_state.get("embedding")
    if isinstance(embedding, dict):
        provider = str(embedding.get("provider") or config.providers.embeddings)
        model = str(embedding.get("model") or "").strip()
        return f"{provider} / {model}" if model else provider
    embedding_health = health_state.get("embedding_health")
    if isinstance(embedding_health, dict):
        return str(embedding_health.get("provider") or config.providers.embeddings)
    return str(config.providers.embeddings)


def client_snippet_models(*, mcp_url: str) -> list[dict[str, str]]:
    snippets = []
    for client in CLIENT_MATRIX:
        snippets.append(
            {
                "name": client["name"],
                "status": client["status"],
                "element_id": "snippet-"
                + hashlib.sha256(client["name"].encode("utf-8")).hexdigest()[:12],
                "snippet": client["snippet"].format(mcp_url=mcp_url),
            }
        )
    return snippets


def mcp_url_for_config(config: HubConfig, *, request: Request | None = None) -> str:
    if config.api.oauth.resource:
        return config.api.oauth.resource.rstrip("/")
    if config.api.public_base_url:
        base = config.api.public_base_url.rstrip("/")
    elif request is not None:
        base = str(request.base_url).rstrip("/")
    else:
        base = f"http://{config.api.host}:{config.api.port}"
    return f"{base}/mcp"


def provider_callback_url(config: HubConfig, provider: str) -> str:
    provider_config = passport_provider_config(config, provider)
    if provider_config is None:
        raise HTTPException(status_code=404, detail="OAuth provider is not supported")
    return (
        provider_config.callback_url
        or f"{config.api.public_base_url.rstrip('/')}/auth/{provider}/callback"
    )


def normalize_passport_provider(provider: str) -> str:
    value = str(provider).strip().lower()
    if value not in {"google", "meta", "x"}:
        raise HTTPException(status_code=404, detail="OAuth provider is not supported")
    return value


def passport_provider_config(config: HubConfig, provider: str) -> Any | None:
    if provider not in config.api.connect.passport.providers:
        return None
    return getattr(config.api.connect.passport, provider, None)


def enabled_passport_provider_models(config: HubConfig) -> list[dict[str, str]]:
    providers = []
    for provider in config.api.connect.passport.providers:
        provider_config = passport_provider_config(config, provider)
        if provider_config is not None and provider_config.enabled:
            providers.append(
                {
                    "name": provider,
                    "label": provider_config.label or provider.title(),
                    "href": f"/auth/{provider}",
                }
            )
    return providers


def provider_status(config: HubConfig, provider: str) -> dict[str, object]:
    provider_config = passport_provider_config(config, provider)
    if provider_config is None:
        return {"enabled": False}
    return {
        "enabled": provider_config.enabled,
        "label": provider_config.label or provider.title(),
        "client_id_configured": bool(env_secret(provider_config.client_id_env)),
        "client_secret_configured": bool(env_secret(provider_config.client_secret_env)),
        "callback_url_configured": bool(provider_config.callback_url),
        "authorization_url_configured": bool(provider_config.authorization_url),
        "token_url_configured": bool(provider_config.token_url),
        "allowed_domains": list(provider_config.allowed_domains),
        "allowed_emails_configured": bool(provider_config.allowed_emails),
    }


def env_secret(env_name: str) -> str:
    return os.environ.get(env_name, "").strip()


def secure_cookie(config: HubConfig) -> bool:
    base = config.api.public_base_url
    return bool(base.startswith("https://") and "localhost" not in base and "127.0.0.1" not in base)


def csrf_matches(session: dict[str, object], csrf_token: str, *, config: HubConfig) -> bool:
    expected = str(session.get("csrf_token_hash") or "")
    actual = hash_secret(csrf_token, config, purpose="connect-csrf")
    return bool(csrf_token) and hmac.compare_digest(expected, actual)


def hash_secret(value: str, config: HubConfig, *, purpose: str) -> str:
    secret = session_secret(config)
    salt = f"ai-memory-hub:{purpose}:{secret}".encode("utf-8")
    digest = hashlib.pbkdf2_hmac(
        "sha256",
        value.encode("utf-8"),
        salt,
        SECRET_HASH_ITERATIONS,
    ).hex()
    return "pbkdf2-sha256:" + digest


def session_secret(config: HubConfig) -> str:
    return config.api.connect.session_secret or os.environ.get(config.api.connect.session_secret_env, "")


def validate_provider_claims(
    claims: dict[str, object], config: HubConfig, *, provider: str
) -> None:
    now = int(time.time())
    provider_config = passport_provider_config(config, provider)
    if provider_config is None:
        raise HTTPException(status_code=404, detail="OAuth provider is not supported")
    if provider == "google":
        issuer_values = {"https://accounts.google.com", "accounts.google.com"}
    else:
        issuer_values = {provider_config.issuer} if provider_config.issuer else set()
    if issuer_values and claims.get("iss") not in issuer_values:
        raise HTTPException(status_code=403, detail="Invalid OAuth issuer")
    if claims.get("aud") != env_secret(provider_config.client_id_env):
        raise HTTPException(status_code=403, detail="Invalid OAuth audience")
    exp = claims.get("exp")
    if not isinstance(exp, int) or exp <= now:
        raise HTTPException(status_code=403, detail="Expired OAuth identity token")
    subject = claims.get("sub")
    if not isinstance(subject, str) or not subject.strip():
        raise HTTPException(status_code=403, detail="OAuth identity token is missing subject")
    email = str(claims.get("email") or "").lower()
    hosted_domain = str(claims.get("hd") or "").lower()
    if provider_config.allowed_domains and hosted_domain not in set(provider_config.allowed_domains):
        raise HTTPException(status_code=403, detail="OAuth hosted domain is not allowed")
    if provider_config.allowed_emails and email not in set(provider_config.allowed_emails):
        raise HTTPException(status_code=403, detail="OAuth email is not allowed")


def issue_hub_token(*, config: HubConfig, owner_id: str) -> str:
    now = int(time.time())
    payload = {
        "sub": owner_id,
        "iss": config.api.public_base_url.rstrip("/"),
        "aud": mcp_url_for_config(config),
        "resource": mcp_url_for_config(config),
        "scope": f"{READ_SCOPE} {WRITE_SCOPE}",
        "iat": now,
        "exp": now + config.api.connect.token_ttl_seconds,
        "jti": "tok_" + secrets.token_hex(16),
    }
    try:
        from joserfc import jwt
        from joserfc.jwk import OctKey
    except ImportError as exc:
        raise HTTPException(
            status_code=503,
            detail="Hub token issuance requires installing the oauth optional extra",
        ) from exc
    key = OctKey.import_key(oauth_jwt_secret(config).encode("utf-8"))
    return jwt.encode({"alg": "HS256", "typ": "JWT"}, payload, key, algorithms=["HS256"])


def jwt_payload(token: str) -> dict[str, object]:
    parts = token.split(".")
    if len(parts) != 3:
        return {}
    try:
        payload = json.loads(_unb64(parts[1]))
    except (ValueError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def oauth_jwt_secret(config: HubConfig) -> str:
    return config.api.oauth.jwt_secret or os.environ.get(config.api.oauth.jwt_secret_env, "")


def utc_after(seconds: int) -> str:
    return (datetime.now(UTC) + timedelta(seconds=seconds)).replace(microsecond=0).isoformat()


def _csrf_token_from_cookie(request: Request, *, config: HubConfig) -> str | None:
    session_id = request.cookies.get(config.api.connect.session_cookie_name)
    return request.cookies.get("amh_csrf") if session_id else None


def _unb64(value: str) -> bytes:
    return base64.urlsafe_b64decode((value + "=" * (-len(value) % 4)).encode("ascii"))
