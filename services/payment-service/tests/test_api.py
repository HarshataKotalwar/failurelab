import logging
import re

from fastapi.testclient import TestClient

from app.api.internal import controller
from app.main import app

client = TestClient(app)


def test_health() -> None:
    response = client.get("/health")
    assert response.json() == {"status": "ok", "service": "payment-service"}
    assert re.fullmatch(r"[0-9a-f]{32}", response.headers["x-trace-id"])


def test_successful_payment() -> None:
    response = client.post("/pay", json={"order_id": "order-1", "amount": 100})
    assert response.status_code == 200
    assert response.json()["status"] == "paid"
    assert response.json()["order_id"] == "order-1"


def test_unavailable_failure_returns_503_and_recovers() -> None:
    state = controller.activate("unavailable")
    try:
        assert state.active is True
        assert state.failure_type.value == "unavailable"
        assert client.get("/health").status_code == 503
        payment_response = client.post(
            "/pay", json={"order_id": "order-1", "amount": 100}
        )
        assert payment_response.status_code == 503
        assert not payment_response.is_success
        assert "payment_id" not in payment_response.json()
        metrics = client.get("/metrics")
        assert 'service="failurelab-payment-service"' in metrics.text
        assert any(
            line.startswith("failurelab_http_requests_total{")
            and all(
                label in line
                for label in (
                    'service="failurelab-payment-service"',
                    'method="POST"',
                    'route="/pay"',
                    'status_code="503"',
                )
            )
            for line in metrics.text.splitlines()
        )
    finally:
        ended_state = controller.deactivate()

    assert ended_state.active is False
    assert ended_state.ended_at is not None
    assert client.get("/health").status_code == 200
    recovered_payment = client.post(
        "/pay", json={"order_id": "order-1", "amount": 100}
    )
    assert recovered_payment.status_code == 200
    assert recovered_payment.json()["payment_id"]
    assert recovered_payment.json()["status"] == "paid"


def test_http_500_failure_returns_500_without_charging() -> None:
    controller.activate("http_500")
    try:
        assert client.get("/health").status_code == 500
        response = client.post("/pay", json={"order_id": "order-1", "amount": 100})
        assert response.status_code == 500
        assert response.json()["detail"]["code"] == "injected_http_500"
    finally:
        controller.deactivate()
    assert client.post("/pay", json={"order_id": "order-1", "amount": 100}).status_code == 200


def test_latency_and_timeout_constants_match_the_order_client() -> None:
    from failurelab_failure import LATENCY_SECONDS, TIMEOUT_SECONDS

    assert LATENCY_SECONDS < 3
    assert TIMEOUT_SECONDS > 3


def test_payment_logs_include_request_and_trace_context(caplog) -> None:
    caplog.set_level(logging.INFO)
    response = client.get("/health")
    trace_id = response.headers["x-trace-id"]
    completed = [
        record
        for record in caplog.records
        if getattr(record, "event", None) == "request_completed"
        and getattr(record, "service", None) == "failurelab-payment-service"
    ]
    assert completed
    assert completed[-1].method == "GET"
    assert completed[-1].route == "/health"
    assert completed[-1].status_code == 200
    assert completed[-1].trace_id == trace_id
