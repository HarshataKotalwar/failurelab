from __future__ import annotations

import time
import uuid
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.models.entities import Experiment
from app.schemas.api import ExperimentCreate
from app.schemas.investigation import (
    EvidenceState,
    ExperimentContext,
    InvestigationContext,
)
from app.services.evaluation import GroundTruthBuilder
from app.services.experiments import ExperimentService
from app.services.failure_control import FailureControlConflict
from app.services.investigation_workflow import InvestigationWorkflow
from app.services.investigator import Investigator, MockLLMProvider


class InMemoryExperimentRepository:
    def __init__(self) -> None:
        self.experiments: dict[uuid.UUID, Experiment] = {}

    def create(self, values: ExperimentCreate) -> Experiment:
        experiment = Experiment(
            id=uuid.uuid4(),
            target_service=values.target_service or "payment-service",
            failure_type=values.failure_type or "none",
            mode=values.mode,
            duration_seconds=values.duration_seconds or 0,
            scenario=values.scenario_id,
            status="CREATED",
            created_at=datetime.now(UTC),
        )
        self.experiments[experiment.id] = experiment
        return experiment

    def list(self) -> list[Experiment]:
        return list(self.experiments.values())

    def get(self, experiment_id: uuid.UUID) -> Experiment | None:
        return self.experiments.get(experiment_id)

    def has_running(self) -> bool:
        return any(experiment.status == "RUNNING" for experiment in self.experiments.values())

    def list_running(self) -> list[Experiment]:
        return [
            experiment for experiment in self.experiments.values() if experiment.status == "RUNNING"
        ]

    def mark_running(self, experiment_id: uuid.UUID, started_at: datetime) -> Experiment | None:
        experiment = self.experiments.get(experiment_id)
        if experiment is None or experiment.status != "CREATED":
            return None
        experiment.status = "RUNNING"
        experiment.started_at = started_at
        return experiment

    def mark_stopped(self, experiment_id: uuid.UUID, ended_at: datetime) -> Experiment | None:
        experiment = self.experiments.get(experiment_id)
        if experiment is None or experiment.status != "RUNNING":
            return None
        experiment.status = "STOPPED"
        experiment.ended_at = ended_at
        return experiment


class FakeFailureControl:
    def __init__(self) -> None:
        self.active_experiment_id: uuid.UUID | None = None
        self.failure_type: str | None = None

    async def activate(
        self, failure_type: str, experiment_id: uuid.UUID, *, target_service: str
    ) -> None:
        if self.active_experiment_id not in (None, experiment_id):
            raise FailureControlConflict("A different failure experiment is already active")
        self.active_experiment_id = experiment_id
        self.failure_type = failure_type

    async def deactivate(self, experiment_id: uuid.UUID, *, target_service: str) -> None:
        if self.active_experiment_id not in (None, experiment_id):
            raise FailureControlConflict("The active failure belongs to another experiment")
        self.active_experiment_id = None
        self.failure_type = None

    @property
    def is_active(self) -> bool:
        return self.active_experiment_id is not None


class InMemoryInvestigationRepository:
    def __init__(self) -> None:
        self.saved: dict[uuid.UUID, tuple[object, object]] = {}

    def save(self, result, ground_truth) -> None:
        self.saved[result.investigation_id] = (result, ground_truth)

    def get(self, investigation_id: uuid.UUID):
        return self.saved.get(investigation_id)

    def latest_for_experiment(self, experiment_id: uuid.UUID):
        return next(
            (
                saved
                for saved in reversed(list(self.saved.values()))
                if saved[0].experiment_id == experiment_id
            ),
            None,
        )

    def list_results(self, experiment_id=None, limit=20):
        return [
            saved[0]
            for saved in list(self.saved.values())[-limit:]
            if experiment_id is None or saved[0].experiment_id == experiment_id
        ]


class InMemoryIncidentRepository:
    def __init__(self) -> None:
        self.items: list[object] = []

    def list(self, limit: int = 100) -> list[object]:
        return self.items[:limit]


@pytest.fixture
def experiment_components() -> tuple[
    ExperimentService, InMemoryExperimentRepository, FakeFailureControl
]:
    repository = InMemoryExperimentRepository()
    failure_control = FakeFailureControl()
    return ExperimentService(repository, failure_control), repository, failure_control


@pytest.fixture
def client(
    experiment_components: tuple[
        ExperimentService, InMemoryExperimentRepository, FakeFailureControl
    ],
) -> TestClient:
    service = experiment_components[0]
    previous_service = app.state.experiment_service
    previous_repository = app.state.investigation_repository
    previous_incident_repository = app.state.incident_repository
    app.state.experiment_service = service
    app.state.investigation_repository = InMemoryInvestigationRepository()
    app.state.incident_repository = InMemoryIncidentRepository()
    try:
        with TestClient(app) as test_client:
            yield test_client
    finally:
        app.state.experiment_service = previous_service
        app.state.investigation_repository = previous_repository
        app.state.incident_repository = previous_incident_repository


def test_health(client: TestClient) -> None:
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "service": "control-api"}
    assert len(response.headers["x-trace-id"]) == 32


def test_metrics_expose_prometheus_http_request_data(client: TestClient) -> None:
    client.get("/health")
    response = client.get("/metrics")
    assert response.status_code == 200
    assert "text/plain" in response.headers["content-type"]
    assert any(
        line.startswith("failurelab_http_requests_total{")
        and all(
            label in line
            for label in (
                'service="failurelab-control-api"',
                'method="GET"',
                'route="/health"',
                'status_code="200"',
            )
        )
        for line in response.text.splitlines()
    )


def test_services(client: TestClient) -> None:
    response = client.get("/services")
    assert response.status_code == 200
    services = response.json()
    assert {service["name"] for service in services} == {
        "order-service",
        "inventory-service",
        "payment-service",
    }
    assert all(service["url"] and service["status"] == "configured" for service in services)


def test_experiments_contract(client: TestClient) -> None:
    response = client.get("/experiments")
    assert response.status_code == 200
    assert response.json() == []


def test_incidents_contract(client: TestClient) -> None:
    response = client.get("/incidents")
    assert response.status_code == 200
    assert response.json() == []


def test_incidents_endpoint_returns_persisted_incident_records(client: TestClient) -> None:
    incident_id = uuid.uuid4()
    experiment_id = uuid.uuid4()
    created_at = datetime.now(UTC)
    app.state.incident_repository.items.append(
        SimpleNamespace(
            id=incident_id,
            experiment_id=experiment_id,
            severity="high",
            status="open",
            started_at=created_at,
            ended_at=None,
            created_at=created_at,
        )
    )

    response = client.get("/incidents")

    assert response.status_code == 200
    assert response.json() == [
        {
            "id": str(incident_id),
            "experiment_id": str(experiment_id),
            "severity": "high",
            "status": "open",
            "started_at": created_at.isoformat().replace("+00:00", "Z"),
            "ended_at": None,
            "created_at": created_at.isoformat().replace("+00:00", "Z"),
        }
    ]


def test_scenarios_match_supported_failure_injection(client: TestClient) -> None:
    response = client.get("/scenarios")
    assert response.status_code == 200
    scenarios = {item["id"]: item for item in response.json()}
    assert scenarios["payment-unavailable"]["failure_type"] == "unavailable"
    assert scenarios["inventory-latency"]["target_service"] == "inventory-service"
    assert "order-service" in scenarios["payment-unavailable"]["indirect_services"]
    created = client.post("/experiments", json={"scenario_id": "inventory-latency"}).json()
    assert created["target_service"] == "inventory-service"
    assert created["failure_type"] == "latency"
    assert created["duration_seconds"] == 30
    assert created["scenario"] == "inventory-latency"
    assert client.get("/dependencies").json()["order-service"] == [
        "inventory-service",
        "payment-service",
    ]


def test_create_experiment_starts_in_created_state(client: TestClient) -> None:
    response = client.post(
        "/experiments",
        json={
            "target_service": "payment-service",
            "failure_type": "unavailable",
            "duration_seconds": 60,
        },
    )
    assert response.status_code == 201
    assert response.json()["status"] == "CREATED"
    assert response.json()["started_at"] is None
    assert response.json()["ended_at"] is None


@pytest.mark.parametrize(
    "payload",
    [
        {"target_service": "order-service", "failure_type": "unavailable", "duration_seconds": 60},
        {"target_service": "payment-service", "failure_type": "disk-full", "duration_seconds": 60},
        {"target_service": "payment-service", "failure_type": "unavailable", "duration_seconds": 0},
        {
            "target_service": "payment-service",
            "failure_type": "unavailable",
            "duration_seconds": -1,
        },
    ],
)
def test_invalid_experiment_input_is_rejected(
    client: TestClient, payload: dict[str, object]
) -> None:
    assert client.post("/experiments", json=payload).status_code == 422


def test_get_experiment_returns_saved_record(client: TestClient) -> None:
    created = client.post(
        "/experiments",
        json={
            "target_service": "payment-service",
            "failure_type": "unavailable",
            "duration_seconds": 60,
        },
    ).json()
    response = client.get(f"/experiments/{created['id']}")
    assert response.status_code == 200
    assert response.json() == created


def test_evidence_not_found_returns_404(client: TestClient) -> None:
    response = client.get(f"/experiments/{uuid.uuid4()}/evidence")
    assert response.status_code == 404


def test_investigation_unknown_experiment_returns_404(client: TestClient) -> None:
    response = client.post(f"/experiments/{uuid.uuid4()}/investigate")
    assert response.status_code == 404


def test_investigation_api_returns_structured_mock_result(
    client: TestClient,
    experiment_components: tuple[
        ExperimentService, InMemoryExperimentRepository, FakeFailureControl
    ],
) -> None:
    created = client.post(
        "/experiments",
        json={
            "target_service": "payment-service",
            "failure_type": "unavailable",
            "duration_seconds": 60,
        },
    ).json()
    experiment_id = created["id"]
    assert client.post(f"/experiments/{experiment_id}/start").status_code == 200

    class Evidence:
        async def collect(self, experiment):
            return InvestigationContext(
                experiment=ExperimentContext(
                    experiment_id=experiment.id,
                    target_service=experiment.target_service,
                    failure_type=experiment.failure_type,
                    configured_duration_seconds=experiment.duration_seconds,
                    status=experiment.status,
                    created_at=experiment.created_at,
                    started_at=experiment.started_at,
                    ended_at=experiment.ended_at,
                ),
                service_states=[],
                timeline=[],
                metrics={},
                metrics_observed_at=None,
                metric_window_start=None,
                metric_window_end=None,
                metrics_state=EvidenceState(
                    status="unavailable", available=False, message="not configured"
                ),
                logs=[],
                logs_state=EvidenceState(
                    status="unavailable", available=False, message="not configured"
                ),
                traces=[],
                traces_state=EvidenceState(
                    status="unavailable", available=False, message="not configured"
                ),
                dependencies={},
                evidence_records=[],
            )

    original_workflow = app.state.investigation_workflow
    app.state.investigation_workflow = InvestigationWorkflow(
        Evidence(),
        Investigator(MockLLMProvider()),
        app.state.investigation_repository,
        GroundTruthBuilder(),
    )
    try:
        response = client.post(f"/experiments/{experiment_id}/investigate")
    finally:
        app.state.investigation_workflow = original_workflow
    assert response.status_code == 200
    result = response.json()
    assert result["experiment_id"] == experiment_id
    assert result["investigation_id"]
    assert result["provider"] == "mock"
    assert result["outcome"] == "insufficient_evidence"
    assert result["root_cause"] is None
    assert result["missing_evidence"]
    assert result["evidence_records"] == []
    assert result["timeline"] == []
    assert 0 <= result["confidence"] <= 1
    assert result["recommended_action"]

    saved = client.get(f"/investigations/{result['investigation_id']}")
    assert saved.status_code == 200
    assert saved.json()["investigation_id"] == result["investigation_id"]
    remediation = client.get(f"/investigations/{result['investigation_id']}/remediation")
    assert remediation.status_code == 200
    assert remediation.json()["outcome"] == "insufficient_evidence"
    assert remediation.json()["actions"] == []
    evaluation = client.post(f"/investigations/{result['investigation_id']}/evaluate")
    assert evaluation.status_code == 200
    assert evaluation.json()["overall"] == "correct_investigation"
    assert client.get("/investigations").status_code == 200


def test_verification_experiment_does_not_activate_failure(
    client: TestClient,
    experiment_components: tuple[
        ExperimentService, InMemoryExperimentRepository, FakeFailureControl
    ],
) -> None:
    failure_control = experiment_components[2]
    created = client.post(
        "/experiments",
        json={
            "target_service": "payment-service",
            "mode": "verification",
            "duration_seconds": 30,
        },
    )
    assert created.status_code == 201, created.text
    assert created.json()["mode"] == "verification"
    assert created.json()["failure_type"] == "none"
    started = client.post(f"/experiments/{created.json()['id']}/start")
    assert started.status_code == 200
    assert not failure_control.is_active
    stopped = client.post(f"/experiments/{created.json()['id']}/stop")
    assert stopped.status_code == 200
    assert not failure_control.is_active


def test_start_and_stop_experiment_lifecycle(
    client: TestClient,
    experiment_components: tuple[
        ExperimentService, InMemoryExperimentRepository, FakeFailureControl
    ],
) -> None:
    failure_control = experiment_components[2]
    created = client.post(
        "/experiments",
        json={
            "target_service": "payment-service",
            "failure_type": "unavailable",
            "duration_seconds": 60,
        },
    ).json()
    experiment_id = created["id"]

    started = client.post(f"/experiments/{experiment_id}/start")
    assert started.status_code == 200
    assert started.json()["status"] == "RUNNING"
    assert started.json()["started_at"] is not None
    assert failure_control.is_active

    already_running = client.post(f"/experiments/{experiment_id}/start")
    assert already_running.status_code == 409

    stopped = client.post(f"/experiments/{experiment_id}/stop")
    assert stopped.status_code == 200
    assert stopped.json()["status"] == "STOPPED"
    assert stopped.json()["ended_at"] is not None
    assert not failure_control.is_active
    assert client.post(f"/experiments/{experiment_id}/stop").status_code == 409
    assert client.post(f"/experiments/{experiment_id}/start").status_code == 409


def test_start_unknown_experiment_returns_404(client: TestClient) -> None:
    assert client.post(f"/experiments/{uuid.uuid4()}/start").status_code == 404


def test_duration_expiration_stops_experiment_automatically(
    client: TestClient,
    experiment_components: tuple[
        ExperimentService, InMemoryExperimentRepository, FakeFailureControl
    ],
) -> None:
    failure_control = experiment_components[2]
    created = client.post(
        "/experiments",
        json={
            "target_service": "payment-service",
            "failure_type": "unavailable",
            "duration_seconds": 1,
        },
    ).json()
    experiment_id = created["id"]
    assert client.post(f"/experiments/{experiment_id}/start").json()["status"] == "RUNNING"

    deadline = time.monotonic() + 3
    stopped = None
    while time.monotonic() < deadline:
        stopped = client.get(f"/experiments/{experiment_id}").json()
        if stopped["status"] == "STOPPED":
            break
        time.sleep(0.05)

    assert stopped is not None
    assert stopped["status"] == "STOPPED"
    assert stopped["ended_at"] is not None
    assert not failure_control.is_active
