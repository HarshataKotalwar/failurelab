import os
import re
import time

import httpx
import pytest

CONTROL_API_URL = os.environ.get("CONTROL_API_URL", "http://127.0.0.1:8000")
PAYMENT_SERVICE_URL = os.environ.get("PAYMENT_SERVICE_URL", "http://127.0.0.1:8003")


@pytest.mark.integration
def test_experiment_controls_running_payment_service() -> None:
    try:
        control_health = httpx.get(f"{CONTROL_API_URL}/health", timeout=1)
        payment_health = httpx.get(f"{PAYMENT_SERVICE_URL}/health", timeout=1)
    except httpx.RequestError:
        pytest.skip(
            "Run the Docker Compose stack to exercise the cross-service experiment flow"
        )
    assert control_health.status_code == 200
    assert payment_health.status_code == 200

    created_response = httpx.post(
        f"{CONTROL_API_URL}/experiments",
        json={
            "target_service": "payment-service",
            "failure_type": "unavailable",
            "duration_seconds": 30,
        },
        timeout=5,
    )
    assert created_response.status_code == 201
    created = created_response.json()
    experiment_id = created["id"]
    assert created["status"] == "CREATED"

    try:
        loaded = httpx.get(f"{CONTROL_API_URL}/experiments/{experiment_id}", timeout=5)
        assert loaded.status_code == 200
        assert loaded.json()["id"] == experiment_id
        persisted = httpx.get(f"{CONTROL_API_URL}/experiments", timeout=5)
        assert persisted.status_code == 200
        assert any(item["id"] == experiment_id for item in persisted.json())

        started = httpx.post(
            f"{CONTROL_API_URL}/experiments/{experiment_id}/start", timeout=5
        )
        assert started.status_code == 200
        assert started.json()["status"] == "RUNNING"
        assert httpx.get(f"{PAYMENT_SERVICE_URL}/health", timeout=5).status_code == 503
        failed_payment = httpx.post(
            f"{PAYMENT_SERVICE_URL}/pay",
            json={"order_id": "experiment-order", "amount": 10},
            timeout=5,
        )
        assert failed_payment.status_code == 503
        assert not failed_payment.is_success
        assert "payment_id" not in failed_payment.json()
        trace_id = failed_payment.headers["x-trace-id"]
        assert re.fullmatch(r"[0-9a-f]{32}", trace_id)
        payment_metrics = httpx.get(f"{PAYMENT_SERVICE_URL}/metrics", timeout=5)
        assert payment_metrics.status_code == 200
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
            for line in payment_metrics.text.splitlines()
        )

        evidence = None
        for _ in range(8):
            evidence = httpx.get(
                f"{CONTROL_API_URL}/experiments/{experiment_id}/evidence", timeout=10
            )
            assert evidence.status_code == 200
            if evidence.json()["traces"]["available"]:
                break
            time.sleep(1)
        assert evidence is not None
        evidence_body = evidence.json()
        assert evidence_body["experiment_id"] == experiment_id
        assert evidence_body["target_service"] == "payment-service"
        assert evidence_body["metrics"]["available"] is True
        assert evidence_body["metrics"]["request_rate"] is not None
        assert evidence_body["metrics"]["request_rate"] > 0
        assert evidence_body["traces"]["available"] is True
        assert evidence_body["traces"]["status"] == "available"
        assert trace_id in evidence_body["traces"]["trace_ids"]
        payment_observation = next(
            observation
            for observation in evidence_body["service_observations"]
            if observation["service"] == "payment-service"
        )
        assert payment_observation["status_code"] == 503
        assert payment_observation["observed_at"]
        assert evidence_body["metrics"]["window_seconds"] == 300
        assert evidence_body["metrics"]["window_start"]
        assert evidence_body["metrics"]["window_end"]

        stopped = httpx.post(
            f"{CONTROL_API_URL}/experiments/{experiment_id}/stop", timeout=5
        )
        assert stopped.status_code == 200
        assert stopped.json()["status"] == "STOPPED"
        assert httpx.get(f"{PAYMENT_SERVICE_URL}/health", timeout=5).status_code == 200
        recovered_payment = httpx.post(
            f"{PAYMENT_SERVICE_URL}/pay",
            json={"order_id": "experiment-order", "amount": 10},
            timeout=5,
        )
        assert recovered_payment.status_code == 200
        assert recovered_payment.json()["payment_id"]
        assert recovered_payment.json()["status"] == "paid"
    finally:
        current = httpx.get(f"{CONTROL_API_URL}/experiments/{experiment_id}", timeout=5)
        if current.is_success and current.json()["status"] == "RUNNING":
            httpx.post(f"{CONTROL_API_URL}/experiments/{experiment_id}/stop", timeout=5)
