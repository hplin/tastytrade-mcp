"""HTTP transport with CORS, rate limiting, health, and optional Azure auth.

Wraps FastMCP's streamable-HTTP ASGI app behind Starlette middleware:

- CORS is restricted to the single ``MCP_CORS_ORIGIN`` (default
  ``http://localhost:3333``).
- A per-IP rate limit (default 120 requests/minute) is applied to MCP
  endpoints, returning HTTP 429 when exceeded.
- Health and OAuth protected-resource metadata endpoints are public.
- When ``MCP_REQUIRE_AZURE_AUTH=true``, MCP requests require the principal
  header injected by Azure Container Apps authentication (Easy Auth).

The rate limiter is a small fixed-window per-IP counter implemented directly as
Starlette middleware. (slowapi's middleware introspects ``handler.__name__``,
which the MCP ASGI sub-app — a class instance — does not have, so it cannot be
used here.)
"""

from __future__ import annotations

import logging
import threading
import time

import uvicorn
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.middleware.cors import CORSMiddleware
from starlette.responses import JSONResponse, PlainTextResponse
from starlette.routing import Route

from .config import Config

logger = logging.getLogger(__name__)

_PERIOD_SECONDS = {"second": 1, "minute": 60, "hour": 3600, "day": 86400}


def parse_rate(rate: str) -> tuple[int, int]:
    """Parse a ``"<count>/<period>"`` string into (count, window_seconds).

    Accepts singular or plural periods, e.g. ``"120/minute"`` or ``"5/seconds"``.
    """
    count_str, period = rate.split("/")
    period = period.strip().lower().rstrip("s")
    if period not in _PERIOD_SECONDS:
        raise ValueError(f"Unknown rate-limit period: {period!r}")
    return int(count_str), _PERIOD_SECONDS[period]


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Fixed-window per-IP rate limit. Returns HTTP 429 when exceeded."""

    def __init__(self, app, limit: int, window_seconds: int):
        super().__init__(app)
        self.limit = limit
        self.window = window_seconds
        self._lock = threading.Lock()
        self._hits: dict[str, tuple[int, int]] = {}  # ip -> (window_index, count)

    async def dispatch(self, request, call_next):
        if not _is_mcp_path(request.url.path):
            return await call_next(request)
        ip = request.client.host if request.client else "unknown"
        window_index = int(time.monotonic() // self.window)
        with self._lock:
            w, count = self._hits.get(ip, (window_index, 0))
            if w != window_index:
                count = 0
            count += 1
            self._hits[ip] = (window_index, count)
        if count > self.limit:
            return JSONResponse(
                status_code=429,
                content={"ok": False, "error": "rate limit exceeded"},
            )
        return await call_next(request)


class AzureEasyAuthMiddleware(BaseHTTPMiddleware):
    """Require Azure Easy Auth's principal header on MCP requests."""

    def __init__(self, app, metadata_url: str, scope: str):
        super().__init__(app)
        self.challenge = (
            f'Bearer resource_metadata="{metadata_url}", scope="{scope}"'
        )

    async def dispatch(self, request, call_next):
        if (
            request.method != "OPTIONS"
            and _is_mcp_path(request.url.path)
            and not request.headers.get("x-ms-client-principal-id")
        ):
            return JSONResponse(
                status_code=401,
                content={"ok": False, "error": "authentication required"},
                headers={"WWW-Authenticate": self.challenge},
            )
        return await call_next(request)


def _is_mcp_path(path: str) -> bool:
    return path == "/mcp" or path.startswith("/mcp/")


def _public_base_url(host: str) -> str:
    value = host.strip().rstrip("/")
    if "://" not in value:
        value = f"https://{value}"
    return value


def _azure_auth_settings(config: Config) -> dict[str, str] | None:
    values = {
        "public_host": config.public_host,
        "tenant_id": config.entra_tenant_id,
        "app_id": config.mcp_api_app_id,
    }
    missing = [name for name, value in values.items() if not value]
    if missing:
        if config.require_azure_auth:
            raise ValueError(
                "Azure authentication requires: " + ", ".join(sorted(missing))
            )
        return None

    base_url = _public_base_url(config.public_host or "")
    scope = f"api://{config.mcp_api_app_id}/mcp.read"
    return {
        "resource": f"{base_url}/mcp",
        "authorization_server": (
            "https://login.microsoftonline.com/"
            f"{config.entra_tenant_id}/v2.0"
        ),
        "scope": scope,
        "metadata_url": f"{base_url}/.well-known/oauth-protected-resource",
    }


async def _health(_request):
    return JSONResponse({"ok": True, "service": "tastytrade-mcp"})


async def _ping(_request):
    return PlainTextResponse("pong\n")


def build_http_app(mcp, config: Config) -> Starlette:
    """Build the Starlette app exposing the MCP server over streamable HTTP."""
    limit, window = parse_rate(config.rate_limit)
    azure_auth = _azure_auth_settings(config)

    # FastMCP provides a ready-made streamable-HTTP ASGI sub-app.
    mcp_app = mcp.streamable_http_app()
    routes = [
        *mcp_app.routes,
        Route("/", _health),
        Route("/healthz", _health),
        Route("/ping", _ping),
    ]

    if azure_auth is not None:
        metadata = {
            "resource": azure_auth["resource"],
            "authorization_servers": [azure_auth["authorization_server"]],
            "scopes_supported": [azure_auth["scope"]],
            "bearer_methods_supported": ["header"],
            "resource_name": "Tastytrade MCP",
        }

        async def _oauth_metadata(_request):
            return JSONResponse(metadata)

        routes.extend(
            [
                Route(
                    "/.well-known/oauth-protected-resource",
                    _oauth_metadata,
                ),
                Route(
                    "/.well-known/oauth-protected-resource/mcp",
                    _oauth_metadata,
                ),
            ]
        )

    middleware = [
        # CORS outermost so even a 429 carries the right CORS headers.
        Middleware(
            CORSMiddleware,
            allow_origins=[config.cors_origin],
            allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
            allow_headers=["*"],
            allow_credentials=True,
        ),
    ]
    if config.require_azure_auth and azure_auth is not None:
        middleware.append(
            Middleware(
                AzureEasyAuthMiddleware,
                metadata_url=azure_auth["metadata_url"],
                scope=azure_auth["scope"],
            )
        )
    middleware.append(
        Middleware(RateLimitMiddleware, limit=limit, window_seconds=window)
    )

    return Starlette(
        routes=routes,
        lifespan=getattr(mcp_app.router, "lifespan_context", None),
        middleware=middleware,
    )


def run_http(mcp, config: Config) -> None:  # pragma: no cover - uvicorn launcher
    app = build_http_app(mcp, config)
    logger.info(
        "Serving MCP over HTTP at http://%s:%s (CORS origin=%s, rate limit=%s)",
        config.http_host,
        config.http_port,
        config.cors_origin,
        config.rate_limit,
    )
    uvicorn.run(
        app,
        host=config.http_host,
        port=config.http_port,
        log_level=config.log_level.lower(),
    )
