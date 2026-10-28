from uuid import uuid4

from fastapi.testclient import TestClient

from app.main import app, inventory

client = TestClient(app)


def setup_function() -> None:
    inventory.clear()
    inventory.update({"product-1": 100})


def test_health() -> None:
    response = client.get("/health")
    assert response.json() == {"status": "ok", "service": "inventory-service"}
    assert len(response.headers["x-trace-id"]) == 32


def test_metrics_expose_prometheus_compatible_requests() -> None:
    client.get("/health")
    response = client.get("/metrics")
    assert response.status_code == 200
    assert "failurelab_http_requests_total" in response.text
    assert 'service="failurelab-inventory-service"' in response.text


def test_successful_reservation() -> None:
    response = client.post("/reserve", json={"product_id": "product-1", "quantity": 3})
    assert response.status_code == 200
    assert response.json()["status"] == "reserved"
    assert inventory["product-1"] == 97


def test_unavailable_failure_does_not_reserve_stock() -> None:
    from app.main import controller

    controller.activate("unavailable")
    try:
        assert client.get("/health").status_code == 503
        response = client.post("/reserve", json={"product_id": "product-1", "quantity": 1})
        assert response.status_code == 503
        assert inventory["product-1"] == 100
    finally:
        controller.deactivate()
    assert client.get("/health").status_code == 200


def test_latency_delays_reservation_and_keeps_health_immediate() -> None:
    import time

    from app.main import controller

    controller.activate("latency")
    try:
        assert client.get("/health").status_code == 200
        started = time.perf_counter()
        response = client.post("/reserve", json={"product_id": "product-1", "quantity": 1})
        elapsed = time.perf_counter() - started
        assert response.status_code == 200
        assert elapsed >= 1.4
        assert inventory["product-1"] == 99
    finally:
        controller.deactivate()
    assert client.get("/health").status_code == 200


def test_private_failure_control_accepts_json_activation_and_deactivation() -> None:
    experiment_id = str(uuid4())
    activated = client.post(
        "/internal/failure-control/activate",
        json={"failure_type": "latency", "experiment_id": experiment_id},
    )
    assert activated.status_code == 200, activated.text
    assert activated.json()["experiment_id"] == experiment_id
    assert activated.json()["active"] is True

    deactivated = client.post(
        "/internal/failure-control/deactivate",
        json={"experiment_id": experiment_id},
    )
    assert deactivated.status_code == 200, deactivated.text
    assert deactivated.json()["active"] is False


def test_insufficient_inventory() -> None:
    response = client.post("/reserve", json={"product_id": "product-1", "quantity": 101})
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "insufficient_inventory"
