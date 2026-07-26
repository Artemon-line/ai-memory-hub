from __future__ import annotations

import logging
from pathlib import Path
from urllib.parse import parse_qs

from fastapi import FastAPI, HTTPException, Request
from starlette.middleware.sessions import SessionMiddleware
from starlette.responses import RedirectResponse, Response
from starlette.staticfiles import StaticFiles
from starlette.templating import Jinja2Templates

from memory.api.connect_oauth import ConnectOAuthRegistry
from memory.api.connect_service import (
    ConnectService,
    secure_cookie,
    session_secret,
)
from memory.api.connect_service import (
    connect_status as _connect_status,
)
from memory.api.oauth_authorization import (
    pending_authorization_redirect,
    register_oauth_authorization_routes,
)
from memory.config import HubConfig
from memory.ingestion.base_agent import BaseIngestionAgent

logger = logging.getLogger(__name__)

CONNECT_UI_ROOT = Path(__file__).resolve().parents[1] / "ui" / "connect"
templates = Jinja2Templates(directory=str(CONNECT_UI_ROOT / "templates"))


def connect_status(config: HubConfig) -> dict[str, object]:
    return _connect_status(config)


def register_connect_routes(app: FastAPI, *, agent: BaseIngestionAgent, config: HubConfig) -> None:
    if not config.api.connect.enabled:
        return

    service = ConnectService(agent=agent, config=config)
    oauth = ConnectOAuthRegistry(config)
    app.add_middleware(
        SessionMiddleware,
        secret_key=session_secret(config),
        session_cookie="amh_oauth_session",
        max_age=600,
        same_site="lax",
        https_only=secure_cookie(config),
    )
    static_dir = CONNECT_UI_ROOT / "static"
    if static_dir.exists():
        app.mount("/connect/static", StaticFiles(directory=str(static_dir)), name="connect-static")
    register_oauth_authorization_routes(app, service=service, oauth=oauth, config=config)

    @app.get("/", include_in_schema=False)
    async def root() -> Response:
        return RedirectResponse("/connect", status_code=307)

    @app.get("/connect", include_in_schema=False)
    async def connect(request: Request) -> Response:
        return templates.TemplateResponse(
            request,
            "connect.html.j2",
            await service.page_model(request),
        )

    @app.get("/auth/{provider}", include_in_schema=False)
    async def auth_provider(provider: str, request: Request) -> Response:
        return await oauth.authorize_redirect(request, provider=provider)

    @app.get("/auth/{provider}/callback", include_in_schema=False)
    async def auth_provider_callback(provider: str, request: Request) -> Response:
        if request.query_params.get("error"):
            logger.info(
                "OAuth login denied",
                extra={"event": "connect_login_denied", "provider": provider},
            )
            raise HTTPException(status_code=403, detail="OAuth login was denied")
        claims = await oauth.callback_claims(request, provider=provider)
        pending_mcp_authorization = _has_pending_mcp_authorization(request)
        login = await service.complete_login(
            provider=provider,
            claims=claims,
            issue_token=not pending_mcp_authorization,
        )
        logger.info(
            "OAuth login succeeded",
            extra={
                "event": "connect_login_success",
                "provider": provider,
                "owner_id": login["identity"]["user_id"],
            },
        )
        authorization_redirect = pending_authorization_redirect(
            request, owner_id=str(login["identity"]["user_id"])
        )
        if authorization_redirect is not None:
            authorization_redirect.set_cookie(
                config.api.connect.session_cookie_name,
                str(login["session_id"]),
                httponly=True,
                secure=secure_cookie(config),
                samesite="lax",
                max_age=config.api.connect.session_ttl_seconds,
            )
            return authorization_redirect
        response = templates.TemplateResponse(
            request,
            "connect.html.j2",
            await service.page_model(
                request,
                signed_in=login["identity"],
                issued_token=str(login["issued_token"]),
                csrf_token=str(login["csrf_token"]),
            ),
        )
        response.set_cookie(
            config.api.connect.session_cookie_name,
            str(login["session_id"]),
            httponly=True,
            secure=secure_cookie(config),
            samesite="lax",
            max_age=config.api.connect.session_ttl_seconds,
        )
        issued_token_id = str(login["issued_token_id"])
        if issued_token_id:
            response.set_cookie(
                "amh_token_id",
                issued_token_id,
                httponly=True,
                secure=secure_cookie(config),
                samesite="lax",
                max_age=config.api.connect.token_ttl_seconds,
            )
        response.set_cookie(
            "amh_csrf",
            str(login["csrf_token"]),
            httponly=False,
            secure=secure_cookie(config),
            samesite="lax",
            max_age=config.api.connect.session_ttl_seconds,
        )
        return response

    @app.post("/auth/logout", include_in_schema=False)
    async def auth_logout(request: Request) -> Response:
        form = parse_qs((await request.body()).decode("utf-8"))
        csrf_token = str((form.get("csrf_token") or [""])[0])
        await service.logout(request, csrf_token=csrf_token)
        response = RedirectResponse("/connect", status_code=303)
        response.delete_cookie(config.api.connect.session_cookie_name)
        response.delete_cookie("amh_csrf")
        response.delete_cookie("amh_token_id")
        logger.info("Connect UI logout", extra={"event": "connect_logout"})
        return response


def _has_pending_mcp_authorization(request: Request) -> bool:
    state_data = getattr(request.state, "oauth_provider_state_data", None)
    if isinstance(state_data, dict) and isinstance(
        state_data.get("pending_oauth_authorization"), dict
    ):
        return True
    try:
        return isinstance(request.session.get("pending_oauth_authorization"), dict)
    except AssertionError:
        return False
