from __future__ import annotations

from contextvars import ContextVar
from dataclasses import dataclass
from typing import Awaitable, Callable

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse, Response

from memory.config import HubConfig
from memory.ingestion.base_agent import BaseIngestionAgent

PUBLIC_PATHS = {"/health", "/ready"}
_CURRENT_OWNER_ID: ContextVar[str | None] = ContextVar("amh_owner_id", default=None)


@dataclass(frozen=True)
class AuthContext:
    owner_id: str | None


def install_auth_middleware(app, *, config: HubConfig, agent: BaseIngestionAgent) -> None:
    if config.api.auth == "none":
        return
    if config.api.auth == "oauth_resource_server":
        raise ValueError("api.auth=oauth_resource_server is not implemented yet")
    app.add_middleware(BearerAuthMiddleware, agent=agent)


def extract_bearer_token(request: Request) -> str | None:
    value = request.headers.get("authorization")
    if not value:
        return None
    scheme, _, token = value.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        return None
    return token.strip()


def build_www_authenticate_challenge(error: str | None = None) -> str:
    challenge = 'Bearer realm="ai-memory-hub"'
    if error:
        challenge += f', error="{error}"'
    return challenge


def current_owner_id() -> str | None:
    return _CURRENT_OWNER_ID.get()


class BearerAuthMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, *, agent: BaseIngestionAgent):
        super().__init__(app)
        self._agent = agent

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        if _is_public_path(request.url.path):
            return await call_next(request)
        token = extract_bearer_token(request)
        if token is None:
            return _unauthorized("invalid_request")
        owner_id = await self._agent.authenticate_bearer_token(token)
        if owner_id is None:
            return _unauthorized("invalid_token")
        request.state.auth = AuthContext(owner_id=owner_id)
        token_handle = _CURRENT_OWNER_ID.set(owner_id)
        try:
            return await call_next(request)
        finally:
            _CURRENT_OWNER_ID.reset(token_handle)


def _is_public_path(path: str) -> bool:
    return path in PUBLIC_PATHS


def _unauthorized(error: str) -> JSONResponse:
    return JSONResponse(
        {"detail": "Unauthorized"},
        status_code=401,
        headers={"WWW-Authenticate": build_www_authenticate_challenge(error)},
    )
