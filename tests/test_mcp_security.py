"""MCP bearer auth + rate limit guard tests (audit C1 / C1b)."""
import asyncio

from property_core.mcp_security import (
    McpGuard,
    client_ip,
    require_metrics_auth,
)


class _Recorder:
    """Minimal ASGI app that records that it ran, and a send() collector."""

    def __init__(self):
        self.ran = False

    async def __call__(self, scope, receive, send):
        self.ran = True
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})


def _scope(headers=None, client=("203.0.113.9", 1234)):
    return {"type": "http", "path": "/mcp", "headers": headers or [], "client": client}


async def _drive(guard, scope):
    sent = []

    async def receive():
        return {"type": "http.request", "body": b""}

    async def send(msg):
        sent.append(msg)

    await guard(scope, receive, send)
    return sent


def _status(sent):
    for m in sent:
        if m["type"] == "http.response.start":
            return m["status"]
    return None


def test_open_when_no_key_set(monkeypatch):
    monkeypatch.delenv("MCP_API_KEY", raising=False)
    app = _Recorder()
    guard = McpGuard(app)
    sent = asyncio.run(_drive(guard, _scope()))
    assert app.ran is True
    assert _status(sent) == 200


def test_rejects_missing_bearer_when_key_set(monkeypatch):
    monkeypatch.setenv("MCP_API_KEY", "s3cret")
    app = _Recorder()
    guard = McpGuard(app)
    sent = asyncio.run(_drive(guard, _scope()))
    assert app.ran is False
    assert _status(sent) == 401


def test_accepts_correct_bearer(monkeypatch):
    monkeypatch.setenv("MCP_API_KEY", "s3cret")
    app = _Recorder()
    guard = McpGuard(app)
    headers = [(b"authorization", b"Bearer s3cret")]
    sent = asyncio.run(_drive(guard, _scope(headers)))
    assert app.ran is True
    assert _status(sent) == 200


def test_rejects_wrong_bearer(monkeypatch):
    monkeypatch.setenv("MCP_API_KEY", "s3cret")
    app = _Recorder()
    guard = McpGuard(app)
    headers = [(b"authorization", b"Bearer nope")]
    sent = asyncio.run(_drive(guard, _scope(headers)))
    assert app.ran is False
    assert _status(sent) == 401


def test_rate_limit_blocks_after_cap(monkeypatch):
    monkeypatch.delenv("MCP_API_KEY", raising=False)
    monkeypatch.setenv("MCP_RATE_LIMIT_PER_MIN", "2")
    guard = McpGuard(_Recorder())
    statuses = [_status(asyncio.run(_drive(guard, _scope()))) for _ in range(3)]
    assert statuses == [200, 200, 429]


def test_client_ip_uses_rightmost_trusted_hop(monkeypatch):
    monkeypatch.setenv("TRUSTED_PROXY_HOPS", "1")
    headers = [(b"x-forwarded-for", b"1.2.3.4, 9.9.9.9")]
    assert client_ip(_scope(headers)) == "9.9.9.9"


def test_metrics_auth(monkeypatch):
    monkeypatch.setenv("MCP_API_KEY", "s3cret")
    assert require_metrics_auth("Bearer s3cret") is True
    assert require_metrics_auth("Bearer wrong") is False
    assert require_metrics_auth(None) is False
    monkeypatch.delenv("MCP_API_KEY", raising=False)
    assert require_metrics_auth(None) is True
