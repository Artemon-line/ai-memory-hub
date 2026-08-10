from __future__ import annotations

import base64
import json
import time
from typing import Any, cast
from urllib.parse import urlencode, urlparse

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
OIDC_CACHE_TTL_SECONDS = 300
OIDC_CLOCK_SKEW_SECONDS = 60


class ConnectOAuthRegistry:
    def __init__(self, config: HubConfig) -> None:
        self.config = config
        self._oidc_metadata_cache: dict[str, tuple[int, dict[str, object]]] = {}
        self._jwks_cache: dict[str, tuple[int, dict[str, object]]] = {}

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
        states = _provider_states(request)
        _prune_provider_states(states)
        if len(states) >= OAUTH_STATE_MAX_RECORDS:
            _drop_oldest_provider_state(states)
        states[state] = provider_state
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
                _validate_oidc_nonce(
                    claims,
                    provider_config=passport_provider_config(self.config, provider_name),
                    nonce=str(state_data.get("nonce") or ""),
                )
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
        claims = await self._claims_from_token(
            token,
            provider=provider_name,
            nonce=str(state_data.get("nonce") or ""),
        )
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

    async def _claims_from_token(
        self, token: Any, *, provider: str, nonce: str
    ) -> dict[str, object] | None:
        provider_config = passport_provider_config(self.config, provider)
        if isinstance(token, dict):
            userinfo = token.get("userinfo")
            if isinstance(userinfo, dict):
                return dict(userinfo)
            id_token = token.get("id_token")
            if isinstance(id_token, dict):
                claims = dict(id_token)
                _validate_oidc_nonce(claims, provider_config=provider_config, nonce=nonce)
                return claims
            if isinstance(id_token, str):
                claims = await self._verified_id_token_claims(
                    provider=provider,
                    provider_config=provider_config,
                    id_token=id_token,
                )
                _validate_oidc_nonce(claims, provider_config=provider_config, nonce=nonce)
                return claims
            if "sub" in token:
                claims = dict(token)
                _validate_oidc_nonce(claims, provider_config=provider_config, nonce=nonce)
                return claims
        return None

    async def _verified_id_token_claims(
        self, *, provider: str, provider_config: Any | None, id_token: str
    ) -> dict[str, object]:
        if provider_config is None:
            raise HTTPException(status_code=404, detail="OAuth provider is not supported")
        if not _provider_uses_oidc(provider_config):
            claims = _jwt_payload(id_token)
            if claims is None:
                raise HTTPException(status_code=403, detail="Invalid OAuth identity token")
            return claims
        try:
            from joserfc import jwt
            from joserfc.jwk import KeySet
        except ImportError as exc:
            raise HTTPException(
                status_code=503,
                detail="OAuth support requires installing the oauth optional extra",
            ) from exc

        jwks = await self._jwks_for_provider(provider, provider_config)
        algorithms = list(provider_config.oidc_algorithms)
        try:
            key_set = KeySet.import_key_set(cast(Any, jwks))
            token = jwt.decode(id_token, key_set, algorithms=algorithms)
        except Exception as exc:
            raise HTTPException(status_code=403, detail="Invalid OAuth identity token") from exc
        claims = token.claims
        if not isinstance(claims, dict):
            raise HTTPException(status_code=403, detail="Invalid OAuth identity token")
        _validate_oidc_time_claims(claims)
        return dict(claims)

    async def _jwks_for_provider(self, provider: str, provider_config: Any) -> dict[str, object]:
        jwks_url = provider_config.jwks_url
        if not jwks_url:
            metadata = await self._metadata_for_provider(provider, provider_config)
            metadata_issuer = metadata.get("issuer")
            if provider_config.issuer and metadata_issuer != provider_config.issuer:
                raise HTTPException(status_code=502, detail="OAuth provider metadata was invalid")
            jwks_uri = metadata.get("jwks_uri")
            if not isinstance(jwks_uri, str) or not jwks_uri.strip():
                raise HTTPException(status_code=502, detail="OAuth provider metadata was invalid")
            jwks_url = jwks_uri.strip()
        if not _safe_oidc_url(jwks_url):
            raise HTTPException(status_code=503, detail="OAuth JWKS URL is not allowed")
        cached = self._jwks_cache.get(jwks_url)
        now = int(time.time())
        if cached is not None and cached[0] > now:
            return cached[1]
        payload = await _fetch_json(jwks_url)
        if not isinstance(payload.get("keys"), list):
            raise HTTPException(status_code=502, detail="OAuth JWKS response was invalid")
        self._jwks_cache[jwks_url] = (now + OIDC_CACHE_TTL_SECONDS, payload)
        return payload

    async def _metadata_for_provider(self, provider: str, provider_config: Any) -> dict[str, object]:
        discovery_url = provider_config.discovery_url
        if not discovery_url:
            raise HTTPException(status_code=503, detail="OAuth discovery URL is not configured")
        if not _safe_oidc_url(discovery_url):
            raise HTTPException(status_code=503, detail="OAuth discovery URL is not allowed")
        cached = self._oidc_metadata_cache.get(provider)
        now = int(time.time())
        if cached is not None and cached[0] > now:
            return cached[1]
        payload = await _fetch_json(discovery_url)
        self._oidc_metadata_cache[provider] = (now + OIDC_CACHE_TTL_SECONDS, payload)
        return payload


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


def _provider_uses_oidc(provider_config: Any) -> bool:
    scopes = {str(scope).lower() for scope in getattr(provider_config, "scopes", [])}
    return bool(
        "openid" in scopes
        and getattr(provider_config, "issuer", "")
        and (getattr(provider_config, "discovery_url", "") or getattr(provider_config, "jwks_url", ""))
    )


def _validate_oidc_nonce(
    claims: dict[str, object], *, provider_config: Any | None, nonce: str
) -> None:
    if provider_config is None or not _provider_uses_oidc(provider_config):
        return
    if not nonce or claims.get("nonce") != nonce:
        raise HTTPException(status_code=403, detail="Invalid OAuth identity token")


def _validate_oidc_time_claims(claims: dict[str, object]) -> None:
    now = int(time.time())
    exp = claims.get("exp")
    if not isinstance(exp, int) or exp <= now - OIDC_CLOCK_SKEW_SECONDS:
        raise HTTPException(status_code=403, detail="Invalid OAuth identity token")
    iat = claims.get("iat")
    if not isinstance(iat, int) or iat > now + OIDC_CLOCK_SKEW_SECONDS:
        raise HTTPException(status_code=403, detail="Invalid OAuth identity token")
    subject = claims.get("sub")
    if not isinstance(subject, str) or not subject.strip():
        raise HTTPException(status_code=403, detail="Invalid OAuth identity token")


async def _fetch_json(url: str) -> dict[str, object]:
    try:
        import httpx
    except ImportError as exc:
        raise HTTPException(
            status_code=503,
            detail="OAuth support requires installing the oauth optional extra",
        ) from exc
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(url, headers={"Accept": "application/json"})
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail="OAuth provider metadata request failed") from exc
    if response.status_code >= 400:
        raise HTTPException(status_code=502, detail="OAuth provider metadata request failed")
    payload = response.json()
    if not isinstance(payload, dict):
        raise HTTPException(status_code=502, detail="OAuth provider metadata response was invalid")
    return payload


def _safe_oidc_url(url: str) -> bool:
    parsed = urlparse(url)
    if parsed.scheme == "https":
        return True
    if parsed.scheme != "http":
        return False
    return parsed.hostname in {"127.0.0.1", "localhost", "::1"}


def _nonce() -> str:
    try:
        import secrets

        return secrets.token_urlsafe(24)
    except Exception:
        return str(int(time.time() * 1000))
