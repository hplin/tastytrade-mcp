"""Connection / health tools."""

from __future__ import annotations

import logging
from typing import Any

from tastytrade.account import Account

from .. import credentials
from ..config import Config
from ..session import get_session
from ._helpers import error_payload

logger = logging.getLogger(__name__)


def register(mcp, config: Config) -> None:
    @mcp.tool()
    async def get_connection_status() -> dict[str, Any]:
        """Check Tastytrade connectivity and report server configuration.

        Returns whether credentials are present, whether live trading is
        enabled, and how many accounts the session can see.
        """
        credential_store = credentials.credential_store_health()
        status: dict[str, Any] = {
            "ok": credential_store["broker_auth_usable"],
            "live_trading_enabled": config.enable_live_trading,
            "credentials_present": credential_store[
                "required_credentials_present"
            ],
            "credential_store": credential_store,
        }
        if not credential_store["broker_auth_usable"]:
            status["connected"] = False
            status["code"] = credential_store["error_code"]
            status["hint"] = (
                "Configure managed TASTYTRADE_CLIENT_SECRET and "
                "TASTYTRADE_REFRESH_TOKEN secrets, or run "
                "`tastytrade-mcp secrets set` with a working keyring."
            )
            return status
        try:
            session = get_session(config)
            accounts = await Account.get(session)
            status["connected"] = True
            status["account_count"] = len(accounts)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Connection check failed: %s", exc)
            status["ok"] = False
            status["connected"] = False
            status.update(error_payload(exc))
        return status
