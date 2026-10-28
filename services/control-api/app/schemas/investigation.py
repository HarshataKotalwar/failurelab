import hashlib
import json
from datetime import UTC, datetime
from typing import Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator

EvidenceSource = Literal["experiment", "service", "metric", "log", "trace", "dependency"]


def evidence_identifier(
    *,
    source: str,
    service: str | None,
    observed_at: datetime,
    event: str,
    description: str,
    trace_id: str | None,
    details: dict[str, str | int | float | bool | None],
) -> str:
    canonical = json.dumps(
        {
            "source": source,
            "service": service,
            "observed_at": observed_at.isoformat(),
            "event": event,
            "description": description,
            "trace_id": trace_id,
            "details": details,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return "ev_" + hashlib.sha256(canonical.encode()).hexdigest()[:24]


class EvidenceRecord(BaseModel):
    model_config = ConfigDict(frozen=True)

    evidence_id: str
    source: EvidenceSource
    service: str | None = None
    observed_at: datetime
    event: str
    description: str
    trace_id: str | None = None
    details: dict[str, str | int | float | bool | None] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_stable_id(self) -> "EvidenceRecord":
        expected = evidence_identifier(
            source=self.source,
            service=self.service,
            observed_at=self.observed_at,
            event=self.event,
            description=self.description,
            trace_id=self.trace_id,
            details=self.details,
        )
        if self.evidence_id != expected:
            raise ValueError("evidence_id does not match the evidence record")
        return self


class NormalizedLog(BaseModel):
    timestamp: datetime
    service: str
    level: str
    message: str
    route: str | None = None
    status_code: int | None = None
    duration_ms: float | None = None
    trace_id: str | None = None
    experiment_id: str | None = None


class NormalizedSpan(BaseModel):
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


class EvidenceState(BaseModel):
    status: Literal["available", "partial", "unavailable", "not_found"]
    available: bool
    message: str | None = None
    failed_sources: list[str] = Field(default_factory=list)


class ServiceState(BaseModel):
    service: str
    health_state: str
    http_status: int | None = None
    observed_at: datetime
    observation_is_current: bool = True
    observed_failure_state: str | None = None


class InvestigationEvent(BaseModel):
    timestamp: datetime
    source: Literal["experiment", "service", "metric", "log", "trace"]
    service: str | None = None
    event: str
    description: str
    evidence_id: str | None = None


class ExperimentContext(BaseModel):
    experiment_id: UUID
    mode: Literal["failure", "verification"] = "failure"
    target_service: str
    failure_type: str
    configured_duration_seconds: int
    status: str
    created_at: datetime
    started_at: datetime | None
    ended_at: datetime | None


class InvestigationContext(BaseModel):
    experiment: ExperimentContext
    service_states: list[ServiceState]
    timeline: list[InvestigationEvent]
    metrics: dict[str, float | None]
    metrics_observed_at: datetime | None
    metric_window_start: datetime | None
    metric_window_end: datetime | None
    metrics_state: EvidenceState
    logs: list[NormalizedLog]
    logs_state: EvidenceState
    traces: list[NormalizedSpan]
    traces_state: EvidenceState
    dependencies: dict[str, list[str]]
    evidence_records: list[EvidenceRecord]


class EvidenceCitation(BaseModel):
    evidence_id: str
    claim: str = Field(min_length=1)
    source: EvidenceSource
    service: str | None = None
    description: str
    trace_id: str | None = None


class AffectedService(BaseModel):
    service: str
    impact: Literal["direct", "indirect"]
    evidence_ids: list[str]

    @model_validator(mode="after")
    def require_supporting_evidence(self) -> "AffectedService":
        if not self.evidence_ids:
            raise ValueError("affected service requires supporting evidence IDs")
        return self


class InvestigationResult(BaseModel):
    investigation_id: UUID = Field(default_factory=uuid4)
    experiment_id: UUID
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    provider: str
    outcome: Literal["root_cause_identified", "insufficient_evidence"]
    root_cause: str | None
    root_cause_evidence_ids: list[str] = Field(default_factory=list)
    evidence: list[EvidenceCitation]
    evidence_records: list[EvidenceRecord]
    affected_services: list[AffectedService]
    timeline: list[InvestigationEvent]
    source_status: dict[str, EvidenceState]
    confidence: float = Field(ge=0.0, le=1.0)
    confidence_method: Literal["deterministic_heuristic_v1"]
    recommended_action: str
    recommended_action_evidence_ids: list[str] = Field(default_factory=list)
    missing_evidence: list[str] = Field(default_factory=list)
    degraded: bool = False
    limitations: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_citation_references(self) -> "InvestigationResult":
        records = {record.evidence_id: record for record in self.evidence_records}
        if len(records) != len(self.evidence_records):
            raise ValueError("evidence_records contains duplicate evidence IDs")

        citations: dict[str, EvidenceCitation] = {}
        for citation in self.evidence:
            record = records.get(citation.evidence_id)
            if record is None:
                raise ValueError(f"citation references missing evidence ID: {citation.evidence_id}")
            if (citation.source, citation.service, citation.description, citation.trace_id) != (
                record.source,
                record.service,
                record.description,
                record.trace_id,
            ):
                raise ValueError(f"citation does not match evidence record: {citation.evidence_id}")
            if citation.evidence_id in citations:
                raise ValueError(f"duplicate citation for evidence ID: {citation.evidence_id}")
            citations[citation.evidence_id] = citation

        reference_ids = set(citations)
        timeline_refs = {
            event.evidence_id for event in self.timeline if event.evidence_id is not None
        }
        if not timeline_refs.issubset(records):
            raise ValueError("timeline references an evidence ID not present in evidence_records")
        all_claim_refs = set(self.root_cause_evidence_ids) | set(
            self.recommended_action_evidence_ids
        )
        all_claim_refs.update(
            evidence_id
            for affected in self.affected_services
            for evidence_id in affected.evidence_ids
        )
        missing_refs = all_claim_refs - reference_ids
        if missing_refs:
            raise ValueError(f"claim references uncited evidence IDs: {sorted(missing_refs)}")

        if self.outcome == "root_cause_identified":
            if not self.root_cause or not self.root_cause_evidence_ids:
                raise ValueError("identified root cause requires text and supporting citations")
            if not self.recommended_action_evidence_ids:
                raise ValueError("identified investigation requires action evidence citations")
        elif self.root_cause is not None or self.root_cause_evidence_ids:
            raise ValueError("insufficient-evidence result must not assert a root cause")
        elif self.confidence > 0.25:
            raise ValueError("insufficient-evidence confidence must not exceed 0.25")
        return self


class GroundTruth(BaseModel):
    """Evaluation-only record of the controlled injection and observable expectations."""

    experiment_id: UUID
    injected_failure_type: str
    expected_root_cause: str
    expected_affected_services: list[str]
    root_cause_determinable: bool


class RemediationAction(BaseModel):
    action_id: str
    action: Literal["stop_experiment", "verify_recovery", "inspect_dependency"]
    description: str = Field(min_length=1)
    priority: Literal["high", "medium", "low"]
    rationale: str = Field(min_length=1)
    expected_effect: str = Field(min_length=1)
    evidence_ids: list[str] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_recommendation_language(self) -> "RemediationAction":
        if self.action == "stop_experiment":
            expected = (
                "Stop the controlled experiment",
                "The persisted experiment lifecycle shows that the controlled failure is running.",
                "The injected failure should no longer be active after the experiment is stopped.",
                "high",
            )
            actual = (self.description, self.rationale, self.expected_effect, self.priority)
            if actual != expected:
                raise ValueError("stop recommendation contains unsupported claims or priority")
        elif self.action == "verify_recovery":
            expected = (
                "Check target health and repeat the affected request",
                "The investigation found target-service failure evidence during this experiment.",
                "A successful health check and request after the experiment would support "
                "recovery; this plan does not assert that recovery has occurred.",
                "medium",
            )
            actual = (self.description, self.rationale, self.expected_effect, self.priority)
            if actual != expected:
                raise ValueError("recovery recommendation contains unsupported claims or priority")
        elif self.action == "inspect_dependency":
            if (
                not self.description.startswith("Recheck ")
                or not self.description.endswith(" after target recovery")
                or "cited request and dependency evidence" not in self.rationale
                or not self.expected_effect.startswith("Repeating the dependent request")
                or self.priority != "low"
            ):
                raise ValueError("dependency recommendation is not supported by its template")
        return self


class RemediationPlan(BaseModel):
    plan_id: str
    investigation_id: UUID
    experiment_id: UUID
    outcome: Literal["planned", "insufficient_evidence"]
    actions: list[RemediationAction]
    evidence_records: list[EvidenceRecord]
    limitations: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_plan_references(self) -> "RemediationPlan":
        records = {record.evidence_id for record in self.evidence_records}
        if len(records) != len(self.evidence_records):
            raise ValueError("remediation evidence contains duplicate IDs")
        action_ids = [action.action_id for action in self.actions]
        if len(set(action_ids)) != len(action_ids):
            raise ValueError("remediation action IDs must be unique")
        if any(not set(action.evidence_ids).issubset(records) for action in self.actions):
            raise ValueError("remediation action references evidence outside its investigation")
        for action in self.actions:
            expected_id = (
                "act_"
                + hashlib.sha256(f"{self.investigation_id}:{action.action}".encode()).hexdigest()[
                    :20
                ]
            )
            if action.action_id != expected_id:
                raise ValueError("remediation action ID is not stable for this investigation")
            referenced = [
                record
                for record in self.evidence_records
                if record.evidence_id in action.evidence_ids
            ]
            if action.action == "stop_experiment" and not any(
                record.source == "experiment" and record.event == "failure_started"
                for record in referenced
            ):
                raise ValueError("stop recommendation requires lifecycle evidence")
            if action.action == "verify_recovery" and not any(
                record.source in {"service", "log", "trace"}
                and record.service
                == next(
                    (
                        item.service
                        for item in self.evidence_records
                        if item.source == "experiment" and item.event == "created"
                    ),
                    None,
                )
                for record in referenced
            ):
                raise ValueError("recovery recommendation requires target-service evidence")
            if action.action == "inspect_dependency" and not (
                any(record.source == "dependency" for record in referenced)
                and any(record.source == "log" for record in referenced)
            ):
                raise ValueError(
                    "dependency recommendation requires request and dependency evidence"
                )
        if self.outcome == "insufficient_evidence" and self.actions:
            raise ValueError("insufficient evidence must not produce remediation actions")
        if self.outcome == "planned" and not self.actions:
            raise ValueError("planned remediation must contain at least one action")
        return self


class ExperimentVerificationRequest(BaseModel):
    verification_experiment_id: UUID


class MetricComparison(BaseModel):
    metric: str
    baseline_value: float | int | str | None
    verification_value: float | int | str | None
    direction: Literal["improved", "unchanged", "regressed", "not_comparable"]
    unit: str | None = None


class ExperimentVerification(BaseModel):
    baseline_experiment_id: UUID
    verification_experiment_id: UUID
    baseline_investigation_id: UUID
    verification_investigation_id: UUID
    baseline_window_start: datetime | None
    baseline_window_end: datetime | None
    verification_window_start: datetime | None
    verification_window_end: datetime | None
    status: Literal["improved", "no_significant_change", "regressed", "insufficient_data"]
    comparisons: list[MetricComparison]
    limitations: list[str] = Field(default_factory=list)


class EvaluationDimension(BaseModel):
    correct: bool
    explanation: str


class EvaluationMetric(BaseModel):
    value: float = Field(ge=0.0, le=1.0)
    explanation: str


class InvestigationEvaluation(BaseModel):
    investigation_id: UUID
    experiment_id: UUID
    root_cause: EvaluationDimension
    affected_services: EvaluationDimension
    evidence_citations: EvaluationDimension
    abstention: EvaluationDimension
    evidence_precision: EvaluationMetric
    evidence_coverage: EvaluationMetric
    unsupported_claim_rate: EvaluationMetric
    overall: Literal["correct_investigation", "unsupported_or_incorrect"]