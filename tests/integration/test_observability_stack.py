import os

import httpx
import pytest

PROMETHEUS_URL = os.environ.get("PROMETHEUS_URL", "http://127.0.0.1:9090")
GRAFANA_URL = os.environ.get("GRAFANA_URL", "http://127.0.0.1:3000")


@pytest.mark.integration
def test_prometheus_scrapes_services_and_grafana_is_provisioned() -> None:
    try:
        targets = httpx.get(f"{PROMETHEUS_URL}/api/v1/targets?state=active", timeout=2)
        datasources = httpx.get(f"{GRAFANA_URL}/api/datasources", timeout=2)
        dashboard = httpx.get(
            f"{GRAFANA_URL}/api/dashboards/uid/failurelab-overview", timeout=2
        )
        grafana_health = httpx.get(f"{GRAFANA_URL}/api/health", timeout=2)
    except httpx.RequestError:
        pytest.skip("Run Docker Compose to verify the observability stack")

    assert targets.status_code == 200
    active_targets = targets.json()["data"]["activeTargets"]
    scraped_instances = {
        target["labels"]["instance"]
        for target in active_targets
        if target["labels"].get("job") == "failurelab" and target["health"] == "up"
    }
    assert scraped_instances == {
        "control-api:8000",
        "order-service:8001",
        "inventory-service:8002",
        "payment-service:8003",
    }

    assert grafana_health.status_code == 200
    assert datasources.status_code == 200
    assert any(
        source["name"] == "Prometheus" and source["url"] == "http://prometheus:9090"
        for source in datasources.json()
    )
    assert any(
        source["name"] == "Tempo" and source["url"] == "http://tempo:3200"
        for source in datasources.json()
    )
    assert dashboard.status_code == 200
    assert dashboard.json()["dashboard"]["title"] == "FailureLab Overview"
    assert len(dashboard.json()["dashboard"]["panels"]) >= 3
