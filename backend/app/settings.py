"""Application settings, validated at import time.

Every deployment knob lives here. Invalid or missing configuration crashes the
process at startup (before readiness passes) rather than failing on the first
request. Human-auth (OIDC_*) and agent-auth (GATE_*, ALLOWED_AGENT_ARNS)
settings are intentionally independent so swapping the human IdP (e.g. IAM
Identity Center -> Entra ID) never touches the agent path.
"""

from __future__ import annotations

from typing import Literal

from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

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
        return self

    @property
    def approver_group_list(self) -> list[str]:
        return [g.strip() for g in self.oidc_approver_groups.split(",") if g.strip()]

    @property
    def approver_email_list(self) -> list[str]:
        return [e.strip().lower() for e in self.approver_emails.split(",") if e.strip()]

    @property
    def allowed_agent_arn_globs(self) -> list[str]:
        return [a.strip() for a in self.allowed_agent_arns.split(",") if a.strip()]


_settings: Settings | None = None


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings()  # type: ignore[call-arg]
    return _settings
