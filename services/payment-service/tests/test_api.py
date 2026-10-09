from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_health() -> None:
    assert client.get("/health").json() == {"status": "ok", "service": "payment-service"}


def test_successful_payment() -> None:
    response = client.post("/pay", json={"order_id": "order-1", "amount": 100})
    assert response.status_code == 200
    assert response.json()["status"] == "paid"
    assert response.json()["order_id"] == "order-1"
