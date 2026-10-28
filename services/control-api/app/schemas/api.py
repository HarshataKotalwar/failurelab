from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator


class HealthResponse(BaseModel):
    status: Literal["ok"]
    service: str


class ServiceResponse(BaseModel):
    id: str
    name: str
    url: str
    status: str


class IncidentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    experiment_id: UUID
    severity: str
    status: str
    started_at: datetime | None = None
    ended_at: datetime | None = None
    created_at: datetime


class ExperimentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    mode: Literal["failure", "verification"] = "failure"
    target_service: str
    failure_type: str
    duration_seconds: int
    status: str
    scenario: str | None = None
    created_at: datetime
    started_at: datetime | None = None
    ended_at: datetime | None = None


class ScenarioResponse(BaseModel):
    id: str
    name: str
    summary: str
    target_service: str
    failure_type: str
    duration_seconds: int
    expected_behavior: str
    direct_services: list[str]
    indirect_services: list[str]


class ExperimentCreate(BaseModel):
    target_service: Literal["order-service", "inventory-service", "payment-service"] | None = None
    mode: Literal["failure", "verification"] = "failure"
    failure_type: Literal["unavailable", "http_500", "latency", "timeout"] | None = None
    duration_seconds: Annotated[int, Field(gt=0, strict=True)] | None = None
    scenario_id: str | None = None

    @model_validator(mode="after")
    def validate_mode(self) -> "ExperimentCreate":
        from app.services.scenarios import SCENARIOS_BY_ID, failure_supported

        if self.scenario_id is not None:
            scenario = SCENARIOS_BY_ID.get(self.scenario_id)
            if scenario is None:
                raise ValueError("unknown scenario")
            if self.target_service is not None and self.target_service != scenario.target_service:
                raise ValueError("target_service does not match the scenario")
            if self.failure_type is not None and self.failure_type != scenario.failure_type:
                raise ValueError("failure_type does not match the scenario")
            self.target_service = scenario.target_service
            self.failure_type = scenario.failure_type
            if self.duration_seconds is None:
                self.duration_seconds = scenario.duration_seconds
            self.mode = "failure"
        if self.target_service is None or self.duration_seconds is None:
            raise ValueError("target_service and duration_seconds are required")
        if self.mode == "failure" and self.failure_type is None:
            raise ValueError("failure_type is required for a failure experiment")
        if self.mode == "verification" and self.failure_type is not None:
            raise ValueError("verification experiments do not inject a failure")
        if self.mode == "verification" and self.target_service != "payment-service":
            raise ValueError("verification experiments currently target payment-service")
        if (
            self.mode == "failure"
            and self.failure_type is not None
            and not failure_supported(self.target_service, self.failure_type)
        ):
            raise ValueError(
                f"{self.target_service} does not support failure type {self.failure_type}"
            )
        return self


class EvidenceTimelineEvent(BaseModel):
    event: str
    status: str
    at: datetime


class ServiceObservation(BaseModel):
    service: str
    url: str
    status: str
    status_code: int | None = None
    observed_at: datetime


class ServiceWindowMetric(BaseModel):
    service: str
    available: bool
    request_count: float | None = None
    error_count: float | None = None
    error_rate: float | None = None
    p95_latency_seconds: float | None = None
    message: str | None = None


class EvidenceMetrics(BaseModel):
    available: bool
    request_rate: float | None = None
    http_5xx_rate: float | None = None
    p95_latency_seconds: float | None = None
    message: str | None = None
    observed_at: datetime | None = None
    window_start: datetime | None = None
    window_end: datetime | None = None
    window_seconds: int | None = None


class EvidenceSpan(BaseModel):
    trace_id: str
    span_id: str
    parent_span_id: str | None = None
    service_name: str
    operation_name: str
    start_time: datetime
    end_time: datetime
    duration_ms: float
    status: str
    attributes: dict[str, str | int | float | bool] = Field(default_factory=dict)


class TraceEvidence(BaseModel):
    status: Literal["available", "not_found", "unavailable", "partial"]
    available: bool
    trace_ids: list[str]
    message: str | None = None
    failed_sources: list[str] = Field(default_factory=list)
    spans: list[EvidenceSpan] = Field(default_factory=list)


class EvidenceLog(BaseModel):
    timestamp: datetime
    service: str
    level: str
    message: str
    route: str | None = None
    status_code: int | None = None
    duration_ms: float | None = None
    trace_id: str | None = None


class EvidenceLogs(BaseModel):
    status: Literal["available", "partial", "unavailable", "not_found"]
    logs: list[EvidenceLog] = Field(default_factory=list)
    message: str | None = None
    failed_sources: list[str] = Field(default_factory=list)


class ExperimentEvidence(BaseModel):
    experiment_id: UUID
    status: str
    target_service: str
    failure_type: str
    timeline: list[EvidenceTimelineEvent]
    service_observations: list[ServiceObservation]
    metrics: EvidenceMetrics
    window_metrics: list[ServiceWindowMetric] = Field(default_factory=list)
    observation_window_start: datetime | None = None
    observation_window_end: datetime | None = None
    traces: TraceEvidence
    logs: EvidenceLogs = Field(
        default_factory=lambda: EvidenceLogs(status="not_found", message="No log evidence returned")
    )
