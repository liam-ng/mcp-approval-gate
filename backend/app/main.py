from __future__ import annotations

import asyncio
import logging
from contextlib import AsyncExitStack, asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from starlette.applications import Starlette

from app.api.errors import install_error_handlers
from app.api.middleware import install_middleware
from app.settings import get_settings

logging.basicConfig(level=logging.INFO)


def create_app(mcp_app: Starlette | None = None) -> FastAPI:
    settings = get_settings()  # crashes at boot on invalid env

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        from app.jobs.expiry import run_expiry_loop
        from app.repo.factory import get_repository

        sweep = asyncio.create_task(
            run_expiry_loop(get_repository(), settings.approval_ttl_hours)
        )
        async with AsyncExitStack() as stack:
            if mcp_app is not None:
                # The MCP sub-app is dispatched to directly at the ASGI level
                # (see the bottom of this module), bypassing Starlette's own
                # lifespan protocol for it — so its session manager needs to
                # be started here instead, alongside the expiry sweep.
                await stack.enter_async_context(mcp_app.router.lifespan_context(mcp_app))
            yield
        sweep.cancel()

    app = FastAPI(title="MCP Approval Gate", version="0.1.0", lifespan=lifespan)

    # Agent path (SigV4) — never uses sessions; human IdP changes don't touch it.
    from app.api.agent_tickets import router as agent_router

    app.include_router(agent_router)

    # Human path (OIDC session).
    from app.api import auth as human_auth
    from app.api.tickets import router as tickets_router

    from app.api.aws_meta import router as aws_meta_router

    human_auth.install(app, settings)
    app.include_router(tickets_router)
    # Credential-free EC2 parameter metadata for the portal's create form.
    app.include_router(aws_meta_router)
    # Account lookups behind the same form's pickers. Mounted unconditionally —
    # the routes answer "not enabled" themselves rather than 404ing, so the SPA
    # gets the same shape either way and needs no build-time knowledge of it.
    from app.api.aws_discovery import router as aws_discovery_router

    app.include_router(aws_discovery_router)

    # Email-link path (signed token, no session) — see auth/approval_links.py.
    from app.api.approval_link_actions import router as approval_link_router

    app.include_router(approval_link_router)

    install_error_handlers(app)
    install_middleware(app)

    @app.get("/api/healthz")
    async def healthz():
        return {"status": "ok"}

    _mount_spa(app)
    return app


def _mount_spa(app: FastAPI) -> None:
    """Serve the built frontend (single-container deployment)."""
    dist = Path(__file__).resolve().parent.parent / "static"
    if not dist.exists():
        return
    app.mount("/assets", StaticFiles(directory=dist / "assets"), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    async def spa_fallback(path: str):
        candidate = dist / path
        if path and candidate.is_file():
            return FileResponse(candidate)
        return FileResponse(dist / "index.html")


_settings = get_settings()

if _settings.mcp_enabled:
    # The MCP SDK's Starlette app owns its own auth middleware stack (bearer
    # token verification + a contextvar populated for get_access_token()),
    # which only works if that app's own routing handles the request end to
    # end. Mounting it as a FastAPI sub-route would either lose that
    # middleware or, mounted at "/", shadow the SPA's catch-all fallback (see
    # docs/mcp-gateway.md). Instead, dispatch by exact path at the ASGI
    # level, above both routers, so each app only ever sees the requests it
    # owns — its lifespan is nested inside create_app()'s instead, since
    # only one of the two ever receives the ASGI "lifespan" scope below.
    from app.api.mcp_gateway import build_mcp_app

    _mcp_app = build_mcp_app(_settings)
    _fastapi_app = create_app(mcp_app=_mcp_app)
    _MCP_PATHS = ("/mcp", "/.well-known/oauth-protected-resource")

    async def app(scope, receive, send):  # noqa: N802 - ASGI callable, not a class
        if scope["type"] == "http" and scope["path"].startswith(_MCP_PATHS):
            await _mcp_app(scope, receive, send)
        else:
            await _fastapi_app(scope, receive, send)

else:
    app = create_app()
