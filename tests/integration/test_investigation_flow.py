import time

import httpx
import pytest

CONTROL_URL = "http://127.0.0.1:8000"
ORDER_URL = "http://127.0.0.1:8001"
PAYMENT_URL = "http://127.0.0.1:8003"
INVENTORY_URL = "http://127.0.0.1:8002"


@pytest.mark.integration
def test_controlled_payment_failure_investigation_end_to_end() -> None:
    client = httpx.Client(timeout=10)
    try:
        if client.get(f"{CONTROL_URL}/health").status_code != 200:
            pytest.skip("Start Docker Compose to run the investigation flow")
    except httpx.RequestError:
        pytest.skip("Start Docker Compose to run the investigation flow")

    created = client.post(
        f"{CONTROL_URL}/experiments",
        json={
            "target_service": "payment-service",
            "failure_type": "unavailable",
            "duration_seconds": 120,
        },
    )
    assert created.status_code == 201
    experiment_id = created.json()["id"]
    try:
        started = client.post(f"{CONTROL_URL}/experiments/{experiment_id}/start")
        assert started.status_code == 200

        payment = client.post(
            f"{PAYMENT_URL}/pay", json={"order_id": "phase4-test", "amount": 12.5}
        )
        assert payment.status_code == 503
        assert "payment_id" not in payment.json()

        order = client.post(
            f"{ORDER_URL}/orders",
            json={"product_id": "product-1", "quantity": 1, "amount": 12.5},
        )
        assert order.status_code == 502
        time.sleep(2)

        investigation = None
        for _ in range(8):
            investigation = client.post(
                f"{CONTROL_URL}/experiments/{experiment_id}/investigate"
            )
            assert investigation.status_code == 200, investigation.text
            result = investigation.json()
            has_trace = any(item["source"] == "trace" for item in result["evidence"])
            has_windowed_metric = any(
                record["source"] == "metric"
                and record["details"].get("window_seconds") == 300
                for record in result["evidence_records"]
            )
            if has_trace and has_windowed_metric:
                break
            time.sleep(1)

        assert investigation is not None
        result = investigation.json()
        assert result["experiment_id"] == experiment_id
        assert result["provider"] == "mock"
        assert "payment-service" in result["root_cause"]
        assert result["evidence"]
        evidence_records = {
            record["evidence_id"]: record for record in result["evidence_records"]
        }
        for citation in result["evidence"]:
            record = evidence_records[citation["evidence_id"]]
            assert citation["source"] == record["source"]
            assert citation["description"] == record["description"]
        assert {"experiment", "service", "log", "trace"}.issubset(
            {item["source"] for item in result["evidence"]}
        )
        assert any(
            record["source"] == "metric"
            and record["details"].get("window_seconds") == 300
            for record in result["evidence_records"]
        )
        assert any(
            item["service"] == "payment-service"
            and item["description"].find("HTTP 503") >= 0
            for item in result["evidence"]
        )
        assert any(
            item["service"] == "order-service" for item in result["affected_services"]
        )
        order_impact = next(
            item
            for item in result["affected_services"]
            if item["service"] == "order-service"
        )
        assert any(
            evidence_records[evidence_id]["source"] == "log"
            and evidence_records[evidence_id]["service"] == "order-service"
            for evidence_id in order_impact["evidence_ids"]
        )
        assert any(
            evidence_records[evidence_id]["source"] == "dependency"
            for evidence_id in order_impact["evidence_ids"]
        )
        trace_records = [
            record
            for record in result["evidence_records"]
            if record["source"] == "trace"
        ]
        correlated = {}
        for record in trace_records:
            correlated.setdefault(record["trace_id"], []).append(record)
        correlated_flow = next(
            group
            for group in correlated.values()
            if {record["service"] for record in group}
            >= {"order-service", "inventory-service", "payment-service"}
        )
        flow_trace_ids = {record["trace_id"] for record in correlated_flow}
        assert len(flow_trace_ids) == 1
        spans_by_id = {
            record["details"]["span_id"]: record for record in correlated_flow
        }
        assert any(
            record["details"].get("parent_span_id") in spans_by_id
            and spans_by_id[record["details"]["parent_span_id"]]["service"]
            != record["service"]
            for record in correlated_flow
        )
        assert {"experiment", "service", "metric", "log", "trace"}.issubset(
            {item["source"] for item in result["timeline"]}
        )
        assert 0 <= result["confidence"] <= 1
        assert result["recommended_action"]

        stopped = client.post(f"{CONTROL_URL}/experiments/{experiment_id}/stop")
        assert stopped.status_code == 200
        assert client.get(f"{PAYMENT_URL}/health").status_code == 200
        historical = client.post(
            f"{CONTROL_URL}/experiments/{experiment_id}/investigate"
        )
        assert historical.status_code == 200
        assert historical.json()["experiment_id"] == experiment_id
        assert any(item["source"] == "trace" for item in historical.json()["evidence"])
    finally:
        current = client.get(f"{CONTROL_URL}/experiments/{experiment_id}")
        if current.status_code == 200 and current.json()["status"] == "RUNNING":
            client.post(f"{CONTROL_URL}/experiments/{experiment_id}/stop")
        client.close()


@pytest.mark.integration
def test_controlled_inventory_latency_is_investigated_from_real_telemetry() -> None:
    client = httpx.Client(timeout=10)
    try:
        if client.get(f"{CONTROL_URL}/health").status_code != 200:
            pytest.skip("Start Docker Compose to run the investigation flow")
    except httpx.RequestError:
        pytest.skip("Start Docker Compose to run the investigation flow")

    created = client.post(
        f"{CONTROL_URL}/experiments",
        json={
            "target_service": "inventory-service",
            "failure_type": "latency",
            "duration_seconds": 60,
        },
    )
    assert created.status_code == 201, created.text
    experiment_id = created.json()["id"]
    try:
        started = client.post(f"{CONTROL_URL}/experiments/{experiment_id}/start")
        assert started.status_code == 200, started.text

        response = client.post(
            f"{INVENTORY_URL}/reserve",
            json={"product_id": "product-1", "quantity": 1},
        )
        assert response.status_code == 200, response.text

        stopped = client.post(f"{CONTROL_URL}/experiments/{experiment_id}/stop")
        assert stopped.status_code == 200, stopped.text

        # Allow Tempo's batch span processor to export the request before
        # querying it. Repeated investigation polling itself generates logs
        # and can evict the experiment's request from the bounded log buffer.
        time.sleep(6)
        investigation = client.post(
            f"{CONTROL_URL}/experiments/{experiment_id}/investigate"
        )
        assert investigation.status_code == 200, investigation.text
        result = investigation.json()

        assert result is not None
        assert result["outcome"] == "root_cause_identified"
        assert "elevated request duration" in result["root_cause"]
        assert result["confidence"] > 0
        assert result["root_cause_evidence_ids"]
        evidence_by_id = {item["evidence_id"]: item for item in result["evidence_records"]}
        latency_citations = [
            evidence_by_id[item]
            for item in result["root_cause_evidence_ids"]
            if item in evidence_by_id
            and evidence_by_id[item]["event"] == "latency_observed"
        ]
        assert {item["source"] for item in latency_citations} >= {"log", "trace"}
        assert any(item["service"] == "inventory-service" for item in result["affected_services"])
        assert result["timeline"]
        assert all(item["evidence_id"] in evidence_by_id for item in result["evidence"])
    finally:
        current = client.get(f"{CONTROL_URL}/experiments/{experiment_id}")
        if current.status_code == 200 and current.json()["status"] == "RUNNING":
            client.post(f"{CONTROL_URL}/experiments/{experiment_id}/stop")
        client.close()
