from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import time
from typing import Any
from urllib.parse import urlencode

from fastapi import HTTPException, Request
from starlette.responses import JSONResponse, RedirectResponse, Response

from memory.api.connect_oauth import ConnectOAuthRegistry
from memory.api.connect_service import ConnectService, issue_hub_token, jwt_payload, utc_after
from memory.auth import READ_SCOPE, WRITE_SCOPE
from memory.config import HubConfig

AUTHORIZATION_CODE_TTL_SECONDS = 300


def register_oauth_authorization_routes(
    app,
    *,
    service: ConnectService,
    oauth: ConnectOAuthRegistry,
    config: HubConfig,
) -> None:
    @app.post("/oauth/register", include_in_schema=False)
    async def oauth_register(request: Request) -> JSONResponse:
        payload = await request.json()
        redirect_uris = _redirect_uris_from_payload(payload)
        client_id = "amh_client_" + secrets.token_urlsafe(24)
        _registered_clients(request)[client_id] = {
            "client_id": client_id,
            "redirect_uris": redirect_uris,
            "client_name": str(payload.get("client_name") or "MCP client"),
            "created_at": int(time.time()),
        }
        return JSONResponse(
            {
                "client_id": client_id,
                "client_id_issued_at": int(time.time()),
                "redirect_uris": redirect_uris,
                "token_endpoint_auth_method": "none",
                "grant_types": ["authorization_code"],
                "response_types": ["code"],
            },
            status_code=201,
        )

    @app.get("/oauth/authorize", include_in_schema=False)
    async def oauth_authorize(request: Request) -> Response:
        params = dict(request.query_params)
        auth_request = _validated_authorization_request(request, params)
        session = await service.session_from_request(request)
        if session is None:
            _session(request)["pending_oauth_authorization"] = auth_request
            provider = _first_enabled_provider(config)
            return await oauth.authorize_redirect(
                request,
                provider=provider,
                state_data={"pending_oauth_authorization": auth_request},
            )
        return _authorization_code_redirect(request, auth_request, owner_id=str(session["user_id"]))

    @app.post("/oauth/token", include_in_schema=False)
    async def oauth_token(request: Request) -> JSONResponse:
        form = dict((await request.form()).items())
        if form.get("grant_type") != "authorization_code":
            raise _oauth_error("unsupported_grant_type", "Only authorization_code is supported")
        code = str(form.get("code") or "")
        record = _authorization_codes(request).pop(code, None)
        if record is None:
            raise _oauth_error("invalid_grant", "Authorization code is invalid or expired")
        expires_at = record.get("expires_at")
        if not isinstance(expires_at, int) or expires_at <= int(time.time()):
            raise _oauth_error("invalid_grant", "Authorization code is invalid or expired")
        if form.get("client_id") != record["client_id"]:
            raise _oauth_error("invalid_grant", "client_id does not match authorization code")
        if form.get("redirect_uri") != record["redirect_uri"]:
            raise _oauth_error("invalid_grant", "redirect_uri does not match authorization code")
        if not _pkce_verifier_matches(str(form.get("code_verifier") or ""), record):
            raise _oauth_error("invalid_grant", "PKCE verification failed")

        owner_id = str(record["owner_id"])
        issued_token = issue_hub_token(config=config, owner_id=owner_id)
        await service.agent.create_auth_token(
            owner_id=owner_id,
            token=issued_token,
            token_display_name="MCP OAuth client",
            expires_at=utc_after(config.api.connect.token_ttl_seconds),
            scopes=[READ_SCOPE, WRITE_SCOPE],
        )
        return JSONResponse(
            {
                "access_token": issued_token,
                "token_type": "Bearer",
                "expires_in": config.api.connect.token_ttl_seconds,
                "scope": f"{READ_SCOPE} {WRITE_SCOPE}",
                "resource": record["resource"],
                "jti": str(jwt_payload(issued_token).get("jti") or ""),
            }
        )


def pending_authorization_redirect(request: Request, *, owner_id: str) -> RedirectResponse | None:
    state_data = getattr(request.state, "oauth_provider_state_data", None)
    pending = (
        state_data.get("pending_oauth_authorization")
        if isinstance(state_data, dict)
        else None
    )
    if not isinstance(pending, dict):
        pending = _session(request).pop("pending_oauth_authorization", None)
    if not isinstance(pending, dict):
        return None
    return _authorization_code_redirect(request, pending, owner_id=owner_id)


def _validated_authorization_request(request: Request, params: dict[str, str]) -> dict[str, str]:
    if params.get("response_type") != "code":
        raise _oauth_error("unsupported_response_type", "Only response_type=code is supported")
    client_id = str(params.get("client_id") or "")
    client = _registered_clients(request).get(client_id)
    if client is None:
        raise _oauth_error("invalid_client", "Unknown OAuth client_id")
    redirect_uri = str(params.get("redirect_uri") or "")
    if redirect_uri not in client["redirect_uris"]:
        raise _oauth_error("invalid_request", "redirect_uri is not registered")
    if params.get("code_challenge_method") != "S256":
        raise _oauth_error("invalid_request", "PKCE S256 is required")
    code_challenge = str(params.get("code_challenge") or "")
    if not code_challenge:
        raise _oauth_error("invalid_request", "code_challenge is required")
    return {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
        "resource": str(params.get("resource") or ""),
        "scope": str(params.get("scope") or ""),
        "state": str(params.get("state") or ""),
    }


def _authorization_code_redirect(
    request: Request, auth_request: dict[str, str], *, owner_id: str
) -> RedirectResponse:
    code = "amh_code_" + secrets.token_urlsafe(32)
    _authorization_codes(request)[code] = {
        **auth_request,
        "code": code,
        "owner_id": owner_id,
        "expires_at": int(time.time()) + AUTHORIZATION_CODE_TTL_SECONDS,
    }
    query = {"code": code}
    if auth_request.get("state"):
        query["state"] = auth_request["state"]
    separator = "&" if "?" in auth_request["redirect_uri"] else "?"
    return RedirectResponse(
        f"{auth_request['redirect_uri']}{separator}{urlencode(query)}",
        status_code=303,
    )


def _redirect_uris_from_payload(payload: Any) -> list[str]:
    if not isinstance(payload, dict):
        raise _oauth_error("invalid_client_metadata", "Request body must be a JSON object")
    values = payload.get("redirect_uris")
    if not isinstance(values, list) or not values:
        raise _oauth_error("invalid_client_metadata", "redirect_uris is required")
    redirect_uris = [str(value).strip() for value in values if str(value).strip()]
    if not redirect_uris or any("\r" in value or "\n" in value for value in redirect_uris):
        raise _oauth_error("invalid_client_metadata", "redirect_uris contains invalid values")
    return redirect_uris


def _pkce_verifier_matches(verifier: str, record: dict[str, object]) -> bool:
    if not verifier:
        return False
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")
    expected = str(record["code_challenge"])
    return hmac.compare_digest(challenge, expected)


def _registered_clients(request: Request) -> dict[str, dict[str, Any]]:
    if not hasattr(request.app.state, "oauth_registered_clients"):
        request.app.state.oauth_registered_clients = {}
    return request.app.state.oauth_registered_clients


def _authorization_codes(request: Request) -> dict[str, dict[str, object]]:
    if not hasattr(request.app.state, "oauth_authorization_codes"):
        request.app.state.oauth_authorization_codes = {}
    return request.app.state.oauth_authorization_codes


def _session(request: Request) -> dict[str, Any]:
    try:
        return request.session
    except AssertionError as exc:
        raise HTTPException(status_code=503, detail="OAuth session middleware is not configured") from exc


def _first_enabled_provider(config: HubConfig) -> str:
    for provider in config.api.connect.passport.providers:
        provider_config = getattr(config.api.connect.passport, provider)
        if provider_config.enabled:
            return provider
    raise HTTPException(status_code=503, detail="No OAuth identity provider is enabled")


def _oauth_error(error: str, description: str) -> HTTPException:
    return HTTPException(
        status_code=400,
        detail={"error": error, "error_description": description},
    )
