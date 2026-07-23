from __future__ import annotations

import base64
import hashlib
import hmac
import html
import json
import logging
import os
import secrets
import time
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlencode

from fastapi import FastAPI, HTTPException, Request
from starlette.responses import HTMLResponse, RedirectResponse, Response

from memory.auth import READ_SCOPE, WRITE_SCOPE
from memory.config import HubConfig
from memory.ingestion.base_agent import BaseIngestionAgent

logger = logging.getLogger(__name__)

OAUTH_STATE_COOKIE = "amh_oauth_state"
SECRET_HASH_ITERATIONS = 210_000
CLIENT_MATRIX: tuple[dict[str, str], ...] = (
    {
        "name": "Codex",
        "status": "Unverified",
        "snippet": '[mcp_servers.ai_memory_hub]\nurl = "{mcp_url}"\nheaders = {{ Authorization = "Bearer <hub-token>" }}',
    },
    {
        "name": "Copilot CLI",
        "status": "Unverified",
        "snippet": 'copilot mcp add --transport http --header "Authorization: Bearer <hub-token>" ai-memory-hub {mcp_url}',
    },
    {"name": "Pi", "status": "Unverified", "snippet": 'MCP URL: {mcp_url}\nBearer token: <hub-token>'},
    {
        "name": "OpenCode",
        "status": "Unverified",
        "snippet": '{{"mcp": {{"ai-memory-hub": {{"type": "remote", "url": "{mcp_url}", "headers": {{"Authorization": "Bearer <hub-token>"}}}}}}}}',
    },
    {"name": "Claude", "status": "Unverified", "snippet": 'mcp add ai-memory-hub {mcp_url}'},
    {"name": "Hermes", "status": "Unverified", "snippet": 'MCP URL: {mcp_url}\nToken: <hub-token>'},
    {"name": "OpenShell", "status": "Unverified", "snippet": 'MCP URL: {mcp_url}\nToken: <hub-token>'},
    {"name": "OpenClaw", "status": "Unverified", "snippet": 'MCP URL: {mcp_url}\nToken: <hub-token>'},
    {"name": "Gemini CLI", "status": "Unverified", "snippet": 'MCP URL: {mcp_url}\nToken: <hub-token>'},
)


def register_connect_routes(app: FastAPI, *, agent: BaseIngestionAgent, config: HubConfig) -> None:
    if not config.api.connect.enabled:
        return

    @app.get("/", include_in_schema=False)
    async def root() -> Response:
        return RedirectResponse("/connect", status_code=307)

    @app.get("/connect", response_class=HTMLResponse, include_in_schema=False)
    async def connect(request: Request) -> HTMLResponse:
        session = await _session_from_request(request, agent=agent, config=config)
        return HTMLResponse(
            _render_connect_page(
                config=config,
                signed_in=session,
                issued_token=None,
                csrf_token=_csrf_token_from_cookie(request, config=config),
            )
        )

    @app.get("/auth/{provider}", include_in_schema=False)
    async def auth_provider(provider: str) -> Response:
        provider_name = _normalize_passport_provider(provider)
        provider_config = _passport_provider_config(config, provider_name)
        if provider_config is None or not provider_config.enabled:
            raise HTTPException(status_code=404, detail="OAuth provider is not enabled")
        client_id = _env_secret(provider_config.client_id_env)
        if not client_id:
            raise HTTPException(status_code=503, detail="OAuth client id is not configured")
        if not provider_config.authorization_url:
            raise HTTPException(status_code=503, detail="OAuth authorization URL is not configured")
        nonce = secrets.token_urlsafe(24)
        state = secrets.token_urlsafe(24)
        state_cookie = _sign_state(
            {"state": state, "nonce": nonce, "provider": provider_name, "iat": int(time.time())},
            config,
        )
        callback_url = _provider_callback_url(config, provider_name)
        params = {
            "client_id": client_id,
            "redirect_uri": callback_url,
            "response_type": "code",
            "scope": " ".join(provider_config.scopes),
            "state": state,
            "nonce": nonce,
            "access_type": "offline",
            "prompt": "select_account",
        }
        response = RedirectResponse(
            provider_config.authorization_url + "?" + urlencode(params),
            status_code=303,
        )
        response.set_cookie(
            OAUTH_STATE_COOKIE,
            state_cookie,
            httponly=True,
            secure=_secure_cookie(config),
            samesite="lax",
            max_age=600,
        )
        return response

    @app.get("/auth/{provider}/callback", response_class=HTMLResponse, include_in_schema=False)
    async def auth_provider_callback(provider: str, request: Request) -> HTMLResponse:
        provider_name = _normalize_passport_provider(provider)
        provider_config = _passport_provider_config(config, provider_name)
        if provider_config is None or not provider_config.enabled:
            raise HTTPException(status_code=404, detail="OAuth provider is not enabled")
        if request.query_params.get("error"):
            logger.info(
                "OAuth login denied",
                extra={"event": "connect_login_denied", "provider": provider_name},
            )
            raise HTTPException(status_code=403, detail="OAuth login was denied")
        state_data = _verify_state_cookie(request.cookies.get(OAUTH_STATE_COOKIE), config)
        if (
            state_data is None
            or request.query_params.get("state") != state_data.get("state")
            or state_data.get("provider") != provider_name
        ):
            raise HTTPException(status_code=400, detail="Invalid OAuth state")
        code = request.query_params.get("code")
        if not code:
            raise HTTPException(status_code=400, detail="Missing OAuth code")
        claims = await _exchange_provider_code(
            request,
            config=config,
            provider=provider_name,
            code=code,
            nonce=str(state_data["nonce"]),
        )
        _validate_provider_claims(claims, config, provider=provider_name)
        identity = await agent.find_or_create_oauth_identity(
            provider=provider_name,
            provider_subject=str(claims["sub"]),
            email=str(claims.get("email") or ""),
            display_name=str(claims.get("name") or claims.get("email") or ""),
        )
        session_id = secrets.token_urlsafe(32)
        csrf_token = secrets.token_urlsafe(32)
        expires_at = _utc_after(config.api.connect.session_ttl_seconds)
        await agent.create_web_session(
            session_id_hash=_hash_secret(session_id, config, purpose="connect-session"),
            user_id=str(identity["user_id"]),
            csrf_token_hash=_hash_secret(csrf_token, config, purpose="connect-csrf"),
            expires_at=expires_at,
        )
        issued_token = _issue_hub_token(config=config, owner_id=str(identity["user_id"]))
        issued_token_id = str(_jwt_payload(issued_token).get("jti") or "")
        await agent.create_auth_token(
            owner_id=str(identity["user_id"]),
            token=issued_token,
            token_display_name="Google Connect UI",
            expires_at=_utc_after(config.api.connect.token_ttl_seconds),
            scopes=[READ_SCOPE, WRITE_SCOPE],
        )
        logger.info(
            "OAuth login succeeded",
            extra={
                "event": "connect_login_success",
                "provider": provider_name,
                "owner_id": identity["user_id"],
            },
        )
        response = HTMLResponse(
            _render_connect_page(
                config=config,
                signed_in=identity,
                issued_token=issued_token,
                csrf_token=csrf_token,
            )
        )
        response.set_cookie(
            config.api.connect.session_cookie_name,
            session_id,
            httponly=True,
            secure=_secure_cookie(config),
            samesite="lax",
            max_age=config.api.connect.session_ttl_seconds,
        )
        if issued_token_id:
            response.set_cookie(
                "amh_token_id",
                issued_token_id,
                httponly=True,
                secure=_secure_cookie(config),
                samesite="lax",
                max_age=config.api.connect.token_ttl_seconds,
            )
        response.set_cookie(
            "amh_csrf",
            csrf_token,
            httponly=False,
            secure=_secure_cookie(config),
            samesite="lax",
            max_age=config.api.connect.session_ttl_seconds,
        )
        response.delete_cookie(OAUTH_STATE_COOKIE)
        return response

    @app.post("/auth/logout", include_in_schema=False)
    async def auth_logout(request: Request) -> Response:
        session_id = request.cookies.get(config.api.connect.session_cookie_name)
        token_id = request.cookies.get("amh_token_id")
        form = parse_qs((await request.body()).decode("utf-8"))
        csrf_token = str((form.get("csrf_token") or [""])[0])
        session = await _session_from_request(request, agent=agent, config=config)
        if session is not None and not _csrf_matches(session, csrf_token, config=config):
            raise HTTPException(status_code=403, detail="Invalid CSRF token")
        if session_id:
            await agent.revoke_web_session(_hash_secret(session_id, config, purpose="connect-session"))
        if token_id:
            await agent.revoke_auth_token(token_id)
        response = RedirectResponse("/connect", status_code=303)
        response.delete_cookie(config.api.connect.session_cookie_name)
        response.delete_cookie("amh_csrf")
        response.delete_cookie("amh_token_id")
        logger.info("Connect UI logout", extra={"event": "connect_logout"})
        return response


def connect_status(config: HubConfig) -> dict[str, object]:
    providers = {
        provider: _provider_status(config, provider)
        for provider in config.api.connect.passport.providers
    }
    return {
        "enabled": config.api.connect.enabled,
        "mcp_url": _mcp_url(config),
        "passport": {"providers": providers},
        "google_oauth": providers.get("google", {}),
    }


async def _session_from_request(
    request: Request, *, agent: BaseIngestionAgent, config: HubConfig
) -> dict[str, object] | None:
    session_id = request.cookies.get(config.api.connect.session_cookie_name)
    if not session_id:
        return None
    return await agent.web_session_for_hash(_hash_secret(session_id, config, purpose="connect-session"))


async def _exchange_provider_code(
    request: Request, *, config: HubConfig, provider: str, code: str, nonce: str
) -> dict[str, object]:
    override = getattr(request.app.state, f"{provider}_oauth_exchange", None)
    if override is not None:
        claims = await override(code=code, nonce=nonce, config=config, provider=provider)
        if isinstance(claims, dict):
            return claims
        raise HTTPException(status_code=502, detail="OAuth test exchange returned invalid claims")
    if provider != "google":
        raise HTTPException(
            status_code=501,
            detail="Live OAuth exchange is currently implemented for Google only",
        )
    try:
        from authlib.integrations.httpx_client import AsyncOAuth2Client
    except ImportError as exc:
        raise HTTPException(
            status_code=503,
            detail="Google OAuth support requires installing the oauth optional extra",
        ) from exc
    provider_config = _passport_provider_config(config, provider)
    if provider_config is None:
        raise HTTPException(status_code=404, detail="OAuth provider is not supported")
    client = AsyncOAuth2Client(
        client_id=_env_secret(provider_config.client_id_env),
        client_secret=_env_secret(provider_config.client_secret_env),
        scope=" ".join(provider_config.scopes),
        redirect_uri=_provider_callback_url(config, provider),
    )
    token = await client.fetch_token(
        provider_config.token_url,
        code=code,
        grant_type="authorization_code",
    )
    id_token = token.get("id_token")
    if not isinstance(id_token, str):
        raise HTTPException(status_code=502, detail="Google OAuth response did not include an id token")
    parse_id_token = getattr(client, "parse_id_token", None)
    if parse_id_token is None:
        raise HTTPException(status_code=503, detail="Installed Authlib client lacks OIDC support")
    claims = await parse_id_token(token, nonce=nonce)
    return dict(claims)


def _validate_provider_claims(
    claims: dict[str, object], config: HubConfig, *, provider: str
) -> None:
    now = int(time.time())
    provider_config = _passport_provider_config(config, provider)
    if provider_config is None:
        raise HTTPException(status_code=404, detail="OAuth provider is not supported")
    if provider == "google":
        issuer_values = {"https://accounts.google.com", "accounts.google.com"}
    else:
        issuer_values = {provider_config.issuer} if provider_config.issuer else set()
    if issuer_values and claims.get("iss") not in issuer_values:
        raise HTTPException(status_code=403, detail="Invalid OAuth issuer")
    if claims.get("aud") != _env_secret(provider_config.client_id_env):
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


def _render_connect_page(
    *,
    config: HubConfig,
    signed_in: dict[str, object] | None,
    issued_token: str | None,
    csrf_token: str | None,
) -> str:
    mcp_url = _mcp_url(config)
    auth_label = "Signed in" if signed_in else "Not signed in"
    identity = str(signed_in.get("email") or signed_in.get("user_id")) if signed_in else ""
    snippets = "\n".join(_client_snippet_card(client, mcp_url=mcp_url) for client in CLIENT_MATRIX)
    token_block = ""
    if issued_token:
        token_block = (
            "<section><h2>Hub Token</h2>"
            "<p>This token is shown once. Store it in your MCP client, then sign out here if needed.</p>"
            f'<textarea readonly rows="5">{html.escape(issued_token)}</textarea></section>'
        )
    login_or_logout = (
        f'<form method="post" action="/auth/logout">'
        f'<input type="hidden" name="csrf_token" value="{html.escape(csrf_token or "")}">'
        '<button type="submit">Sign Out</button></form>'
        if signed_in
        else _passport_login_buttons(config)
    )
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>ai-memory-hub Connect</title>
  <style>
    body {{ font-family: system-ui, sans-serif; margin: 2rem; color: #17202a; }}
    main {{ max-width: 960px; margin: 0 auto; }}
    code, textarea {{ width: 100%; box-sizing: border-box; }}
    textarea {{ font-family: ui-monospace, SFMono-Regular, Consolas, monospace; }}
    section, article {{ border-top: 1px solid #ccd3db; padding: 1rem 0; }}
    .button, button {{ display: inline-block; padding: .55rem .8rem; border: 1px solid #4b5f73; background: #fff; color: #17202a; text-decoration: none; border-radius: 4px; }}
    .muted {{ color: #5e6b78; }}
  </style>
</head>
<body>
<main>
  <h1>ai-memory-hub Connect</h1>
  <section>
    <p>Status: <strong>ok</strong></p>
    <p>Auth: <strong>{html.escape(auth_label)}</strong> {html.escape(identity)}</p>
    <p>MCP URL: <code>{html.escape(mcp_url)}</code></p>
    {login_or_logout}
  </section>
  {token_block}
  <section>
    <h2>Client Setup</h2>
    <p class="muted">Account switching is client-driven. Clear the old MCP auth token in the client, sign in here with the other Google account, then use the new hub token.</p>
    {snippets}
  </section>
</main>
<script>
document.querySelectorAll("button[data-copy]").forEach((button) => {{
  button.addEventListener("click", () => {{
    const target = document.getElementById(button.dataset.copy);
    if (target) navigator.clipboard.writeText(target.value);
  }});
}});
</script>
</body>
</html>"""


def _client_snippet_card(client: dict[str, str], *, mcp_url: str) -> str:
    element_id = "snippet-" + hashlib.sha256(client["name"].encode("utf-8")).hexdigest()[:12]
    snippet = client["snippet"].format(mcp_url=mcp_url)
    return (
        f"<article><h3>{html.escape(client['name'])}</h3>"
        f"<p>{html.escape(client['status'])}</p>"
        f'<textarea id="{element_id}" readonly rows="4">{html.escape(snippet)}</textarea>'
        f'<button type="button" data-copy="{element_id}">Copy</button></article>'
    )


def _mcp_url(config: HubConfig) -> str:
    if config.api.oauth.resource:
        return config.api.oauth.resource.rstrip("/")
    base = config.api.public_base_url.rstrip("/") or f"http://{config.api.host}:{config.api.port}"
    return f"{base}/mcp"


def _provider_callback_url(config: HubConfig, provider: str) -> str:
    provider_config = _passport_provider_config(config, provider)
    if provider_config is None:
        raise HTTPException(status_code=404, detail="OAuth provider is not supported")
    return (
        provider_config.callback_url
        or f"{config.api.public_base_url.rstrip('/')}/auth/{provider}/callback"
    )


def _normalize_passport_provider(provider: str) -> str:
    value = str(provider).strip().lower()
    if value not in {"google", "meta", "x"}:
        raise HTTPException(status_code=404, detail="OAuth provider is not supported")
    return value


def _passport_provider_config(config: HubConfig, provider: str):
    if provider not in config.api.connect.passport.providers:
        return None
    return getattr(config.api.connect.passport, provider, None)


def _enabled_passport_providers(config: HubConfig) -> list[tuple[str, object]]:
    providers = []
    for provider in config.api.connect.passport.providers:
        provider_config = _passport_provider_config(config, provider)
        if provider_config is not None and provider_config.enabled:
            providers.append((provider, provider_config))
    return providers


def _passport_login_buttons(config: HubConfig) -> str:
    buttons = []
    for provider, provider_config in _enabled_passport_providers(config):
        label = getattr(provider_config, "label", None) or provider.title()
        buttons.append(
            f'<a class="button" href="/auth/{html.escape(provider)}">'
            f"Sign In With {html.escape(str(label))}</a>"
        )
    if not buttons:
        return '<p class="muted">No sign-in providers are enabled.</p>'
    return "\n".join(buttons)


def _provider_status(config: HubConfig, provider: str) -> dict[str, object]:
    provider_config = _passport_provider_config(config, provider)
    if provider_config is None:
        return {"enabled": False}
    return {
        "enabled": provider_config.enabled,
        "label": provider_config.label or provider.title(),
        "client_id_configured": bool(_env_secret(provider_config.client_id_env)),
        "client_secret_configured": bool(_env_secret(provider_config.client_secret_env)),
        "callback_url_configured": bool(provider_config.callback_url),
        "authorization_url_configured": bool(provider_config.authorization_url),
        "token_url_configured": bool(provider_config.token_url),
        "allowed_domains": list(provider_config.allowed_domains),
        "allowed_emails_configured": bool(provider_config.allowed_emails),
    }


def _env_secret(env_name: str) -> str:
    return os.environ.get(env_name, "").strip()


def _secure_cookie(config: HubConfig) -> bool:
    base = config.api.public_base_url
    return bool(base.startswith("https://") and "localhost" not in base and "127.0.0.1" not in base)


def _csrf_token_from_cookie(request: Request, *, config: HubConfig) -> str | None:
    session_id = request.cookies.get(config.api.connect.session_cookie_name)
    return request.cookies.get("amh_csrf") if session_id else None


def _csrf_matches(session: dict[str, object], csrf_token: str, *, config: HubConfig) -> bool:
    expected = str(session.get("csrf_token_hash") or "")
    actual = _hash_secret(csrf_token, config, purpose="connect-csrf")
    return bool(csrf_token) and hmac.compare_digest(expected, actual)


def _hash_secret(value: str, config: HubConfig, *, purpose: str) -> str:
    secret = _session_secret(config)
    salt = f"ai-memory-hub:{purpose}:{secret}".encode("utf-8")
    digest = hashlib.pbkdf2_hmac(
        "sha256",
        value.encode("utf-8"),
        salt,
        SECRET_HASH_ITERATIONS,
    ).hex()
    return "pbkdf2-sha256:" + digest


def _session_secret(config: HubConfig) -> str:
    return config.api.connect.session_secret or os.environ.get(config.api.connect.session_secret_env, "")


def _sign_state(payload: dict[str, object], config: HubConfig) -> str:
    raw = _b64(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
    sig = _b64(hmac.new(_session_secret(config).encode("utf-8"), raw.encode("ascii"), hashlib.sha256).digest())
    return f"{raw}.{sig}"


def _verify_state_cookie(value: str | None, config: HubConfig) -> dict[str, object] | None:
    if not value:
        return None
    raw, sep, sig = value.partition(".")
    if not sep:
        return None
    expected = _b64(hmac.new(_session_secret(config).encode("utf-8"), raw.encode("ascii"), hashlib.sha256).digest())
    if not hmac.compare_digest(expected, sig):
        return None
    try:
        payload = json.loads(_unb64(raw))
    except (ValueError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    iat = payload.get("iat")
    if not isinstance(iat, int) or iat < int(time.time()) - 600:
        return None
    return payload


def _issue_hub_token(*, config: HubConfig, owner_id: str) -> str:
    now = int(time.time())
    payload = {
        "sub": owner_id,
        "iss": config.api.public_base_url.rstrip("/"),
        "aud": _mcp_url(config),
        "resource": _mcp_url(config),
        "scope": f"{READ_SCOPE} {WRITE_SCOPE}",
        "iat": now,
        "exp": now + config.api.connect.token_ttl_seconds,
        "jti": "tok_" + secrets.token_hex(16),
    }
    header = {"alg": "HS256", "typ": "JWT"}
    signing_input = f"{_b64(json.dumps(header, separators=(',', ':')).encode())}.{_b64(json.dumps(payload, separators=(',', ':')).encode())}"
    signature = hmac.digest(
        _oauth_jwt_secret(config).encode("utf-8"),
        signing_input.encode("ascii"),
        "sha256",
    )
    return f"{signing_input}.{_b64(signature)}"


def _jwt_payload(token: str) -> dict[str, object]:
    parts = token.split(".")
    if len(parts) != 3:
        return {}
    try:
        payload = json.loads(_unb64(parts[1]))
    except (ValueError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _oauth_jwt_secret(config: HubConfig) -> str:
    return config.api.oauth.jwt_secret or os.environ.get(config.api.oauth.jwt_secret_env, "")


def _utc_after(seconds: int) -> str:
    return (datetime.now(UTC) + timedelta(seconds=seconds)).replace(microsecond=0).isoformat()


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _unb64(value: str) -> bytes:
    return base64.urlsafe_b64decode((value + "=" * (-len(value) % 4)).encode("ascii"))
