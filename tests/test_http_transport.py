"""Tests for the hardened HTTP transport: CORS restriction and rate limiting.

Exercises the Starlette middleware in build_http_app via Starlette's TestClient,
without starting uvicorn. The MCP route responses themselves are irrelevant here
— we assert only on the CORS and rate-limit behavior the middleware enforces.
"""

from __future__ import annotations

import pytest
from starlette.testclient import TestClient

from tastytrade_mcp.http_app import build_http_app
from tastytrade_mcp.server import build_server

ALLOWED = "http://localhost:3333"
DISALLOWED = "http://evil.example.com"
PUBLIC_HOST = "mcp.example.com"
TENANT_ID = "00000000-0000-0000-0000-000000000001"
APP_ID = "00000000-0000-0000-0000-000000000002"


@pytest.fixture
def client_factory(make_config):
    def _make(**overrides):
        base_url = overrides.pop("_base_url", "http://localhost")
        config = make_config(**overrides)
        app = build_http_app(build_server(config), config)
        return TestClient(app, base_url=base_url)

    return _make


def test_cors_preflight_allows_configured_origin(client_factory):
    with client_factory() as client:
        resp = client.options(
            "/mcp",
            headers={"Origin": ALLOWED, "Access-Control-Request-Method": "POST"},
        )
    assert resp.status_code == 200
    assert resp.headers.get("access-control-allow-origin") == ALLOWED


def test_cors_preflight_rejects_other_origin(client_factory):
    with client_factory() as client:
        resp = client.options(
            "/mcp",
            headers={"Origin": DISALLOWED, "Access-Control-Request-Method": "POST"},
        )
    # Starlette's CORSMiddleware returns 400 for a disallowed preflight origin.
    assert resp.status_code == 400
    assert resp.headers.get("access-control-allow-origin") != DISALLOWED


def test_rate_limit_returns_429_after_limit(client_factory):
    with client_factory(rate_limit="3/minute") as client:
        statuses = [client.get("/mcp").status_code for _ in range(6)]
    assert 429 in statuses
    # Once tripped it stays limited.
    assert statuses[-1] == 429
    # The first few (within the limit) were NOT rate-limited.
    assert statuses[0] != 429


def test_health_endpoints_are_public_and_not_rate_limited(client_factory):
    with client_factory(
        rate_limit="1/minute",
        require_azure_auth=True,
        public_host=PUBLIC_HOST,
        entra_tenant_id=TENANT_ID,
        mcp_api_app_id=APP_ID,
    ) as client:
        for _ in range(3):
            assert client.get("/healthz").json()["ok"] is True
        assert client.get("/").json()["service"] == "tastytrade-mcp"
        assert client.get("/ping").text == "pong\n"


def test_oauth_protected_resource_metadata(client_factory):
    with client_factory(
        require_azure_auth=True,
        public_host=PUBLIC_HOST,
        entra_tenant_id=TENANT_ID,
        mcp_api_app_id=APP_ID,
    ) as client:
        resp = client.get("/.well-known/oauth-protected-resource")
    assert resp.status_code == 200
    assert resp.json() == {
        "resource": f"https://{PUBLIC_HOST}/mcp",
        "authorization_servers": [
            f"https://login.microsoftonline.com/{TENANT_ID}/v2.0"
        ],
        "scopes_supported": [f"api://{APP_ID}/mcp.read"],
        "bearer_methods_supported": ["header"],
        "resource_name": "Tastytrade MCP",
    }


def test_azure_auth_rejects_missing_principal(client_factory):
    with client_factory(
        require_azure_auth=True,
        public_host=PUBLIC_HOST,
        entra_tenant_id=TENANT_ID,
        mcp_api_app_id=APP_ID,
    ) as client:
        resp = client.post("/mcp", json={})
    assert resp.status_code == 401
    assert "resource_metadata=" in resp.headers["www-authenticate"]


def test_azure_auth_allows_injected_principal(client_factory):
    with client_factory(
        _base_url=f"https://{PUBLIC_HOST}",
        require_azure_auth=True,
        public_host=PUBLIC_HOST,
        entra_tenant_id=TENANT_ID,
        mcp_api_app_id=APP_ID,
    ) as client:
        resp = client.post(
            "/mcp",
            json={},
            headers={"X-MS-CLIENT-PRINCIPAL-ID": "test-principal"},
        )
    assert resp.status_code not in (401, 421)


def test_transport_security_rejects_unknown_host(client_factory):
    with client_factory(
        _base_url="https://evil.example.com",
        require_azure_auth=True,
        public_host=PUBLIC_HOST,
        entra_tenant_id=TENANT_ID,
        mcp_api_app_id=APP_ID,
    ) as client:
        resp = client.post(
            "/mcp",
            json={},
            headers={"X-MS-CLIENT-PRINCIPAL-ID": "test-principal"},
        )
    assert resp.status_code == 421


def test_azure_auth_requires_complete_configuration(client_factory):
    with pytest.raises(ValueError, match="Azure authentication requires"):
        client_factory(require_azure_auth=True)
