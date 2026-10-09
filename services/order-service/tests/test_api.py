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


def test_health() -> None:
    assert TestClient(app).get("/health").json() == {"status": "ok", "service": "order-service"}


def test_successful_order() -> None:
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        if request.url.path == "/reserve":
            return httpx.Response(200, json={"status": "reserved"})
        return httpx.Response(200, json={"payment_id": "payment-1"})

    response = client_for(handler).post("/orders", json={"product_id": "product-1", "quantity": 1})
    assert response.status_code == 201
    assert response.json()["status"] == "created"
    assert calls == ["/reserve", "/pay"]


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
