from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_health() -> None:
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "service": "control-api"}


def test_services() -> None:
    response = client.get("/services")
    assert response.status_code == 200
    services = response.json()
    assert {service["name"] for service in services} == {
        "order-service",
        "inventory-service",
        "payment-service",
    }
    assert all(service["url"] and service["status"] == "configured" for service in services)


def test_experiments_contract() -> None:
    response = client.get("/experiments")
    assert response.status_code == 200
    assert response.json() == []


def test_incidents_contract() -> None:
    response = client.get("/incidents")
    assert response.status_code == 200
    assert response.json() == []
