"""OAuth 2.1 Resource Server token verification for the /mcp route.

Cursor/VS Code run the full Authorization Code + PKCE flow directly against
the OIDC IdP (IAM Identity Center or Entra ID) themselves — see
docs/mcp-gateway.md for why the gate is never in that browser-redirect or
token-exchange path. This module only validates the bearer token the IDE
presents on every MCP call, by checking its signature and claims against the
IdP's published JWKS. No client secret or shared credential is involved on
this path.
"""

from __future__ import annotations

import time

import httpx
from authlib.jose import JsonWebKey, jwt
from authlib.jose.errors import JoseError
from mcp.server.auth.provider import AccessToken, TokenVerifier

from app.settings import Settings

_JWKS_TTL_SECONDS = 3600
_USERINFO_CACHE_TTL_SECONDS = 300


class _JwksCache:
    """Fetches and caches the IdP's signing keys (OIDC discovery -> JWKS)."""

    def __init__(self) -> None:
        self._key_set: JsonWebKey | None = None
        self._userinfo_endpoint: str | None = None
        self._fetched_at: float = 0.0

    async def get(self, issuer: str) -> tuple[JsonWebKey, str | None]:
        now = time.monotonic()
        if self._key_set is None or (now - self._fetched_at) > _JWKS_TTL_SECONDS:
            async with httpx.AsyncClient(timeout=10) as client:
                metadata = (
                    await client.get(f"{issuer.rstrip('/')}/.well-known/openid-configuration")
                ).json()
                jwks = (await client.get(metadata["jwks_uri"])).json()
            self._key_set = JsonWebKey.import_key_set(jwks)
            self._userinfo_endpoint = metadata.get("userinfo_endpoint")
            self._fetched_at = now
        return self._key_set, self._userinfo_endpoint


class _UserinfoEmailCache:
    """Caches token -> email lookups against the IdP's userinfo endpoint.

    Cognito access tokens never carry an `email` claim (only ID tokens do,
    and only the access token is ever sent as the MCP Bearer credential), so
    email has to be resolved out-of-band per token. Cached briefly so a
    client polling check_ticket_status doesn't hit userinfo on every call.
    """

    def __init__(self) -> None:
        self._entries: dict[str, tuple[str, float]] = {}

    async def get(self, userinfo_endpoint: str, token: str) -> str | None:
        now = time.monotonic()
        cached = self._entries.get(token)
        if cached and (now - cached[1]) <= _USERINFO_CACHE_TTL_SECONDS:
            return cached[0]
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.get(
                userinfo_endpoint, headers={"Authorization": f"Bearer {token}"}
            )
            response.raise_for_status()
            userinfo = response.json()
        email = userinfo.get("email") or userinfo.get("preferred_username")
        if email:
            self._entries[token] = (email, now)
        return email


class OidcTokenVerifier(TokenVerifier):
    """Verifies JWT access tokens issued by the human OIDC IdP against its JWKS."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._jwks = _JwksCache()
        self._userinfo_cache = _UserinfoEmailCache()

    async def verify_token(self, token: str) -> AccessToken | None:
        issuer = self._settings.mcp_issuer
        if not issuer:
            return None
        try:
            key_set, userinfo_endpoint = await self._jwks.get(issuer)
            claims = jwt.decode(token, key_set)
            claims.validate()  # exp / nbf / iat
        except (JoseError, httpx.HTTPError, KeyError, ValueError):
            return None

        if claims.get("iss") != issuer:
            return None

        audience = self._settings.mcp_oauth_audience
        if audience:
            # Cognito access tokens carry the app client id as `client_id`,
            # not `aud` (only ID tokens get `aud` on Cognito) — check both so
            # this stays a config-only swap across IdPs per CLAUDE.md.
            aud = claims.get("aud")
            aud_list = aud if isinstance(aud, list) else [aud] if aud else []
            aud_list.append(claims.get("client_id"))
            if audience not in aud_list:
                return None

        email = claims.get("email") or claims.get("preferred_username")
        if not email and userinfo_endpoint:
            try:
                email = await self._userinfo_cache.get(userinfo_endpoint, token)
            except (httpx.HTTPError, ValueError):
                email = None
        if not email:
            return None

        scope_claim = claims.get("scope") or claims.get("scp") or ""
        scopes = scope_claim.split() if isinstance(scope_claim, str) else list(scope_claim)

        return AccessToken(
            token=token,
            client_id=str(claims.get("client_id") or claims.get("azp") or claims.get("appid") or "mcp-client"),
            scopes=scopes,
            expires_at=int(claims["exp"]) if claims.get("exp") else None,
            resource=self._settings.mcp_resource_server_url,
            subject=email,
            claims=dict(claims),
        )
