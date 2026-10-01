from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import keyring
import keyring.errors
import pytest
from tastytrade.account import Account

from tastytrade_mcp import session
from tastytrade_mcp.server import build_server
from tastytrade_mcp.tools import market


@pytest.fixture(autouse=True)
def _reset_session():
    session.reset_session()
    yield
    session.reset_session()


async def test_managed_container_broker_tools_do_not_require_keyring(
    monkeypatch,
    make_config,
    call_tool,
    fake_account,
):
    monkeypatch.setenv("TASTYTRADE_CLIENT_SECRET", "managed-client-secret")
    monkeypatch.setenv("TASTYTRADE_REFRESH_TOKEN", "managed-refresh-token")

    def _no_keyring(*_args, **_kwargs):
        raise keyring.errors.NoKeyringError

    monkeypatch.setattr(keyring, "get_password", _no_keyring)
    monkeypatch.setattr(session, "Session", lambda *_args, **_kwargs: SimpleNamespace())

    async def fake_get(_session, number=None):
        return fake_account if number else [fake_account]

    async def fake_metrics(_session, symbols):
        return [
            SimpleNamespace(symbol=symbol, implied_volatility_rank="0.45")
            for symbol in symbols
        ]

    monkeypatch.setattr(Account, "get", fake_get)
    monkeypatch.setattr(market, "get_market_metrics", fake_metrics)
    monkeypatch.setattr(
        fake_account,
        "get_positions",
        AsyncMock(return_value=[]),
    )
    monkeypatch.setattr(
        fake_account,
        "get_live_orders",
        AsyncMock(return_value=[]),
    )

    mcp = build_server(make_config(enable_live_trading=False))

    status = await call_tool(mcp, "get_connection_status")
    assert status["ok"] is True
    assert status["connected"] is True
    assert status["credential_store"]["status"] == "ready"
    assert (
        status["credential_store"]["credential_mode"]
        == "managed_environment"
    )
    assert status["credential_store"]["keyring_required"] is False

    accounts = await call_tool(mcp, "list_accounts")
    assert accounts["ok"] is True
    assert len(accounts["accounts"]) == 1

    info = await call_tool(mcp, "get_account_info")
    assert info["ok"] is True
    assert info["account_number"] == fake_account.account_number

    positions = await call_tool(mcp, "get_positions")
    assert positions["ok"] is True
    assert positions["positions"] == []

    orders = await call_tool(mcp, "get_working_orders")
    assert orders["ok"] is True
    assert orders["orders"] == []

    overview = await call_tool(
        mcp,
        "get_market_overview",
        {"symbols": ["SPY"]},
    )
    assert overview["ok"] is True


async def test_connection_status_reports_unusable_headless_backend(
    monkeypatch,
    make_config,
    call_tool,
):
    def _no_keyring(*_args, **_kwargs):
        raise keyring.errors.NoKeyringError

    monkeypatch.setattr(keyring, "get_password", _no_keyring)
    mcp = build_server(make_config(enable_live_trading=False))

    status = await call_tool(mcp, "get_connection_status")

    assert status["ok"] is False
    assert status["connected"] is False
    assert status["code"] == "KEYRING_BACKEND_UNAVAILABLE"
    assert status["credential_store"]["status"] == "unusable"
    assert status["credential_store"]["broker_auth_usable"] is False

    positions = await call_tool(mcp, "get_positions")
    assert positions["ok"] is False
    assert positions["code"] == "KEYRING_BACKEND_UNAVAILABLE"
