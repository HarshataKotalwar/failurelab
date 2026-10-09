from fastapi.testclient import TestClient

from app.main import app, inventory

client = TestClient(app)


def setup_function() -> None:
    inventory.clear()
    inventory.update({"product-1": 100})


def test_health() -> None:
    assert client.get("/health").json() == {"status": "ok", "service": "inventory-service"}


def test_successful_reservation() -> None:
    response = client.post("/reserve", json={"product_id": "product-1", "quantity": 3})
    assert response.status_code == 200
    assert response.json()["status"] == "reserved"
    assert inventory["product-1"] == 97


def test_insufficient_inventory() -> None:
    response = client.post("/reserve", json={"product_id": "product-1", "quantity": 101})
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "insufficient_inventory"
