"""Bearer auth + rate limiting for the public MCP endpoint.

Shared by both server entrypoints (``app/main.py`` and ``property_app``), so it
lives in ``property_core`` (the only package both images contain). Dependency
free: typed against plain ASGI dict/callables, no starlette import.

The MCP server is reachable on the public internet. Without this, anyone could
`tools/call` every tool with no credential and drive unbounded, expensive
outbound scraping (audit C1 / C1b). It provides:

  * Bearer-token auth, fail-closed: when ``MCP_API_KEY`` is set, ``/mcp``
    requires ``Authorization: Bearer <key>`` (constant-time compare). When it is
    unset the endpoint stays open, so deploys MUST set the key.
  * A per-client in-process rate limit as a backstop before the scraping
    fan-out.
"""
from __future__ import annotations

import hmac
import os
import time
from collections import defaultdict, deque
from typing import Any, Awaitable, Callable
from urllib.parse import parse_qs

Scope = dict
Receive = Callable[[], Awaitable[Any]]
Send = Callable[[dict], Awaitable[Any]]


def client_ip(scope: Scope) -> str:
    """Best-effort client IP, trusting only the proxy hops we control.

    ``X-Forwarded-For`` is appended-to on the right by each proxy; with
    ``TRUSTED_PROXY_HOPS`` hops the real client is that many entries from the
    end. Falls back to the socket peer.
    """
    hops = max(1, int(os.environ.get("TRUSTED_PROXY_HOPS", "1")))
    headers = dict(scope.get("headers", []))
    xff = headers.get(b"x-forwarded-for")
    if xff:
        parts = [p.strip() for p in xff.decode("latin-1").split(",") if p.strip()]
        if parts:
            idx = len(parts) - hops
            return parts[idx if idx >= 0 else 0]
    client = scope.get("client")
    return client[0] if client else "unknown"


class _SlidingWindowLimiter:
    """In-process per-key sliding-window limiter. Per-process is sufficient as a
    backstop in front of the heavy outbound scraping."""

    def __init__(self, limit: int, window_seconds: float) -> None:
        self.limit = limit
        self.window = window_seconds
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._since_sweep = 0

    def allow(self, key: str, now: float) -> bool:
        dq = self._hits[key]
        cutoff = now - self.window
        while dq and dq[0] < cutoff:
            dq.popleft()
        allowed = len(dq) < self.limit
        if allowed:
            dq.append(now)
        self._maybe_sweep(now)
        return allowed

    def _maybe_sweep(self, now: float) -> None:
        """Periodically drop idle keys so the dict can't grow one empty deque
        per distinct IP ever seen (a slow leak in a long-lived process)."""
        self._since_sweep += 1
        if self._since_sweep < 1000:
            return
        self._since_sweep = 0
        cutoff = now - self.window
        stale = [k for k, dq in self._hits.items() if not dq or dq[-1] < cutoff]
        for k in stale:
            del self._hits[k]


def _extract_token(scope: Scope) -> str:
    """Pull the API token from either the ``Authorization: Bearer`` header or a
    ``?key=`` / ``?api_key=`` query parameter.

    The property-agent app sends the header. Interactive MCP clients (the
    claude.ai connector, ChatGPT) can only configure OAuth or a plain URL, not a
    static header, so they pass the key in the connector URL instead. Trade-off:
    a URL-borne key appears in access logs - acceptable for a personal connector,
    and the key is rotatable.
    """
    headers = dict(scope.get("headers", []))
    auth = headers.get(b"authorization", b"").decode("latin-1")
    if auth[:7].lower() == "bearer ":
        return auth[7:]
    qs = scope.get("query_string", b"")
    if qs:
        params = parse_qs(qs.decode("latin-1"))
        for name in ("key", "api_key"):
            values = params.get(name)
            if values and values[0]:
                return values[0]
    return ""


async def _respond(send: Send, status: int, body: bytes) -> None:
    await send(
        {
            "type": "http.response.start",
            "status": status,
            "headers": [(b"content-type", b"text/plain; charset=utf-8")],
        }
    )
    await send({"type": "http.response.body", "body": body})


class McpGuard:
    """ASGI wrapper that enforces bearer auth + rate limiting on MCP traffic.

    Wrap the MCP handler with this; it guards every HTTP request it sees (the
    handler is only dispatched for ``/mcp``).
    """

    def __init__(self, app: Callable) -> None:
        self.app = app
        self.api_key = os.environ.get("MCP_API_KEY") or None
        limit = int(os.environ.get("MCP_RATE_LIMIT_PER_MIN", "120"))
        self.limiter = _SlidingWindowLimiter(limit, 60.0)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        # Auth FIRST: reject unauthenticated traffic at 401 before it can
        # consume a (possibly shared-IP) rate-limit budget.
        if self.api_key is not None:
            token = _extract_token(scope)
            if not token or not hmac.compare_digest(token, self.api_key):
                await _respond(send, 401, b"Unauthorized")
                return

        if not self.limiter.allow(client_ip(scope), time.monotonic()):
            await _respond(send, 429, b"Rate limit exceeded")
            return

        await self.app(scope, receive, send)


def require_metrics_auth(authorization: str | None) -> bool:
    """True if the request may read /metrics. When ``MCP_API_KEY`` is set,
    require it as a bearer token; when unset, allow (dev)."""
    key = os.environ.get("MCP_API_KEY") or None
    if key is None:
        return True
    if not authorization:
        return False
    token = authorization[7:] if authorization[:7].lower() == "bearer " else ""
    return bool(token) and hmac.compare_digest(token, key)


def forwarded_allow_ips() -> str:
    """Value for uvicorn's ``forwarded_allow_ips``. Defaults to the loopback /
    private-proxy default rather than ``"*"`` (which trusts proxy headers from
    anyone, audit C1). Override with ``FORWARDED_ALLOW_IPS`` to the real proxy
    address(es) in production."""
    return os.environ.get("FORWARDED_ALLOW_IPS", "127.0.0.1")
