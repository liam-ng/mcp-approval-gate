"""Application settings, validated at import time.

Every deployment knob lives here. Invalid or missing configuration crashes the
process at startup (before readiness passes) rather than failing on the first
request. Human-auth (OIDC_*) and agent-auth (GATE_*, ALLOWED_AGENT_ARNS)
settings are intentionally independent so swapping the human IdP (e.g. IAM
Identity Center -> Entra ID) never touches the agent path.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Literal

from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


def _default_env_file() -> str:
    """ENV_FILE, if set, wins outright (e.g. `ENV_FILE=.env.liam-mcp uvicorn ...`).
    Otherwise pick the first of .env.prod, .env.liam-dev, .env that exists."""
    override = os.environ.get("ENV_FILE")
    if override:
        return override
    for candidate in (".env.prod", ".env.liam-dev", ".env"):
        if Path(candidate).is_file():
            return candidate
    return ".env"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=_default_env_file(), extra="ignore")

    # --- Human auth (provider-agnostic OIDC) ---
    # "dev" bypasses OIDC with a fake local user; refused when ENV=production.
    auth_mode: Literal["oidc", "dev"] = "oidc"
    env: Literal["development", "production"] = "development"
    oidc_issuer: str | None = None            # discovery URL base
    oidc_client_id: str | None = None
    oidc_client_secret: str | None = None
    oidc_scopes: str = "openid profile email"
    oidc_groups_claim: str = "groups"
    oidc_approver_groups: str = ""            # comma-separated group names
    approver_emails: str = ""                 # comma-separated fallback allowlist
    session_secret: str
    public_base_url: str = "http://localhost:8000"

    # --- Agent auth (IAM SigV4 via presigned sts:GetCallerIdentity) ---
    gate_server_id: str
    allowed_agent_arns: str                   # comma-separated globs, e.g. arn:aws:iam::123*:role/mcp-*

    # --- Workflow ---
    required_approvals: int = 1
    approval_ttl_hours: int = 72
    # Off by default: the four-eyes principle ("approver != proposer",
    # core/service.py's _assert_actionable_by) is a deliberate invariant, not
    # an accident. Only relevant when a *human* is the proposer -- MCP-created
    # and supersede/edit tickets -- since agent-created tickets always have
    # proposed_by = the agent's own ARN, which can never match a human email.
    # Intended for small/solo deployments where a second human approver
    # genuinely doesn't exist, not as a routine bypass.
    allow_self_approval: bool = False

    # --- Storage ---
    store_backend: Literal["jsonl", "s3", "dynamodb"] = "jsonl"
    data_dir: str = "./data"
    dynamodb_table: str | None = None
    s3_bucket: str | None = None
    audit_mirror_s3_bucket: str | None = None

    # --- Notifications (SES) ---
    notify_on_create: bool = False
    ses_from_address: str | None = None
    ses_region: str | None = None

    # --- MCP gateway (IDE clients — Cursor / VS Code — connect here; see
    # docs/mcp-gateway.md). The gate is an OAuth 2.1 Resource Server only: it
    # never runs the browser auth-code flow itself, it just verifies the
    # bearer token the IDE already obtained from the OIDC IdP. Defaults to
    # the same issuer as human auth (a second, public/native OAuth client
    # registered there for Cursor/VSCode) — override MCP_OAUTH_ISSUER only if
    # IDE clients must authenticate against a different AS. ---
    mcp_enabled: bool = False
    mcp_oauth_issuer: str | None = None
    mcp_oauth_audience: str | None = None     # expected `aud` claim; strongly recommended
    mcp_required_scopes: str = ""             # space-separated; empty = no scope requirement
    # The trusted automation identity that executes MCP-created tickets once
    # approved, via the existing SigV4 agent contract. Must also appear in
    # ALLOWED_AGENT_ARNS. Never the human's own credentials.
    mcp_executor_arn: str | None = None

    # --- Read-only AWS discovery for the portal's create form (app/aws/) ---
    # OFF BY DEFAULT, and that default is the safe one: with this unset the gate
    # holds no AWS credentials at all, which is the posture CLAUDE.md's "the
    # gate needs no AWS permissions" invariant describes. Turning it on trades
    # that for real subnet/AMI/security-group pickers instead of typed-in ids.
    # The identity here must be a SEPARATE, Describe-only role — never the
    # executor's, whose whole point is that only the executor holds it.
    aws_discovery_enabled: bool = False
    aws_discovery_role_arn: str | None = None   # unset = use the ambient chain (IRSA)
    aws_discovery_default_region: str = "ca-central-1"
    aws_discovery_cache_seconds: int = 300

    @field_validator("required_approvals")
    @classmethod
    def _approvals_range(cls, v: int) -> int:
        if not 1 <= v <= 2:
            raise ValueError("REQUIRED_APPROVALS must be 1 or 2")
        return v

    @model_validator(mode="after")
    def _cross_checks(self) -> "Settings":
        if self.auth_mode == "dev" and self.env == "production":
            raise ValueError("AUTH_MODE=dev is not allowed when ENV=production")
        if self.auth_mode == "oidc" and not (
            self.oidc_issuer and self.oidc_client_id and self.oidc_client_secret
        ):
            raise ValueError("AUTH_MODE=oidc requires OIDC_ISSUER, OIDC_CLIENT_ID and OIDC_CLIENT_SECRET")
        if self.store_backend == "dynamodb" and not self.dynamodb_table:
            raise ValueError("STORE_BACKEND=dynamodb requires DYNAMODB_TABLE")
        if self.store_backend == "s3" and not self.s3_bucket:
            raise ValueError("STORE_BACKEND=s3 requires S3_BUCKET")
        if self.notify_on_create and not (self.ses_from_address and self.ses_region):
            raise ValueError("NOTIFY_ON_CREATE requires SES_FROM_ADDRESS and SES_REGION")
        if self.mcp_enabled and not (self.mcp_issuer and self.mcp_executor_arn):
            raise ValueError(
                "MCP_ENABLED requires an OIDC issuer (OIDC_ISSUER or MCP_OAUTH_ISSUER) and MCP_EXECUTOR_ARN"
            )
        return self

    @property
    def executor_arn(self) -> str | None:
        """The identity that executes human-proposed tickets.

        One setting, two consumers: the /mcp gateway and the portal's create
        form. Named after MCP only for historical reasons — a second env var
        holding the same ARN would be a second thing to get wrong, and a
        mismatch between them fails at execution time, long after the mistake.
        """
        return self.mcp_executor_arn

    @property
    def approver_group_list(self) -> list[str]:
        return [g.strip() for g in self.oidc_approver_groups.split(",") if g.strip()]

    @property
    def approver_email_list(self) -> list[str]:
        return [e.strip().lower() for e in self.approver_emails.split(",") if e.strip()]

    @property
    def allowed_agent_arn_globs(self) -> list[str]:
        return [a.strip() for a in self.allowed_agent_arns.split(",") if a.strip()]

    @property
    def mcp_issuer(self) -> str | None:
        return self.mcp_oauth_issuer or self.oidc_issuer

    @property
    def mcp_resource_server_url(self) -> str:
        return f"{self.public_base_url.rstrip('/')}/mcp"

    @property
    def mcp_required_scope_list(self) -> list[str]:
        return [s for s in self.mcp_required_scopes.split() if s]


_settings: Settings | None = None


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings()  # type: ignore[call-arg]
    return _settings
