from __future__ import annotations

import base64
import json
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
        client_id = env_secret(provider_config.client_id_env)
        if not client_id:
            raise HTTPException(status_code=503, detail="OAuth client id is not configured")
        state = _nonce()
        nonce = _nonce()
        provider_state: dict[str, object] = {
            "provider": provider_name,
            "nonce": nonce,
            "created_at": int(time.time()),
        }
        if state_data:
            provider_state.update(state_data)
        _provider_states(request)[state] = provider_state
        query = urlencode(
            {
                "response_type": "code",
                "client_id": client_id,
                "redirect_uri": provider_callback_url(self.config, provider_name),
                "scope": " ".join(provider_config.scopes),
                "state": state,
                "nonce": nonce,
                "access_type": "offline",
                "prompt": "select_account",
            }
        )
        return RedirectResponse(f"{provider_config.authorization_url}?{query}", status_code=303)

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

        state_data = await self._state_data(request, provider_name)
        code = request.query_params.get("code")
        if not code:
            raise HTTPException(status_code=400, detail="Missing OAuth code")
        token = await self._exchange_code_for_token(
            provider_name,
            code=str(code),
            nonce=str(state_data.get("nonce") or ""),
        )
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
        data = _provider_states(request).pop(str(state), None)
        if not isinstance(data, dict) or data.get("provider") != provider:
            raise HTTPException(status_code=400, detail="Invalid OAuth state")
        created_at = data.get("created_at")
        if not isinstance(created_at, int) or created_at + 600 <= int(time.time()):
            raise HTTPException(status_code=400, detail="Invalid OAuth state")
        request.state.oauth_provider_state_data = data
        return data

    async def _exchange_code_for_token(
        self, provider: str, *, code: str, nonce: str
    ) -> dict[str, object]:
        provider_config = passport_provider_config(self.config, provider)
        if provider_config is None:
            raise HTTPException(status_code=404, detail="OAuth provider is not supported")
        client_id = env_secret(provider_config.client_id_env)
        client_secret = env_secret(provider_config.client_secret_env)
        if not client_id:
            raise HTTPException(status_code=503, detail="OAuth client id is not configured")
        if not provider_config.token_url:
            raise HTTPException(status_code=503, detail="OAuth token URL is not configured")
        try:
            import httpx
        except ImportError as exc:
            raise HTTPException(
                status_code=503,
                detail="OAuth support requires installing the oauth optional extra",
            ) from exc
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.post(
                    provider_config.token_url,
                    data={
                        "grant_type": "authorization_code",
                        "code": code,
                        "redirect_uri": provider_callback_url(self.config, provider),
                        "client_id": client_id,
                        "client_secret": client_secret,
                    },
                    headers={"Accept": "application/json"},
                )
        except httpx.HTTPError as exc:
            raise HTTPException(status_code=502, detail="OAuth token exchange failed") from exc
        if response.status_code >= 400:
            raise HTTPException(status_code=400, detail="OAuth token exchange was rejected")
        payload = response.json()
        if isinstance(payload, dict):
            payload["nonce"] = nonce
            return payload
        raise HTTPException(status_code=502, detail="OAuth token response was invalid")

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
        if isinstance(id_token, str):
            claims = _jwt_payload(id_token)
            if claims is not None:
                return claims
        if "sub" in token:
            return dict(token)
    return None


def _provider_states(request: Request) -> dict[str, dict[str, object]]:
    if not hasattr(request.app.state, "oauth_provider_states"):
        request.app.state.oauth_provider_states = {}
    return request.app.state.oauth_provider_states


def _jwt_payload(token: str) -> dict[str, object] | None:
    parts = token.split(".")
    if len(parts) != 3:
        return None
    try:
        raw = base64.urlsafe_b64decode((parts[1] + "=" * (-len(parts[1]) % 4)).encode("ascii"))
        payload = json.loads(raw)
    except (ValueError, json.JSONDecodeError, UnicodeEncodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _nonce() -> str:
    try:
        import secrets

        return secrets.token_urlsafe(24)
    except Exception:
        return str(int(time.time() * 1000))
