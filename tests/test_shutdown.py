from types import SimpleNamespace

from tastytrade_mcp import server, session


def test_run_handles_ctrl_c_gracefully(monkeypatch, make_config):
    """A KeyboardInterrupt from the transport should not propagate, and the
    session must be closed on the way out."""
    closed = {"called": False}

    class FakeMCP:
        def run(self):
            raise KeyboardInterrupt()

    monkeypatch.setattr(server, "build_server", lambda config: FakeMCP())
    monkeypatch.setattr(
        server, "close_session", lambda: closed.__setitem__("called", True)
    )

    # Should return cleanly, not raise.
    server.run(transport="stdio", config=make_config(enable_live_trading=False))
    assert closed["called"]


def test_run_validates_credential_store_on_startup(monkeypatch, make_config):
    checked = {"called": False}

    class FakeMCP:
        def run(self):
            raise KeyboardInterrupt()

    def fake_health():
        checked["called"] = True
        return {
            "status": "unusable",
            "broker_auth_usable": False,
            "required_credentials_present": False,
            "missing_required": ["client_secret", "refresh_token"],
            "sources": {},
            "credential_mode": "keyring_or_mixed",
            "keyring_backend": "fail.Keyring",
            "keyring_available": False,
            "keyring_required": True,
            "error_code": "KEYRING_BACKEND_UNAVAILABLE",
        }

    monkeypatch.setattr(server, "build_server", lambda config: FakeMCP())
    monkeypatch.setattr(server.credentials, "credential_store_health", fake_health)

    server.run(transport="stdio", config=make_config(enable_live_trading=False))

    assert checked["called"] is True


def test_close_session_closes_http_client():
    session.reset_session()
    closed = {"called": False}
    fake = SimpleNamespace(
        sync_client=SimpleNamespace(
            close=lambda: closed.__setitem__("called", True)
        )
    )
    # Inject a cached session directly.
    session._session = fake
    session._session_sandbox = True

    session.close_session()
    assert closed["called"]
    assert session._session is None
