from __future__ import annotations

import base64
import hashlib
import hmac
import ipaddress
import logging
import re
import secrets
import time
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlencode, urlparse

from fastapi import HTTPException, Request
from starlette.responses import JSONResponse, RedirectResponse, Response

from memory.api.connect_oauth import ConnectOAuthRegistry
from memory.api.connect_service import (
    ConnectService,
    csrf_matches,
    issue_hub_token,
    jwt_payload,
    utc_after,
)
from memory.auth import READ_SCOPE, WRITE_SCOPE
from memory.backend.errors import OAuthClientQuotaExceeded
from memory.config import HubConfig

AUTHORIZATION_CODE_TTL_SECONDS = 300
OAUTH_CLIENT_MAX_RECORDS = 128
OAUTH_CLIENT_NAME_MAX_LENGTH = 100
OAUTH_REDIRECT_URI_MAX_COUNT = 10
OAUTH_REDIRECT_URI_MAX_LENGTH = 2048
PKCE_VERIFIER_PATTERN = re.compile(r"^[A-Za-z0-9._~-]{43,128}$")
PKCE_CHALLENGE_PATTERN = re.compile(r"^[A-Za-z0-9_-]{43}$")
LOCALHOST_NAMES = {"localhost"}
REDIRECT_URI_SCHEMES = {"http", "https"}
logger = logging.getLogger(__name__)


def register_oauth_authorization_routes(
    app,
    *,
    service: ConnectService,
    oauth: ConnectOAuthRegistry,
    config: HubConfig,
) -> None:
    @app.post("/register", include_in_schema=False)
    @app.post("/oauth/register", include_in_schema=False)
    async def oauth_register(request: Request) -> JSONResponse:
        try:
            payload = await request.json()
        except ValueError as exc:
            raise _oauth_error(
                "invalid_client_metadata", "Request body must be a JSON object"
            ) from exc
        redirect_uris = _redirect_uris_from_payload(payload)
        client_name = str(payload.get("client_name") or "MCP client")
        if len(client_name) > OAUTH_CLIENT_NAME_MAX_LENGTH:
            raise _oauth_error("invalid_client_metadata", "client_name is too long")
        client_id = "amh_client_" + secrets.token_urlsafe(24)
        issued_at = int(time.time())
        ttl_seconds = config.api.oauth.client_registration_ttl_seconds
        try:
            await service.agent.create_oauth_client(
                client_id=client_id,
                client_name=client_name,
                redirect_uris=redirect_uris,
                expires_at=utc_after(ttl_seconds),
                max_active_clients=OAUTH_CLIENT_MAX_RECORDS,
            )
        except OAuthClientQuotaExceeded as exc:
            raise _oauth_error(
                "invalid_client_metadata", "Active OAuth client quota is exhausted"
            ) from exc
        return JSONResponse(
            {
                "client_id": client_id,
                "client_id_issued_at": issued_at,
                "client_id_expires_at": issued_at + ttl_seconds,
                "redirect_uris": redirect_uris,
                "token_endpoint_auth_method": "none",
                "grant_types": ["authorization_code", "refresh_token"],
                "response_types": ["code"],
            },
            status_code=201,
        )

    @app.get("/authorize", include_in_schema=False)
    @app.get("/oauth/authorize", include_in_schema=False)
    async def oauth_authorize(request: Request) -> Response:
        params = dict(request.query_params)
        try:
            auth_request = await _validated_authorization_request(
                service=service, request=request, params=params, config=config
            )
        except HTTPException as exc:
            redirect_uri = await _trusted_authorization_redirect_uri(
                service=service, request=request, params=params
            )
            if redirect_uri is None:
                raise
            return _authorization_error_redirect(
                redirect_uri,
                exc,
                state=str(params.get("state") or ""),
            )
        _session(request)["pending_oauth_authorization"] = auth_request
        provider = _first_enabled_provider(config)
        return await oauth.authorize_redirect(
            request,
            provider=provider,
            state_data={"pending_oauth_authorization": auth_request},
        )

    @app.post("/oauth/authorize/approve", include_in_schema=False)
    async def oauth_authorize_approve(request: Request) -> Response:
        form = dict((await request.form()).items())
        session = await service.session_from_request(request)
        if session is None:
            raise HTTPException(status_code=403, detail="Connect session is required")
        if not csrf_matches(session, str(form.get("csrf_token") or ""), config=config):
            raise HTTPException(status_code=403, detail="Invalid CSRF token")
        pending = _session(request).pop("pending_oauth_authorization", None)
        auth_request = await _validated_pending_authorization_request(
            service=service, request=request, pending=pending, config=config
        )
        return await _authorization_code_redirect(
            service, auth_request, owner_id=str(session["user_id"])
        )

    @app.post("/oauth/authorize/deny", include_in_schema=False)
    async def oauth_authorize_deny(request: Request) -> Response:
        form = dict((await request.form()).items())
        session = await service.session_from_request(request)
        if session is None:
            raise HTTPException(status_code=403, detail="Connect session is required")
        if not csrf_matches(session, str(form.get("csrf_token") or ""), config=config):
            raise HTTPException(status_code=403, detail="Invalid CSRF token")
        pending = _session(request).pop("pending_oauth_authorization", None)
        auth_request = await _validated_pending_authorization_request(
            service=service, request=request, pending=pending, config=config
        )
        return _authorization_error_redirect(
            auth_request["redirect_uri"],
            _oauth_error("access_denied", "The resource owner denied the request"),
            state=auth_request["state"],
        )

    @app.post("/token", include_in_schema=False)
    @app.post("/oauth/token", include_in_schema=False)
    async def oauth_token(request: Request) -> JSONResponse:
        form = dict((await request.form()).items())
        try:
            grant_type = form.get("grant_type")
            if grant_type == "refresh_token":
                return await _refresh_token_response(service=service, config=config, form=form)
            if grant_type != "authorization_code":
                raise _oauth_error(
                    "unsupported_grant_type",
                    "Only authorization_code and refresh_token are supported",
                )
            code = str(form.get("code") or "")
            record = await service.agent.consume_oauth_authorization_code(code)
            if record is None:
                raise _oauth_error("invalid_grant", "Authorization code is invalid or expired")
            if form.get("client_id") != record["client_id"]:
                raise _oauth_error("invalid_grant", "client_id does not match authorization code")
            if form.get("redirect_uri") != record["redirect_uri"]:
                raise _oauth_error(
                    "invalid_grant", "redirect_uri does not match authorization code"
                )
            if not _pkce_verifier_matches(str(form.get("code_verifier") or ""), record):
                raise _oauth_error("invalid_grant", "PKCE verification failed")

            return await _issue_access_and_refresh_tokens(
                service=service,
                config=config,
                client_id=str(record["client_id"]),
                owner_id=str(record["owner_id"]),
                resource=str(record["resource"]),
                scopes=_scopes_from_scope_value(str(record.get("scope") or "")),
            )
        except HTTPException as exc:
            return _oauth_error_response(exc)

    @app.post("/oauth/revoke", include_in_schema=False)
    async def oauth_revoke(request: Request) -> JSONResponse:
        form = dict((await request.form()).items())
        token = str(form.get("token") or "")
        if token:
            if await service.agent.revoke_oauth_refresh_token(token):
                return JSONResponse({})
            try:
                revoked = await service.agent.revoke_oauth_authorization_for_access_token(token)
            except NotImplementedError:
                revoked = False
            if not revoked:
                token_id = jwt_payload(token).get("jti")
                if isinstance(token_id, str) and token_id:
                    await service.agent.revoke_auth_token(token_id)
        return JSONResponse({})


def pending_authorization_redirect(request: Request) -> RedirectResponse | None:
    state_data = getattr(request.state, "oauth_provider_state_data", None)
    pending = (
        state_data.get("pending_oauth_authorization") if isinstance(state_data, dict) else None
    )
    session_pending = _session(request).get("pending_oauth_authorization")
    if not isinstance(pending, dict):
        pending = session_pending
    if not isinstance(pending, dict):
        return None
    _session(request)["pending_oauth_authorization"] = pending
    return RedirectResponse("/connect", status_code=303)


async def pending_authorization_model(
    request: Request, *, service: ConnectService, config: HubConfig
) -> dict[str, str] | None:
    try:
        pending = request.session.get("pending_oauth_authorization")
    except AssertionError:
        return None
    if not isinstance(pending, dict):
        return None
    try:
        auth_request = await _validated_pending_authorization_request(
            service=service, request=request, pending=pending, config=config
        )
    except HTTPException:
        return None
    client = (
        await _oauth_client_for_request(
            service=service, request=request, client_id=auth_request["client_id"]
        )
        or {}
    )
    return {
        "client_name": str(client.get("client_name") or "MCP client"),
        "redirect_uri": auth_request["redirect_uri"],
        "scope": auth_request["scope"] or f"{READ_SCOPE} {WRITE_SCOPE}",
        "resource": auth_request["resource"],
    }


async def _validated_authorization_request(
    *, service: ConnectService, request: Request, params: dict[str, str], config: HubConfig
) -> dict[str, str]:
    client_id = str(params.get("client_id") or "")
    client = await _oauth_client_for_request(service=service, request=request, client_id=client_id)
    if client is None:
        raise _oauth_error("invalid_client", "Unknown OAuth client_id")
    redirect_uri = str(params.get("redirect_uri") or "")
    if redirect_uri not in client["redirect_uris"]:
        raise _oauth_error("invalid_request", "redirect_uri is not registered")
    if params.get("response_type") != "code":
        raise _oauth_error("unsupported_response_type", "Only response_type=code is supported")
    if params.get("code_challenge_method") != "S256":
        raise _oauth_error("invalid_request", "PKCE S256 is required")
    code_challenge = str(params.get("code_challenge") or "")
    if not PKCE_CHALLENGE_PATTERN.fullmatch(code_challenge):
        raise _oauth_error("invalid_request", "code_challenge must be 43 base64url characters")
    scope = str(params.get("scope") or "")
    _validate_requested_scopes(scope, config=config)
    return {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
        "resource": str(params.get("resource") or ""),
        "scope": scope,
        "state": str(params.get("state") or ""),
    }


async def _validated_pending_authorization_request(
    *, service: ConnectService, request: Request, pending: object, config: HubConfig
) -> dict[str, str]:
    if not isinstance(pending, dict):
        raise _oauth_error("invalid_request", "No pending authorization request")
    values = {key: str(pending.get(key) or "") for key in _AUTH_REQUEST_FIELDS}
    client = await _oauth_client_for_request(
        service=service, request=request, client_id=values["client_id"]
    )
    if client is None:
        raise _oauth_error("invalid_client", "Unknown OAuth client_id")
    if values["redirect_uri"] not in client["redirect_uris"]:
        raise _oauth_error("invalid_request", "redirect_uri is not registered")
    if values["code_challenge_method"] != "S256" or not PKCE_CHALLENGE_PATTERN.fullmatch(
        values["code_challenge"]
    ):
        raise _oauth_error("invalid_request", "PKCE S256 is required")
    _validate_requested_scopes(values["scope"], config=config)
    return values


async def _oauth_client_for_request(
    *, service: ConnectService, request: Request, client_id: str
) -> dict[str, Any] | None:
    if client_id:
        try:
            client = await service.agent.oauth_client(client_id)
        except NotImplementedError:
            client = None
        if client is not None:
            return client
    return None


async def _trusted_authorization_redirect_uri(
    *, service: ConnectService, request: Request, params: dict[str, str]
) -> str | None:
    client_id = str(params.get("client_id") or "")
    client = await _oauth_client_for_request(service=service, request=request, client_id=client_id)
    redirect_uri = str(params.get("redirect_uri") or "")
    if client is None or redirect_uri not in client["redirect_uris"]:
        return None
    return redirect_uri


def _validate_requested_scopes(value: str, *, config: HubConfig) -> None:
    requested = value.split()
    supported = set(config.api.oauth.scopes_supported)
    if any(scope not in supported for scope in requested):
        raise _oauth_error("invalid_scope", "One or more requested scopes are not supported")


async def _refresh_token_response(
    *,
    service: ConnectService,
    config: HubConfig,
    form: dict[str, Any],
) -> JSONResponse:
    refresh_token = str(form.get("refresh_token") or "")
    client_id = str(form.get("client_id") or "")
    if not refresh_token:
        raise _oauth_error("invalid_grant", "Refresh token is invalid")
    record = await service.agent.oauth_refresh_token(refresh_token)
    if record is None:
        raise _oauth_error("invalid_grant", "Refresh token is invalid")
    if record["client_id"] != client_id:
        raise _oauth_error("invalid_grant", "Refresh token is invalid")
    if record.get("revoked_at") is not None or record.get("consumed_at") is not None:
        await service.agent.revoke_oauth_refresh_token_family(str(record["token_family_id"]))
        raise _oauth_error("invalid_grant", "Refresh token is invalid")
    if _timestamp_is_expired(record.get("expires_at")):
        raise _oauth_error("invalid_grant", "Refresh token is expired")
    consumed = await service.agent.consume_oauth_refresh_token(refresh_token)
    if consumed is None or consumed.get("consumed_at") is None:
        await service.agent.revoke_oauth_refresh_token_family(str(record["token_family_id"]))
        raise _oauth_error("invalid_grant", "Refresh token is invalid")
    scopes = record.get("scopes")
    return await _issue_access_and_refresh_tokens(
        service=service,
        config=config,
        client_id=client_id,
        owner_id=str(record["owner_id"]),
        resource=str(record["resource"]),
        scopes=[str(scope) for scope in scopes] if isinstance(scopes, list) else [],
        token_family_id=str(record["token_family_id"]),
    )


async def _issue_access_and_refresh_tokens(
    *,
    service: ConnectService,
    config: HubConfig,
    client_id: str,
    owner_id: str,
    resource: str,
    scopes: list[str],
    token_family_id: str | None = None,
) -> JSONResponse:
    normalized_scopes = _normalize_scopes(scopes)
    issued_token = issue_hub_token(
        config=config,
        owner_id=owner_id,
        scopes=normalized_scopes,
    )
    token_record = await service.agent.create_auth_token(
        owner_id=owner_id,
        token=issued_token,
        token_display_name="MCP OAuth client",
        expires_at=utc_after(config.api.connect.token_ttl_seconds),
        scopes=normalized_scopes,
    )
    access_token_id = str(
        token_record.get("token_id") or jwt_payload(issued_token).get("jti") or ""
    )
    refresh_token = "amh_refresh_" + secrets.token_urlsafe(32)
    family_id = token_family_id or "amh_family_" + secrets.token_urlsafe(24)
    try:
        await service.agent.create_oauth_refresh_token(
            refresh_token=refresh_token,
            token_family_id=family_id,
            client_id=client_id,
            owner_id=owner_id,
            scopes=normalized_scopes,
            resource=resource,
            access_token_id=access_token_id,
            expires_at=utc_after(config.api.oauth.refresh_token_ttl_seconds),
        )
    except Exception:
        await _revoke_failed_access_token(service, access_token_id)
        raise
    return JSONResponse(
        {
            "access_token": issued_token,
            "token_type": "Bearer",
            "expires_in": config.api.connect.token_ttl_seconds,
            "refresh_token": refresh_token,
            "refresh_token_expires_in": config.api.oauth.refresh_token_ttl_seconds,
            "scope": " ".join(normalized_scopes),
            "resource": resource,
            "jti": access_token_id,
        }
    )


async def _revoke_failed_access_token(service: ConnectService, token_id: str) -> None:
    try:
        await service.agent.revoke_auth_token(token_id)
    except Exception:
        logger.exception(
            "OAuth access-token compensation failed",
            extra={"event": "oauth_access_token_compensation_failed"},
        )


def _scopes_from_scope_value(value: str) -> list[str]:
    return _normalize_scopes(str(value).split())


def _normalize_scopes(scopes: list[str]) -> list[str]:
    normalized = [scope for scope in (str(scope).strip() for scope in scopes) if scope]
    return normalized or [READ_SCOPE, WRITE_SCOPE]


def _timestamp_is_expired(value: object) -> bool:
    if value is None:
        return False
    if isinstance(value, (int, float)):
        return value <= time.time()
    try:
        timestamp = str(value).replace("Z", "+00:00")
        parsed = datetime.fromisoformat(timestamp)
    except ValueError:
        return True
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed <= datetime.now(UTC)


async def _authorization_code_redirect(
    service: ConnectService, auth_request: dict[str, str], *, owner_id: str
) -> RedirectResponse:
    code = "amh_code_" + secrets.token_urlsafe(32)
    await service.agent.create_oauth_authorization_code(
        code=code,
        client_id=auth_request["client_id"],
        owner_id=owner_id,
        redirect_uri=auth_request["redirect_uri"],
        code_challenge=auth_request["code_challenge"],
        resource=auth_request["resource"],
        scope=auth_request["scope"],
        expires_at=utc_after(AUTHORIZATION_CODE_TTL_SECONDS),
    )
    query = {"code": code}
    if auth_request.get("state"):
        query["state"] = auth_request["state"]
    separator = "&" if "?" in auth_request["redirect_uri"] else "?"
    return RedirectResponse(
        f"{auth_request['redirect_uri']}{separator}{urlencode(query)}",
        status_code=303,
    )


def _authorization_error_redirect(
    redirect_uri: str, exc: HTTPException, *, state: str
) -> RedirectResponse:
    detail = exc.detail if isinstance(exc.detail, dict) else {}
    query = {
        "error": str(detail.get("error") or "invalid_request"),
        "error_description": str(detail.get("error_description") or "Authorization failed"),
    }
    if state:
        query["state"] = state
    separator = "&" if "?" in redirect_uri else "?"
    return RedirectResponse(f"{redirect_uri}{separator}{urlencode(query)}", status_code=303)


def _oauth_error_response(exc: HTTPException) -> JSONResponse:
    detail = exc.detail if isinstance(exc.detail, dict) else {"error": "invalid_request"}
    return JSONResponse(
        detail,
        status_code=exc.status_code,
        headers={"Cache-Control": "no-store", "Pragma": "no-cache"},
    )


def _redirect_uris_from_payload(payload: Any) -> list[str]:
    if not isinstance(payload, dict):
        raise _oauth_error("invalid_client_metadata", "Request body must be a JSON object")
    values = payload.get("redirect_uris")
    if not isinstance(values, list) or not values:
        raise _oauth_error("invalid_client_metadata", "redirect_uris is required")
    if len(values) > OAUTH_REDIRECT_URI_MAX_COUNT:
        raise _oauth_error("invalid_client_metadata", "too many redirect_uris")
    redirect_uris = [_validated_redirect_uri(value) for value in values]
    if not redirect_uris:
        raise _oauth_error("invalid_client_metadata", "redirect_uris contains invalid values")
    return redirect_uris


def _validated_redirect_uri(value: object) -> str:
    if not isinstance(value, str) or not value or value.strip() != value:
        raise _oauth_error("invalid_client_metadata", "redirect_uris contains invalid values")
    if "\r" in value or "\n" in value:
        raise _oauth_error("invalid_client_metadata", "redirect_uris contains invalid values")
    if len(value) > OAUTH_REDIRECT_URI_MAX_LENGTH:
        raise _oauth_error("invalid_client_metadata", "redirect_uri is too long")
    try:
        parsed = urlparse(value)
        _ = parsed.port
    except ValueError as exc:
        raise _oauth_error(
            "invalid_client_metadata", "redirect_uris contains invalid values"
        ) from exc
    if (
        parsed.scheme not in REDIRECT_URI_SCHEMES
        or not parsed.netloc
        or parsed.fragment
        or parsed.username
        or parsed.password
        or not parsed.hostname
        or not _is_loopback_host(parsed.hostname)
    ):
        raise _oauth_error("invalid_client_metadata", "redirect_uris contains invalid values")
    return value


def _is_loopback_host(hostname: str) -> bool:
    normalized = hostname.rstrip(".").lower()
    if normalized in LOCALHOST_NAMES:
        return True
    try:
        return ipaddress.ip_address(normalized).is_loopback
    except ValueError:
        return False


def _pkce_verifier_matches(verifier: str, record: dict[str, object]) -> bool:
    if not PKCE_VERIFIER_PATTERN.fullmatch(verifier):
        return False
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")
    expected = str(record["code_challenge"])
    return hmac.compare_digest(challenge, expected)


def _session(request: Request) -> dict[str, Any]:
    try:
        return request.session
    except AssertionError as exc:
        raise HTTPException(
            status_code=503, detail="OAuth session middleware is not configured"
        ) from exc


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


_AUTH_REQUEST_FIELDS = (
    "client_id",
    "redirect_uri",
    "code_challenge",
    "code_challenge_method",
    "resource",
    "scope",
    "state",
)
