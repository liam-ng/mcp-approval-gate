"""Executor configuration. Validated at import so bad env crashes at boot."""

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # In-cluster address of the gate. Not the public URL: the executor never
    # leaves the cluster, and the SigV4 identity header must not traverse a
    # path the gate doesn't control.
    gate_base_url: str = "http://mcp-approval-gate-backend"

    # Must match the gate's GATE_SERVER_ID exactly. The gate rejects an
    # envelope whose signed X-Gate-Server-Id differs, which is what stops a
    # presigned request captured by one gate being replayed against another.
    gate_server_id: str

    # Region for the STS endpoint used to *sign* the identity envelope.
    # Must be us-east-1, not ca-central-1.
    sts_region: str = "us-east-1"

    poll_interval_seconds: float = Field(default=20.0, gt=0)
    # Jitter avoids a thundering herd if this is ever scaled past one replica
    # (which it should not be without distributed locking -- see main.py).
    poll_jitter_seconds: float = Field(default=5.0, ge=0)

    # Refuses to make any real AWS call; logs what it would have sent and
    # reports success with a synthetic request id. Defaults to ON: a scaffold
    # that mutates EC2 the first time it is applied is not a scaffold.
    dry_run: bool = True

    request_timeout_seconds: float = 30.0

    @model_validator(mode="after")
    def _check(self) -> "Settings":
        if not self.gate_base_url.startswith(("http://", "https://")):
            raise ValueError("GATE_BASE_URL must be an absolute http(s) URL")
        return self


settings = Settings()  # type: ignore[call-arg]
