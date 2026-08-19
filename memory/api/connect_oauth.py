from __future__ import annotations

import inspect
import time
from typing import Any
from urllib.parse import urlencode

from fastapi import HTTPException, Request
from starlette.responses import RedirectResponse, Response

from memory.api.connect_service import (
    env_secret,
    normalize_passport_provider,
    passport_provider_config,
    provider_callback_url,
)
from memory.config import HubConfig

OAUTH_STATE_TTL_SECONDS = 600
OAUTH_STATE_MAX_RECORDS = 128


class ConnectOAuthRegistry:
    def __init__(self, config: HubConfig) -> None:
        self.config = config

    async def authorize_redirect(
        self,
        request: Request,
        *,
        provider: str,
        state_data: dict[str, object] | None = None,
    ) -> Response:
        provider_name = normalize_passport_provider(provider)
        provider_config = passport_provider_config(self.config, provider_name)
        if provider_config is None:
            raise HTTPException(status_code=404, detail="OAuth provider is not supported")
        state = _nonce()
        nonce = _nonce()
        provider_state: dict[str, object] = {
            "provider": provider_name,
            "nonce": nonce,
            "created_at": int(time.time()),
        }
        if state_data:
            provider_state.update(state_data)
        states = _provider_states(request)
        _prune_provider_states(states)
        if len(states) >= OAUTH_STATE_MAX_RECORDS:
            _drop_oldest_provider_state(states)
        states[state] = provider_state
        override = getattr(request.app.state, f"{provider_name}_oauth_exchange", None)
        if override is not None:
            return _test_authorize_redirect(
                self.config,
                provider_name,
                state=state,
                nonce=nonce,
            )
        client = _authlib_client(self.config, provider_name)
        response = await client.authorize_redirect(
            request,
            provider_callback_url(self.config, provider_name),
            state=state,
            nonce=nonce,
            prompt="select_account",
            access_type="offline",
        )
        response.status_code = 303
        return response

    async def callback_claims(self, request: Request, *, provider: str) -> dict[str, object]:
        provider_name = normalize_passport_provider(provider)
        override = getattr(request.app.state, f"{provider_name}_oauth_exchange", None)
        state_data = await self._state_data(request, provider_name)
        code = request.query_params.get("code")
        if not code:
            raise HTTPException(status_code=400, detail="Missing OAuth code")
        if override is not None:
            claims = await override(
                code=code,
                nonce=str(state_data.get("nonce") or ""),
                config=self.config,
                provider=provider_name,
            )
            if isinstance(claims, dict):
                return claims
            raise HTTPException(status_code=502, detail="OAuth test exchange returned invalid claims")
        try:
            client = _authlib_client(self.config, provider_name)
            token = await client.authorize_access_token(request)
        except Exception as exc:
            raise HTTPException(status_code=400, detail="OAuth token exchange was rejected") from exc
        claims = await _claims_from_authlib_token(client, request, token)
        if claims is None:
            raise HTTPException(
                status_code=502,
                detail="OAuth provider response did not include a supported identity payload",
            )
        return claims

    async def _state_data(self, request: Request, provider: str) -> dict[str, Any]:
        state = request.query_params.get("state")
        if not state:
            raise HTTPException(status_code=400, detail="Invalid OAuth state")
        data = _provider_states(request).pop(str(state), None)
        if not isinstance(data, dict) or data.get("provider") != provider:
            raise HTTPException(status_code=400, detail="Invalid OAuth state")
        created_at = data.get("created_at")
        if not isinstance(created_at, int) or created_at + OAUTH_STATE_TTL_SECONDS <= int(
            time.time()
        ):
            raise HTTPException(status_code=400, detail="Invalid OAuth state")
        request.state.oauth_provider_state_data = data
        return data


def _authlib_client(config: HubConfig, provider: str) -> Any:
    provider_config = passport_provider_config(config, provider)
    if provider_config is None:
        raise HTTPException(status_code=404, detail="OAuth provider is not supported")
    client_id = env_secret(provider_config.client_id_env)
    client_secret = env_secret(provider_config.client_secret_env)
    if not client_id:
        raise HTTPException(status_code=503, detail="OAuth client id is not configured")
    if not client_secret:
        raise HTTPException(status_code=503, detail="OAuth client secret is not configured")
    try:
        from authlib.integrations.starlette_client import OAuth
    except ImportError as exc:
        raise HTTPException(
            status_code=503,
            detail="OAuth support requires installing the oauth optional extra",
        ) from exc
    oauth = OAuth()
    client_kwargs = {"scope": " ".join(provider_config.scopes)}
    if provider_config.discovery_url:
        oauth.register(
            name=provider,
            client_id=client_id,
            client_secret=client_secret,
            server_metadata_url=provider_config.discovery_url,
            client_kwargs=client_kwargs,
        )
    else:
        oauth.register(
            name=provider,
            client_id=client_id,
            client_secret=client_secret,
            authorize_url=provider_config.authorization_url,
            access_token_url=provider_config.token_url,
            client_kwargs=client_kwargs,
        )
    client = oauth.create_client(provider)
    if client is None:
        raise HTTPException(status_code=503, detail="OAuth provider is not configured")
    return client


def _test_authorize_redirect(
    config: HubConfig, provider: str, *, state: str, nonce: str
) -> RedirectResponse:
    provider_config = passport_provider_config(config, provider)
    if provider_config is None:
        raise HTTPException(status_code=404, detail="OAuth provider is not supported")
    client_id = env_secret(provider_config.client_id_env)
    if not client_id:
        raise HTTPException(status_code=503, detail="OAuth client id is not configured")
    query = urlencode(
        {
            "response_type": "code",
            "client_id": client_id,
            "redirect_uri": provider_callback_url(config, provider),
            "scope": " ".join(provider_config.scopes),
            "state": state,
            "nonce": nonce,
        }
    )
    return RedirectResponse(f"{provider_config.authorization_url}?{query}", status_code=303)


async def _claims_from_authlib_token(
    client: Any, request: Request, token: Any
) -> dict[str, object] | None:
    if not isinstance(token, dict):
        return None
    userinfo = token.get("userinfo")
    if isinstance(userinfo, dict):
        return dict(userinfo)
    if token.get("id_token"):
        parsed_claims = client.parse_id_token(request, token)
        claims = await parsed_claims if inspect.isawaitable(parsed_claims) else parsed_claims
        if isinstance(claims, dict):
            return dict(claims)
    if isinstance(token.get("sub"), str):
        return dict(token)
    return None


def _provider_states(request: Request) -> dict[str, dict[str, object]]:
    if not hasattr(request.app.state, "oauth_provider_states"):
        request.app.state.oauth_provider_states = {}
    return request.app.state.oauth_provider_states


def _prune_provider_states(states: dict[str, dict[str, object]]) -> None:
    now = int(time.time())
    expired = []
    for state, data in states.items():
        created_at = data.get("created_at")
        if not isinstance(created_at, int) or created_at + OAUTH_STATE_TTL_SECONDS <= now:
            expired.append(state)
    for state in expired:
        states.pop(state, None)


def _drop_oldest_provider_state(states: dict[str, dict[str, object]]) -> None:
    def created_at_for(state: str) -> int:
        created_at = states[state].get("created_at")
        return created_at if isinstance(created_at, int) else 0

    oldest_state = min(states, key=created_at_for)
    states.pop(oldest_state, None)


def _nonce() -> str:
    try:
        import secrets

        return secrets.token_urlsafe(24)
    except Exception:
        return str(int(time.time() * 1000))
