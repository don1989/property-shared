from contextlib import asynccontextmanager
from importlib.metadata import version as _pkg_version
from typing import Any

from fastapi import FastAPI
from starlette.types import ASGIApp, Receive, Scope, Send
import uvicorn

from app.api.routes import api_router
from app.core.config import get_settings
from app.core.logging import configure_logging
from app.web.routes import router as demo_router
from app.core.middleware import _AcceptNormalizer
from app.mcp.server import build_asgi_app, _http_app as _mcp_http_app


# Can't use app.mount("/mcp") — Starlette always 307-redirects /mcp → /mcp/
# and neither Claude.ai nor ChatGPT follow 307 for POST requests.
# Middleware routes /mcp directly without redirect.
_local_mcp_app = _AcceptNormalizer(build_asgi_app())


class MCPMiddleware:
    """Route /mcp requests to the local FastMCP app without Starlette mount redirect."""

    def __init__(self, app: ASGIApp, mcp_handler: Any) -> None:
        self.app = app
        self.mcp_handler = mcp_handler

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        path = scope.get("path", "")
        if scope["type"] == "http" and (path == "/mcp" or path.startswith("/mcp/")):
            await self.mcp_handler(scope, receive, send)
        else:
            await self.app(scope, receive, send)


# ---------------------------------------------------------------------------
# App lifecycle
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    _ = get_settings()
    configure_logging()
    async with _mcp_http_app.lifespan(app):
        yield


def create_app() -> FastAPI:
    app_lifespan = lifespan
    settings = get_settings()
    app = FastAPI(
        title=settings.app_name,
        version=_pkg_version("property-shared"),
        lifespan=app_lifespan,
    )
    app.include_router(api_router)
    app.include_router(demo_router)

    @app.get("/health", include_in_schema=False)
    async def health():
        return {"status": "ok"}

    @app.get("/.well-known/glama.json", include_in_schema=False)
    async def glama_connector_manifest():
        return {
            "$schema": "https://glama.ai/mcp/schemas/connector.json",
            "maintainers": [{"email": "paul@bouch.dev"}],
        }

    @app.get("/.well-known/mcp/server-card.json", include_in_schema=False)
    async def server_card():
        return {"serverInfo": {"name": "property-data", "version": _pkg_version("property-shared")}}

    from app.core.metrics import HTTPMetricsMiddleware, setup_metrics
    setup_metrics(app)

    app.add_middleware(MCPMiddleware, mcp_handler=_local_mcp_app)
    app.add_middleware(HTTPMetricsMiddleware)

    return app


app = create_app()


def run() -> None:
    """Entry point for property-api/property-demo scripts."""
    settings = get_settings()
    uvicorn.run("app.main:app", host=settings.host, port=settings.port, reload=False)
