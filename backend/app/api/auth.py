"""Human authentication: server-side OIDC (Authlib) with a signed httpOnly
session cookie. The SPA never touches tokens.

The provider is built entirely from env (OIDC_ISSUER/CLIENT_ID/SECRET/SCOPES/
GROUPS_CLAIM) — swapping IAM Identity Center for Azure AD / Entra ID is a
config change, zero code. AUTH_MODE=dev provides a local fake login and is
refused in production by settings validation.
"""

from __future__ import annotations

from dataclasses import dataclass

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import RedirectResponse
from starlette.middleware.sessions import SessionMiddleware

from app.auth.rbac import Role, resolve_role
from app.core.schemas import MeResponse
from app.settings import Settings, get_settings

_oauth = None  # lazily configured Authlib registry


@dataclass
class SessionUser:
    email: str
    name: str | None
    role: Role


def install(app: FastAPI, settings: Settings) -> None:
    app.add_middleware(
        SessionMiddleware,
        secret_key=settings.session_secret,
        same_site="lax",
        https_only=settings.env == "production",
    )

    if settings.auth_mode == "oidc":
        global _oauth
        from authlib.integrations.starlette_client import OAuth

        _oauth = OAuth()
        _oauth.register(
            name="sso",
            server_metadata_url=f"{settings.oidc_issuer.rstrip('/')}/.well-known/openid-configuration",
            client_id=settings.oidc_client_id,
            client_secret=settings.oidc_client_secret,
            client_kwargs={"scope": settings.oidc_scopes},
        )

        @app.get("/api/auth/login", include_in_schema=False)
        async def login(request: Request):
            redirect_uri = f"{settings.public_base_url.rstrip('/')}/api/auth/callback"
            return await _oauth.sso.authorize_redirect(request, redirect_uri)

        @app.get("/api/auth/callback", include_in_schema=False)
        async def callback(request: Request):
            token = await _oauth.sso.authorize_access_token(request)
            claims = token.get("userinfo") or {}
            email = claims.get("email") or claims.get("preferred_username")
            if not email:
                raise HTTPException(401, "IdP returned no email claim")
            groups = claims.get(settings.oidc_groups_claim) or []
            if isinstance(groups, str):
                groups = [groups]
            request.session["user"] = {
                "email": email,
                "name": claims.get("name"),
                "role": resolve_role(email, groups, settings),
            }
            return RedirectResponse("/")

    else:  # dev mode (blocked in production by settings validation)

        @app.get("/api/auth/login", include_in_schema=False)
        async def dev_login(request: Request, email: str = "dev@example.com", role: str = "approver"):
            if role not in ("approver", "viewer"):
                raise HTTPException(400, "role must be approver or viewer")
            request.session["user"] = {"email": email, "name": "Dev User", "role": role}
            return RedirectResponse("/")

    @app.get("/api/auth/logout", include_in_schema=False)
    async def logout(request: Request):
        request.session.clear()
        return RedirectResponse("/")

    @app.get("/api/me", response_model=MeResponse, response_model_by_alias=True)
    async def me(request: Request):
        user = require_session(request)
        return MeResponse(
            email=user.email,
            name=user.name,
            role=user.role,
            approval_ttl_hours=settings.approval_ttl_hours,
        )


def require_session(request: Request) -> SessionUser:
    data = request.session.get("user")
    if not data:
        raise HTTPException(
            status_code=401, detail={"code": "UNAUTHENTICATED", "message": "sign in required"}
        )
    return SessionUser(email=data["email"], name=data.get("name"), role=data["role"])


def require_approver(request: Request) -> SessionUser:
    user = require_session(request)
    if user.role != "approver":
        raise HTTPException(
            status_code=403,
            detail={"code": "NOT_APPROVER", "message": "approver role required"},
        )
    return user
