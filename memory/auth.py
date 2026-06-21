from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Awaitable, Callable

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse, Response

from memory.config import HubConfig
from memory.ingestion.base_agent import BaseIngestionAgent

PUBLIC_PATHS = {"/health", "/ready", "/.well-known/oauth-protected-resource"}
READ_SCOPE = "memory:read"
WRITE_SCOPE = "memory:write"
_CURRENT_OWNER_ID: ContextVar[str | None] = ContextVar("amh_owner_id", default=None)


@dataclass(frozen=True)
class AuthContext:
    owner_id: str | None
    token_id: str | None = None
    scopes: frozenset[str] = frozenset()
    auth_mode: str = "none"


def install_auth_middleware(app, *, config: HubConfig, agent: BaseIngestionAgent) -> None:
    if config.api.auth == "none":
        return
    app.add_middleware(AuthMiddleware, config=config, agent=agent)


def extract_bearer_token(request: Request) -> str | None:
    value = request.headers.get("authorization")
    if not value:
        return None
    scheme, _, token = value.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        return None
    return token.strip()


def build_www_authenticate_challenge(
    request: Request,
    config: HubConfig,
    *,
    error: str | None = None,
    scopes: set[str] | None = None,
) -> str:
    challenge = 'Bearer realm="ai-memory-hub"'
    if error:
        challenge += f', error="{error}"'
    if scopes:
        scope_value = " ".join(sorted(scopes))
        challenge += f', scope="{scope_value}"'
    if config.api.auth == "oauth_resource_server":
        challenge += f', resource_metadata="{_resource_metadata_url(config, request.url.path)}"'
    return challenge


def current_owner_id() -> str | None:
    return _CURRENT_OWNER_ID.get()


class AuthMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, *, config: HubConfig, agent: BaseIngestionAgent):
        super().__init__(app)
        self._config = config
        self._agent = agent

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        if _is_public_path(request.url.path):
            return await call_next(request)
        if _has_query_access_token(request):
            return _unauthorized(request, self._config, "invalid_request")
        token = extract_bearer_token(request)
        if token is None:
            return _unauthorized(request, self._config, "invalid_request")
        required_scopes = required_scopes_for_request(request)
        auth = await self._authenticate(request, token)
        if auth is None:
            return _unauthorized(request, self._config, "invalid_token")
        if not _has_required_scopes(auth.scopes, required_scopes):
            return _forbidden(request, self._config, required_scopes)
        request.state.auth = auth
        token_handle = _CURRENT_OWNER_ID.set(auth.owner_id)
        try:
            return await call_next(request)
        finally:
            _CURRENT_OWNER_ID.reset(token_handle)

    async def _authenticate(self, request: Request, token: str) -> AuthContext | None:
        if self._config.api.auth == "bearer_token":
            context = await self._agent.authenticate_bearer_token_context(token)
            if context is None:
                return None
            scopes = context.get("scopes", [])
            if not isinstance(scopes, list | tuple | set | frozenset):
                scopes = []
            return AuthContext(
                owner_id=str(context["owner_id"]),
                token_id=str(context["token_id"]) if context.get("token_id") is not None else None,
                scopes=frozenset(str(scope) for scope in scopes),
                auth_mode="bearer_token",
            )
        claims = validate_oauth_access_token(token, self._config)
        if claims is None:
            return None
        return AuthContext(
            owner_id=claims.owner_id,
            token_id=claims.token_id,
            scopes=frozenset(claims.scopes),
            auth_mode="oauth_resource_server",
        )


@dataclass(frozen=True)
class AccessTokenClaims:
    owner_id: str
    token_id: str | None
    scopes: frozenset[str]


def required_scopes_for_request(request: Request) -> set[str]:
    path = request.url.path
    if path == "/mcp" or path.startswith("/mcp/"):
        return {READ_SCOPE}
    if path in {"/memory/insert", "/memory/facts/supersede"}:
        return {WRITE_SCOPE}
    if path.startswith("/memory/"):
        return {READ_SCOPE}
    return set()


def validate_oauth_access_token(token: str, config: HubConfig) -> AccessTokenClaims | None:
    claims = _decode_hs256_jwt(token, _oauth_jwt_secret(config))
    if claims is None:
        return None
    if not _validate_time_claims(claims):
        return None
    if config.api.oauth.issuer and claims.get("iss") != config.api.oauth.issuer:
        return None
    if not _claim_matches_resource(claims, _oauth_resource(config)):
        return None
    owner_id = claims.get("sub")
    if not isinstance(owner_id, str) or not owner_id.strip():
        return None
    return AccessTokenClaims(
        owner_id=owner_id.strip(),
        token_id=str(claims["jti"]) if claims.get("jti") is not None else None,
        scopes=frozenset(_scopes_from_claims(claims)),
    )


def protected_resource_metadata(config: HubConfig, *, resource_path: str = "/mcp") -> dict[str, object]:
    return {
        "resource": _oauth_resource(config, resource_path=resource_path),
        "authorization_servers": list(config.api.oauth.authorization_servers),
        "scopes_supported": list(config.api.oauth.scopes_supported),
        "bearer_methods_supported": ["header"],
        "resource_documentation": f"{config.api.public_base_url.rstrip('/')}/docs",
    }


def _is_public_path(path: str) -> bool:
    return path in PUBLIC_PATHS or path.startswith("/.well-known/oauth-protected-resource/")


def _unauthorized(request: Request, config: HubConfig, error: str) -> JSONResponse:
    return JSONResponse(
        {"detail": "Unauthorized"},
        status_code=401,
        headers={
            "WWW-Authenticate": build_www_authenticate_challenge(
                request, config, error=error, scopes=required_scopes_for_request(request)
            )
        },
    )


def _forbidden(request: Request, config: HubConfig, required_scopes: set[str]) -> JSONResponse:
    return JSONResponse(
        {"detail": "Forbidden"},
        status_code=403,
        headers={
            "WWW-Authenticate": build_www_authenticate_challenge(
                request, config, error="insufficient_scope", scopes=required_scopes
            )
        },
    )


def _has_query_access_token(request: Request) -> bool:
    params = request.query_params
    return "access_token" in params or "token" in params


def _has_required_scopes(actual: frozenset[str], required: set[str]) -> bool:
    return not required or required.issubset(actual)


def _resource_metadata_url(config: HubConfig, path: str) -> str:
    base = config.api.public_base_url.rstrip("/")
    suffix = "/mcp" if path == "/mcp" or path.startswith("/mcp/") else ""
    return f"{base}/.well-known/oauth-protected-resource{suffix}"


def _oauth_resource(config: HubConfig, *, resource_path: str = "/mcp") -> str:
    if config.api.oauth.resource:
        return config.api.oauth.resource
    return f"{config.api.public_base_url.rstrip('/')}{resource_path}"


def _oauth_jwt_secret(config: HubConfig) -> str:
    if config.api.oauth.jwt_secret:
        return config.api.oauth.jwt_secret
    return os.environ.get(config.api.oauth.jwt_secret_env, "")


def _decode_hs256_jwt(token: str, secret: str) -> dict[str, object] | None:
    if not secret:
        return None
    parts = token.split(".")
    if len(parts) != 3:
        return None
    signing_input = f"{parts[0]}.{parts[1]}".encode("ascii")
    expected = hmac.new(secret.encode("utf-8"), signing_input, hashlib.sha256).digest()
    actual = _base64url_decode(parts[2])
    if actual is None or not hmac.compare_digest(expected, actual):
        return None
    header_raw = _base64url_decode(parts[0])
    payload_raw = _base64url_decode(parts[1])
    if header_raw is None or payload_raw is None:
        return None
    try:
        header = json.loads(header_raw)
        payload = json.loads(payload_raw)
    except json.JSONDecodeError:
        return None
    if not isinstance(header, dict) or header.get("alg") != "HS256":
        return None
    return payload if isinstance(payload, dict) else None


def _base64url_decode(value: str) -> bytes | None:
    try:
        padded = value + "=" * (-len(value) % 4)
        return base64.urlsafe_b64decode(padded.encode("ascii"))
    except (ValueError, UnicodeEncodeError):
        return None


def _validate_time_claims(claims: dict[str, object]) -> bool:
    now = int(time.time())
    exp = claims.get("exp")
    if not isinstance(exp, int) or exp <= now:
        return False
    nbf = claims.get("nbf")
    if isinstance(nbf, int) and nbf > now:
        return False
    return True


def _claim_matches_resource(claims: dict[str, object], resource: str) -> bool:
    for key in ("aud", "resource"):
        value = claims.get(key)
        if isinstance(value, str) and value == resource:
            return True
        if isinstance(value, list) and resource in {str(item) for item in value}:
            return True
    return False


def _scopes_from_claims(claims: dict[str, object]) -> set[str]:
    scope = claims.get("scope")
    if isinstance(scope, str):
        return {item for item in scope.split() if item}
    scopes = claims.get("scopes")
    if isinstance(scopes, list):
        return {str(item) for item in scopes if str(item).strip()}
    return set()
