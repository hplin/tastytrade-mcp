"""Runtime configuration loaded from environment / .env (non-secret only).

Only allowlisted non-secret settings are loaded from ``.env``. Managed container
secret variables are read directly from the process environment by
:mod:`tastytrade_mcp.credentials`, never from ``.env``.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import dotenv_values

_DOTENV_KEYS = {
    "ENABLE_LIVE_TRADING",
    "FORCE_DRY_RUN",
    "BUYING_POWER_BUFFER_PCT",
    "ACCOUNT_DEPLOY_LIMIT_PCT",
    "TASTYTRADE_MCP_LOG_LEVEL",
    "MCP_CORS_ORIGIN",
    "MCP_RATE_LIMIT",
    "MCP_HTTP_HOST",
    "MCP_HTTP_PORT",
    "MCP_REQUIRE_AZURE_AUTH",
    "MCP_PUBLIC_HOST",
    "ENTRA_TENANT_ID",
    "MCP_API_APP_ID",
}


def _load_non_secret_dotenv(path: str | os.PathLike[str] = ".env") -> None:
    """Load only supported non-secret keys; real environment values win."""
    for key, value in dotenv_values(path).items():
        if key in _DOTENV_KEYS and value is not None:
            os.environ.setdefault(key, value)


_load_non_secret_dotenv()


def _as_bool(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Config:
    """Resolved server configuration."""

    enable_live_trading: bool
    force_dry_run: bool
    buying_power_buffer_pct: float
    account_deploy_limit_pct: float
    log_level: str

    # HTTP transport
    cors_origin: str
    rate_limit: str
    http_host: str
    http_port: int
    require_azure_auth: bool
    public_host: str | None
    entra_tenant_id: str | None
    mcp_api_app_id: str | None

    @classmethod
    def from_env(cls) -> "Config":
        return cls(
            enable_live_trading=_as_bool(
                os.getenv("ENABLE_LIVE_TRADING"), default=False
            ),
            force_dry_run=_as_bool(os.getenv("FORCE_DRY_RUN"), default=False),
            buying_power_buffer_pct=float(
                os.getenv("BUYING_POWER_BUFFER_PCT", "0")
            ),
            account_deploy_limit_pct=float(
                os.getenv("ACCOUNT_DEPLOY_LIMIT_PCT", "0")
            ),
            log_level=os.getenv("TASTYTRADE_MCP_LOG_LEVEL", "INFO").upper(),
            cors_origin=os.getenv("MCP_CORS_ORIGIN", "http://localhost:3333"),
            rate_limit=os.getenv("MCP_RATE_LIMIT", "120/minute"),
            http_host=os.getenv("MCP_HTTP_HOST", "127.0.0.1"),
            http_port=int(os.getenv("MCP_HTTP_PORT", "7698")),
            require_azure_auth=_as_bool(
                os.getenv("MCP_REQUIRE_AZURE_AUTH"), default=False
            ),
            public_host=os.getenv("MCP_PUBLIC_HOST") or None,
            entra_tenant_id=os.getenv("ENTRA_TENANT_ID") or None,
            mcp_api_app_id=os.getenv("MCP_API_APP_ID") or None,
        )


def get_config() -> Config:
    """Return freshly-resolved configuration."""
    return Config.from_env()
