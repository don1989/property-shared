from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from app.core.metrics import HTTPMetricsMiddleware, _route_label, _surface, setup_metrics


def _make_request(path: str, query_string: bytes = b"", route_path: str | None = None) -> Request:
    scope = {
        "type": "http",
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": path,
        "query_string": query_string,
        "headers": [],
        "client": ("testclient", 50000),
        "server": ("testserver", 80),
    }
    if route_path is not None:
        class _Route:
            path = route_path

        scope["route"] = _Route()
    return Request(scope)


def test_surface_classification():
    assert _surface("/v1/health") == "infra"
    assert _surface("/v1/report") == "api"
    assert _surface("/mcp") == "mcp_proxy"
    assert _surface("/mcp/messages") == "mcp_proxy"
    assert _surface("/metrics") == "infra"
    assert _surface("/") == "web"


def test_route_label_uses_template_and_not_querystring():
    request = _make_request("/v1/report/AB12", query_string=b"postcode=SW1A+1AA", route_path="/v1/report/{uprn}")
    assert _route_label(request, "api") == "/v1/report/{uprn}"


def test_route_label_fallbacks_are_low_cardinality():
    assert _route_label(_make_request("/v1/report/AB12"), "api") == "/v1/<unmatched>"
    assert _route_label(_make_request("/mcp/session/abc123"), "mcp_proxy") == "/mcp"
    assert _route_label(_make_request("/metrics"), "infra") == "/infra"
    assert _route_label(_make_request("/demo/random/xyz"), "web") == "/web"


def test_metrics_endpoint_returns_prometheus_text_and_metric_families():
    app = FastAPI()

    @app.get("/v1/health")
    async def health():
        return {"ok": True}

    setup_metrics(app)
    app.add_middleware(HTTPMetricsMiddleware)
    client = TestClient(app)

    client.get("/v1/health")
    response = client.get("/metrics")

    assert response.status_code == 200
    assert "text/plain" in response.headers["content-type"]
    assert "property_shared_http_requests_total" in response.text
    assert "property_shared_http_request_duration_seconds_bucket" in response.text


def test_metrics_scrapes_are_not_counted_as_app_traffic():
    app = FastAPI()

    @app.get("/")
    async def home():
        return {"ok": True}

    setup_metrics(app)
    app.add_middleware(HTTPMetricsMiddleware)
    client = TestClient(app)

    client.get("/")
    metrics_text = client.get("/metrics").text

    assert 'route="/metrics"' not in metrics_text


def test_metrics_use_fastapi_route_template_for_real_request():
    app = FastAPI()

    @app.get("/v1/report/{postcode}")
    async def report(postcode: str):
        return {"postcode": postcode}

    setup_metrics(app)
    app.add_middleware(HTTPMetricsMiddleware)

    client = TestClient(app)
    client.get("/v1/report/SW1A-1AA?secret=not-a-label")

    metrics_text = client.get("/metrics").text

    assert 'route="/v1/report/{postcode}"' in metrics_text
    assert "SW1A-1AA" not in metrics_text
    assert "secret=not-a-label" not in metrics_text
