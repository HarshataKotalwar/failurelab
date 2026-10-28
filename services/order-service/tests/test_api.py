import httpx
from fastapi.testclient import TestClient

from app.dependencies import get_http_client
from app.main import app


def client_for(handler):
    async_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    app.dependency_overrides[get_http_client] = lambda: async_client
    return TestClient(app)


def teardown_function() -> None:
    app.dependency_overrides.clear()


def test_injected_http_500_stops_before_downstream_calls() -> None:
    from app.main import controller

    called = False

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal called
        called = True
        return httpx.Response(200, json={"payment_id": "pay-1", "status": "reserved"})

    controller.activate("http_500")
    try:
        response = client_for(handler).post(
            "/orders", json={"product_id": "product-1", "quantity": 1}
        )
        assert response.status_code == 500
        assert called is False
        assert TestClient(app).get("/health").status_code == 500
    finally:
        controller.deactivate()


def test_health() -> None:
    response = TestClient(app).get("/health")
    assert response.json() == {"status": "ok", "service": "order-service"}
    assert len(response.headers["x-trace-id"]) == 32


def test_metrics_expose_prometheus_compatible_requests() -> None:
    client = TestClient(app)
    client.get("/health")
    response = client.get("/metrics")
    assert response.status_code == 200
    assert "failurelab_http_requests_total" in response.text
    assert 'service="failurelab-order-service"' in response.text


def test_metrics_count_http_5xx_responses() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/reserve":
            return httpx.Response(200, json={"status": "reserved"})
        return httpx.Response(503, json={"detail": "unavailable"})

    client_for(handler).post("/orders", json={"product_id": "product-1", "quantity": 1})
    metrics = TestClient(app).get("/metrics")
    assert any(
        line.startswith("failurelab_http_requests_total{")
        and all(
            label in line
            for label in (
                'service="failurelab-order-service"',
                'method="POST"',
                'route="/orders"',
                'status_code="502"',
            )
        )
        for line in metrics.text.splitlines()
    )


def test_successful_order() -> None:
    calls = []
    trace_headers = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        trace_headers.append(request.headers.get("traceparent"))
        if request.url.path == "/reserve":
            return httpx.Response(200, json={"status": "reserved"})
        return httpx.Response(200, json={"payment_id": "payment-1"})

    response = client_for(handler).post(
        "/orders",
        json={"product_id": "product-1", "quantity": 1},
        headers={"traceparent": "00-0123456789abcdef0123456789abcdef-0123456789abcdef-01"},
    )
    assert response.status_code == 201
    assert response.json()["status"] == "created"
    assert calls == ["/reserve", "/pay"]
    assert all(header is not None for header in trace_headers)
    assert all(
        header.split("-")[1] == "0123456789abcdef0123456789abcdef"
        for header in trace_headers
    )


def test_inventory_dependency_failure() -> None:
    response = client_for(
        lambda request: httpx.Response(409, json={"detail": "out of stock"})
    ).post("/orders", json={"product_id": "product-1", "quantity": 1})
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "inventory_rejected"


def test_payment_dependency_failure() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/reserve":
            return httpx.Response(200, json={"status": "reserved"})
        return httpx.Response(503, json={"detail": "unavailable"})

    response = client_for(handler).post("/orders", json={"product_id": "product-1", "quantity": 1})
    assert response.status_code == 502
    assert response.json()["detail"]["code"] == "payment_rejected"
