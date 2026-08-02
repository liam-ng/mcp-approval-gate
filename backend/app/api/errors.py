from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.core.service import ServiceError
from app.repo.base import ConflictError, DuplicateError, NotFoundError


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(ServiceError)
    async def service_error_handler(_: Request, exc: ServiceError):
        return JSONResponse(
            status_code=exc.http_status,
            content={"error": {"code": exc.code, "message": str(exc)}},
        )

    @app.exception_handler(ConflictError)
    async def conflict_handler(_: Request, exc: ConflictError):
        return JSONResponse(
            status_code=409, content={"error": {"code": "CONFLICT", "message": str(exc)}}
        )

    @app.exception_handler(DuplicateError)
    async def duplicate_handler(_: Request, exc: DuplicateError):
        return JSONResponse(
            status_code=409, content={"error": {"code": "DUPLICATE", "message": str(exc)}}
        )

    @app.exception_handler(NotFoundError)
    async def notfound_handler(_: Request, exc: NotFoundError):
        return JSONResponse(
            status_code=404, content={"error": {"code": "NOT_FOUND", "message": str(exc)}}
        )
