"""MCP auth + rate limiting for the FastAPI server.

The implementation is shared with the ``property_app`` entrypoint and lives in
``property_core.mcp_security`` (the only package both Docker images contain).
This module just re-exports it so ``app`` code imports stay local.
"""
from __future__ import annotations

from property_core.mcp_security import (  # noqa: F401
    McpGuard,
    client_ip,
    forwarded_allow_ips,
    require_metrics_auth,
)

__all__ = ["McpGuard", "client_ip", "forwarded_allow_ips", "require_metrics_auth"]
