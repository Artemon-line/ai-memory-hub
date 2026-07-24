from __future__ import annotations

import time
from typing import Any

from fastapi import HTTPException, Request
from starlette.responses import Response

from memory.api.connect_service import (
    env_secret,
    normalize_passport_provider,
    passport_provider_config,
    provider_callback_url,
)
from memory.config import HubConfig


class ConnectOAuthRegistry:
    def __init__(self, config: HubConfig) -> None:
        self.config = config

    async def authorize_redirect(self, request: Request, *, provider: str) -> Response:
        provider_name = normalize_passport_provider(provider)
        client = self._client(provider_name)
        provider_config = passport_provider_config(self.config, provider_name)
        if provider_config is None:
            raise HTTPException(status_code=404, detail="OAuth provider is not supported")
        redirect = await client.authorize_redirect(
            request,
            provider_callback_url(self.config, provider_name),
            nonce=_nonce(),
            access_type="offline",
            prompt="select_account",
        )
        redirect.status_code = 303
        return redirect

    async def callback_claims(self, request: Request, *, provider: str) -> dict[str, object]:
        provider_name = normalize_passport_provider(provider)
        override = getattr(request.app.state, f"{provider_name}_oauth_exchange", None)
        if override is not None:
            state_data = await self._state_data(request, provider_name)
            code = request.query_params.get("code")
            if not code:
                raise HTTPException(status_code=400, detail="Missing OAuth code")
            claims = await override(
                code=code,
                nonce=str(state_data.get("nonce") or ""),
                config=self.config,
                provider=provider_name,
            )
            if isinstance(claims, dict):
                return claims
            raise HTTPException(status_code=502, detail="OAuth test exchange returned invalid claims")

        client = self._client(provider_name)
        try:
            token = await client.authorize_access_token(request)
        except Exception as exc:
            raise HTTPException(status_code=400, detail="Invalid OAuth state") from exc
        claims = _claims_from_token(token)
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
        client = self._client(provider)
        data = await client.framework.get_state_data(request.session, state)
        if not data:
            raise HTTPException(status_code=400, detail="Invalid OAuth state")
        await client.framework.clear_state_data(request.session, state)
        return data

    def _client(self, provider: str) -> Any:
        provider_name = normalize_passport_provider(provider)
        provider_config = passport_provider_config(self.config, provider_name)
        if provider_config is None or not provider_config.enabled:
            raise HTTPException(status_code=404, detail="OAuth provider is not enabled")
        client_id = env_secret(provider_config.client_id_env)
        client_secret = env_secret(provider_config.client_secret_env)
        if not client_id:
            raise HTTPException(status_code=503, detail="OAuth client id is not configured")
        if not provider_config.authorization_url:
            raise HTTPException(status_code=503, detail="OAuth authorization URL is not configured")
        if not provider_config.token_url:
            raise HTTPException(status_code=503, detail="OAuth token URL is not configured")
        try:
            from authlib.integrations.starlette_client import OAuth
        except ImportError as exc:
            raise HTTPException(
                status_code=503,
                detail="OAuth support requires installing the oauth optional extra",
            ) from exc
        oauth = OAuth()
        oauth.register(
            name=provider_name,
            client_id=client_id,
            client_secret=client_secret,
            authorize_url=provider_config.authorization_url,
            access_token_url=provider_config.token_url,
            client_kwargs={"scope": " ".join(provider_config.scopes)},
        )
        client = oauth.create_client(provider_name)
        if client is None:
            raise HTTPException(status_code=503, detail="OAuth provider could not be initialized")
        return client


def _claims_from_token(token: Any) -> dict[str, object] | None:
    if isinstance(token, dict):
        userinfo = token.get("userinfo")
        if isinstance(userinfo, dict):
            return dict(userinfo)
        id_token = token.get("id_token")
        if isinstance(id_token, dict):
            return dict(id_token)
        if "sub" in token:
            return dict(token)
    return None


def _nonce() -> str:
    try:
        import secrets

        return secrets.token_urlsafe(24)
    except Exception:
        return str(int(time.time() * 1000))
