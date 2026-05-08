from contextlib import asynccontextmanager
from typing import Any

import httpx
from fastapi import FastAPI
from starlette.types import ASGIApp, Receive, Scope, Send
import uvicorn

from app.api.routes import api_router
from app.core.config import get_settings
from app.core.logging import configure_logging
from app.web.routes import router as demo_router


# ---------------------------------------------------------------------------
# MCP proxy — forward /mcp to the canonical MCP server at propertydata.fly.dev
#
# Can't use app.mount("/mcp") — Starlette always 307-redirects /mcp → /mcp/
# and neither Claude.ai nor ChatGPT follow 307 for POST requests.
# Middleware routes /mcp directly without redirect.
# ---------------------------------------------------------------------------
_MCP_UPSTREAM = "https://uk-property-mcp.fly.dev"
_HOP_BY_HOP = frozenset([
    b"host", b"transfer-encoding", b"connection", b"keep-alive",
    b"proxy-authenticate", b"proxy-authorization", b"te", b"trailers", b"upgrade",
])


async def _mcp_proxy(scope: Scope, receive: Receive, send: Send) -> None:
    """Proxy /mcp requests to the canonical MCP server at propertydata.fly.dev."""
    path = scope["path"]
    query = scope.get("query_string", b"").decode()
    url = _MCP_UPSTREAM + path
    if query:
        url += f"?{query}"

    body = b""
    while True:
        message = await receive()
        body += message.get("body", b"")
        if not message.get("more_body", False):
            break

    headers = {k: v for k, v in scope.get("headers", []) if k.lower() not in _HOP_BY_HOP}

    async with httpx.AsyncClient() as client:
        async with client.stream(
            scope["method"], url, content=body, headers=headers, timeout=60.0
        ) as response:
            await send({
                "type": "http.response.start",
                "status": response.status_code,
                "headers": [
                    (k, v) for k, v in response.headers.raw
                    if k.lower() not in {b"transfer-encoding"}
                ],
            })
            async for chunk in response.aiter_bytes():
                await send({"type": "http.response.body", "body": chunk, "more_body": True})
            await send({"type": "http.response.body", "body": b"", "more_body": False})


class MCPMiddleware:
    """Route /mcp requests to the MCP proxy without Starlette mount redirect."""

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
    yield


def create_app() -> FastAPI:
    app_lifespan = lifespan
    settings = get_settings()
    app = FastAPI(
        title=settings.app_name,
        version="1.4.0",
        lifespan=app_lifespan,
    )
    app.include_router(api_router)
    app.include_router(demo_router)

    @app.get("/.well-known/glama.json", include_in_schema=False)
    async def glama_connector_manifest():
        return {
            "$schema": "https://glama.ai/mcp/schemas/connector.json",
            "maintainers": [{"email": "paul@bouch.dev"}],
        }

    from app.core.metrics import HTTPMetricsMiddleware, setup_metrics
    setup_metrics(app)

    app.add_middleware(MCPMiddleware, mcp_handler=_mcp_proxy)
    app.add_middleware(HTTPMetricsMiddleware)

    return app


app = create_app()


def run() -> None:
    """Entry point for property-api/property-demo scripts."""
    settings = get_settings()
    uvicorn.run("app.main:app", host=settings.host, port=settings.port, reload=False)
