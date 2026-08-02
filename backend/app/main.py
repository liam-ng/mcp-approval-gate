from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.api.errors import install_error_handlers
from app.api.middleware import install_middleware
from app.settings import get_settings

logging.basicConfig(level=logging.INFO)


def create_app() -> FastAPI:
    settings = get_settings()  # crashes at boot on invalid env

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        from app.jobs.expiry import run_expiry_loop
        from app.repo.factory import get_repository

        sweep = asyncio.create_task(
            run_expiry_loop(get_repository(), settings.approval_ttl_hours)
        )
        yield
        sweep.cancel()

    app = FastAPI(title="MCP Approval Gate", version="0.1.0", lifespan=lifespan)

    # Agent path (SigV4) — never uses sessions; human IdP changes don't touch it.
    from app.api.agent_tickets import router as agent_router

    app.include_router(agent_router)

    # Human path (OIDC session).
    from app.api import auth as human_auth
    from app.api.tickets import router as tickets_router

    human_auth.install(app, settings)
    app.include_router(tickets_router)

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


app = create_app()
