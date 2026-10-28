import asyncio
import json
import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import httpx
import pytest

from app.core.config import Settings
from app.models.entities import Experiment
from app.schemas.api import EvidenceMetrics, ServiceObservation
from app.schemas.investigation import (
    EvidenceCitation,
    EvidenceRecord,
    EvidenceState,
    ExperimentContext,
    GroundTruth,
    InvestigationContext,
    InvestigationEvent,
    NormalizedLog,
    NormalizedSpan,
    RemediationPlan,
    ServiceState,
    evidence_identifier,
)
from app.services.evaluation import GroundTruthBuilder, InvestigationEvaluator
from app.services.evidence import PrometheusEvidenceCollector
from app.services.gemini_investigator import (
    GeminiReasoningProvider,
    _recommendation_matches_evidence,
)
from app.services.investigation_evidence import InvestigationEvidenceService
from app.services.investigation_workflow import InvestigationWorkflow
from app.services.investigator import (
    Investigator,
    LocalInvestigator,
    MockLLMProvider,
    create_investigator_provider,
)
from app.services.log_provider import LogCollectionResult, LogProvider
from app.services.remediation import RemediationPlanner
from app.services.trace_client import (
    TraceClient,
    TraceLookupResult,
    _attributes,
    _time,
)
from app.services.verification import ExperimentVerifier


def make_record(
    source: str,
    *,
    service: str | None,
    timestamp: datetime,
    event: str,
    description: str,
    trace_id: str | None = None,
    details: dict | None = None,
) -> EvidenceRecord:
    values = details or {}

    evidence_id = evidence_identifier(
        source=source,
        service=service,
        observed_at=timestamp,
        event=event,
        description=description,
        trace_id=trace_id,
        details=values,
    )

    return EvidenceRecord(
        evidence_id=evidence_id,
        source=source,
        service=service,
        observed_at=timestamp,
        event=event,
        description=description,
        trace_id=trace_id,
        details=values,
    )


def make_context(
    *,
    insufficient: bool = False,
    contradictory: bool = False,
) -> InvestigationContext:
    now = datetime.now(UTC)

    started = None if insufficient else now - timedelta(minutes=10)
    created = (started or now) - timedelta(minutes=1)

    trace_id = "4d0a9cc8a06e97cfa85ac186130d504d"
    experiment_id = uuid.uuid4()

    experiment = ExperimentContext(
        experiment_id=experiment_id,
        target_service="payment-service",
        failure_type="unavailable",
        configured_duration_seconds=600,
        status="CREATED" if insufficient else "RUNNING",
        created_at=created,
        started_at=started,
        ended_at=None,
    )

    records = [
        make_record(
            "experiment",
            service="payment-service",
            timestamp=created,
            event="created",
            description="Unavailable failure experiment created.",
            details={
                "experiment_id": str(experiment_id),
                "status": experiment.status,
            },
        )
    ]

    if started:
        records.append(
            make_record(
                "experiment",
                service="payment-service",
                timestamp=started,
                event="failure_started",
                description="Persisted experiment start.",
                details={
                    "experiment_id": str(experiment_id),
                    "status": "RUNNING",
                },
            )
        )

    service_status = "healthy" if contradictory else "unavailable"
    health_code = 200 if contradictory else 503

    health_record = make_record(
        "service",
        service="payment-service",
        timestamp=now,
        event="health_observation",
        description=f"Current health observation: {service_status} (HTTP {health_code}).",
        details={
            "status": service_status,
            "status_code": health_code,
            "observation_is_current": True,
        },
    )

    target_log_status = 503

    target_log = make_record(
        "log",
        service="payment-service",
        timestamp=now - timedelta(seconds=2),
        event="request_completed",
        description="/pay returned HTTP 503.",
        trace_id=trace_id,
        details={
            "route": "/pay",
            "status_code": target_log_status,
        },
    )

    order_log = make_record(
        "log",
        service="order-service",
        timestamp=now - timedelta(seconds=1),
        event="request_completed",
        description="/orders returned HTTP 502.",
        trace_id=trace_id,
        details={
            "route": "/orders",
            "status_code": 502,
        },
    )

    span = NormalizedSpan(
        trace_id=trace_id,
        span_id="1122334455667788",
        parent_span_id="8877665544332211",
        service_name="failurelab-payment-service",
        operation_name="POST /pay",
        start_time=now - timedelta(seconds=3),
        end_time=now - timedelta(seconds=2),
        duration_ms=1000,
        status="STATUS_CODE_ERROR",
        attributes={
            "http.status_code": 503,
        },
    )

    span_record = make_record(
        "trace",
        service="payment-service",
        timestamp=span.start_time,
        event="span_observed",
        description="POST /pay returned an error span.",
        trace_id=trace_id,
        details={
            "span_id": span.span_id,
            "parent_span_id": span.parent_span_id,
            "status": span.status,
            "http.status_code": 503,
        },
    )

    metric_window_start = now - timedelta(minutes=4)

    metric_record = make_record(
        "metric",
        service="payment-service",
        timestamp=now,
        event="metric_observation",
        description="Prometheus http_5xx_rate=0.25 over a bounded window.",
        details={
            "metric_name": "http_5xx_rate",
            "value": 0.25,
            "window_start": metric_window_start.isoformat(),
            "window_end": now.isoformat(),
            "window_seconds": 240,
        },
    )

    dependency = make_record(
        "dependency",
        service="order-service",
        timestamp=created,
        event="configured_dependency",
        description="Configured service relationship: order-service -> payment-service.",
        details={
            "downstream_service": "payment-service",
        },
    )

    if not insufficient:
        records.extend(
            [
                health_record,
                target_log,
                order_log,
                span_record,
                metric_record,
                dependency,
            ]
        )

    logs = (
        [
            NormalizedLog(
                timestamp=now - timedelta(seconds=2),
                service="payment-service",
                level="info",
                message="request completed",
                route="/pay",
                status_code=503,
                trace_id=trace_id,
            ),
            NormalizedLog(
                timestamp=now - timedelta(seconds=1),
                service="order-service",
                level="info",
                message="request completed",
                route="/orders",
                status_code=502,
                trace_id=trace_id,
            ),
        ]
        if not insufficient
        else []
    )

    if contradictory:
        success_record = make_record(
            "log",
            service="payment-service",
            timestamp=now - timedelta(milliseconds=500),
            event="request_completed",
            description="/pay returned HTTP 200.",
            trace_id=trace_id,
            details={
                "route": "/pay",
                "status_code": 200,
            },
        )

        records.append(success_record)

        logs.append(
            NormalizedLog(
                timestamp=now - timedelta(milliseconds=500),
                service="payment-service",
                level="info",
                message="request completed",
                route="/pay",
                status_code=200,
                trace_id=trace_id,
            )
        )

    state = "unavailable" if insufficient else "available"

    return InvestigationContext(
        experiment=experiment,
        service_states=(
            []
            if insufficient
            else [
                ServiceState(
                    service="payment-service",
                    health_state=service_status,
                    http_status=health_code,
                    observed_at=now,
                    observation_is_current=True,
                )
            ]
        ),
        timeline=[
            InvestigationEvent(
                timestamp=created,
                source="experiment",
                service="payment-service",
                event="created",
                description="Experiment created.",
                evidence_id=records[0].evidence_id,
            )
        ],
        metrics={
            "request_rate": 1.0,
            "http_5xx_rate": 0.25,
            "p95_latency_seconds": 0.1,
        },
        metrics_observed_at=now if not insufficient else None,
        metric_window_start=(
            metric_window_start if not insufficient else None
        ),
        metric_window_end=(
            now if not insufficient else None
        ),
        metrics_state=EvidenceState(
            status=state,
            available=not insufficient,
            message="Prometheus unavailable" if insufficient else None,
        ),
        logs=logs,
        logs_state=EvidenceState(
            status=state,
            available=not insufficient,
            message="All log providers unavailable" if insufficient else None,
        ),
        traces=[] if insufficient else [span],
        traces_state=EvidenceState(
            status="unavailable" if insufficient else "available",
            available=not insufficient,
            message="Tempo unavailable" if insufficient else None,
        ),
        dependencies={
            "order-service": [
                "inventory-service",
                "payment-service",
            ]
        },
        evidence_records=records,
    )


def test_valid_citations_and_indirect_service_claim_reference_real_records() -> None:
    context = make_context()

    result = asyncio.run(
        Investigator(MockLLMProvider()).investigate(context)
    )

    assert result.outcome == "root_cause_identified"

    records = {
        record.evidence_id
        for record in result.evidence_records
    }

    cited = {
        citation.evidence_id
        for citation in result.evidence
    }

    assert cited <= records

    order = next(
        item
        for item in result.affected_services
        if item.service == "order-service"
    )

    order_evidence = set(order.evidence_ids)

    assert order_evidence <= cited

    assert any(
        context_record.service == "order-service"
        and context_record.source == "log"
        and context_record.evidence_id in order_evidence
        for context_record in result.evidence_records
    )

    assert any(
        context_record.source == "dependency"
        and context_record.evidence_id in order_evidence
        for context_record in result.evidence_records
    )

    assert result.root_cause_evidence_ids
    assert result.recommended_action_evidence_ids


def _gemini_assessment(context: InvestigationContext, *, outcome: str = "root_cause_identified") -> dict:
    baseline = asyncio.run(MockLLMProvider().generate("", context))
    target_impact = next(
        (
            item
            for item in baseline["affected_services"]
            if item.service == context.experiment.target_service and item.impact == "direct"
        ),
        None,
    )
    return {
        "outcome": outcome,
        "root_cause": (
            "payment-service returned HTTP failure responses during the experiment window."
            if outcome == "root_cause_identified"
            else None
        ),
        "root_cause_evidence_ids": (
            [
                record.evidence_id
                for record in context.evidence_records
                if record.source == "log" and record.service == "payment-service"
            ][:1]
            if outcome == "root_cause_identified"
            else []
        ),
        "affected_services": (
            [
                {
                    "service": target_impact.service,
                    "impact": target_impact.impact,
                    "evidence_ids": target_impact.evidence_ids[:1],
                }
            ]
            if target_impact and outcome == "root_cause_identified"
            else []
        ),
        "timeline_evidence_ids": [
            event.evidence_id for event in context.timeline if event.evidence_id
        ],
        "confidence_rationale": "Independent log and trace observations corroborate the event.",
        "recommended_action": (
            f"Inspect {context.experiment.target_service} HTTP failures and verify recovery."
        ),
        "recommended_action_evidence_ids": baseline["recommended_action_evidence_ids"][:1],
        "missing_evidence": [],
        "limitations": [],
    }


class _GeminiClient:
    def __init__(self, output: str | Exception) -> None:
        self.output = output
        self.models = self
        self.request = None

    def generate_content(self, **kwargs):
        self.request = kwargs
        if isinstance(self.output, Exception):
            raise self.output
        return SimpleNamespace(text=self.output)


def test_gemini_structured_result_is_built_with_validated_evidence_and_local_confidence() -> None:
    context = make_context()
    output = json.dumps(_gemini_assessment(context))
    client = _GeminiClient(output)
    result = asyncio.run(
        Investigator(GeminiReasoningProvider("test-key", "gemini-test", client)).investigate(context)
    )

    assert result.provider == "gemini"
    assert result.outcome == "root_cause_identified"
    assert result.root_cause.startswith("payment-service returned HTTP failure")
    assert any(
        impact.service == "payment-service" and impact.impact == "direct"
        for impact in result.affected_services
    )
    assert result.confidence_method == "deterministic_heuristic_v1"
    assert result.confidence == asyncio.run(
        Investigator(MockLLMProvider()).investigate(context)
    ).confidence
    assert result.root_cause_evidence_ids[0] in {
        citation.evidence_id for citation in result.evidence
    }
    assert client.request["config"]["response_mime_type"] == "application/json"
    assert client.request["config"]["response_schema"] is not None
    assert "evidence_records" in client.request["contents"]


def test_gemini_nonexistent_evidence_id_is_rejected() -> None:
    context = make_context()
    assessment = _gemini_assessment(context)
    assessment["root_cause_evidence_ids"] = ["ev_not_collected"]
    client = _GeminiClient(json.dumps(assessment))
    with pytest.raises(ValueError, match="outside its validated support"):
        asyncio.run(
            Investigator(GeminiReasoningProvider("test-key", "gemini-test", client)).investigate(context)
        )


def test_gemini_unsupported_claim_is_rejected_even_with_real_ids() -> None:
    context = make_context()
    assessment = _gemini_assessment(context)
    assessment["root_cause"] = (
        "A database pool exhaustion caused HTTP failure responses on payment-service."
    )
    client = _GeminiClient(json.dumps(assessment))
    with pytest.raises(ValueError, match="not supported by the cited observations"):
        asyncio.run(
            Investigator(GeminiReasoningProvider("test-key", "gemini-test", client)).investigate(context)
        )


def test_gemini_ungrounded_recommendation_is_rejected() -> None:
    context = make_context()
    assessment = _gemini_assessment(context)
    assessment["recommended_action"] = (
        "Drop the payment-service database and restart the production cluster."
    )
    client = _GeminiClient(json.dumps(assessment))
    with pytest.raises(ValueError, match="not grounded in the cited evidence"):
        asyncio.run(
            Investigator(GeminiReasoningProvider("test-key", "gemini-test", client)).investigate(context)
        )


def test_gemini_recommendation_can_request_logs_to_investigate_http_failure() -> None:
    observed_at = datetime.now(UTC)
    evidence = make_record(
        "trace",
        service="payment-service",
        timestamp=observed_at,
        event="http_failure_observed",
        description="/pay returned HTTP 500.",
        details={"route": "/pay", "status_code": 500},
    )
    action = (
        "Investigate payment-service on route /pay and inspect application logs "
        "and telemetry to identify why it returned HTTP 500 errors."
    )
    assert _recommendation_matches_evidence(
        action,
        "payment-service",
        "Stop or allow the controlled experiment to expire, then verify recovery.",
        [evidence],
    )


def test_gemini_recommendation_can_name_observed_elevated_latency() -> None:
    evidence = make_record(
        "trace",
        service="inventory-service",
        timestamp=datetime.now(UTC),
        event="latency_observed",
        description="POST /reserve",
        details={
            "route": "/reserve",
            "http.status_code": 200,
            "duration_ms": 1510.0,
        },
    )

    assert _recommendation_matches_evidence(
        "Investigate elevated latency on inventory-service route /reserve.",
        "inventory-service",
        "Stop or allow the controlled experiment to expire, then verify recovery.",
        [evidence],
    )


def test_gemini_insufficient_evidence_abstains_without_a_root_cause() -> None:
    context = make_context(insufficient=True)
    client = _GeminiClient(json.dumps(_gemini_assessment(context, outcome="insufficient_evidence")))
    result = asyncio.run(
        Investigator(GeminiReasoningProvider("test-key", "gemini-test", client)).investigate(context)
    )
    assert result.outcome == "insufficient_evidence"
    assert result.root_cause is None
    assert result.root_cause_evidence_ids == []
    assert result.confidence <= 0.25


def test_gemini_malformed_response_and_api_failure_are_clear_errors() -> None:
    context = make_context()
    malformed = _GeminiClient("not-json")
    with pytest.raises(ValueError, match="malformed structured"):
        asyncio.run(GeminiReasoningProvider("key", "model", malformed).assess(context))
    failed = _GeminiClient(RuntimeError("private sdk details"))
    with pytest.raises(ValueError, match="check server logs") as error:
        asyncio.run(GeminiReasoningProvider("key", "model", failed).assess(context))
    assert "private sdk details" not in str(error.value)


@pytest.mark.parametrize("status", [503, 429])
def test_gemini_transient_failure_uses_explicit_local_fallback(status: int) -> None:
    class UpstreamError(ValueError):
        status_code = status

    context = make_context()
    result = asyncio.run(
        Investigator(
            GeminiReasoningProvider("key", "model", _GeminiClient(UpstreamError("unavailable"))),
            gemini_fallback_to_local=True,
        ).investigate(context)
    )

    assert result.provider == "local_fallback"
    assert result.degraded is True
    assert any(f"HTTP {status}" in item for item in result.limitations)


def test_gemini_transient_failure_without_fallback_preserves_provider_error() -> None:
    class UpstreamError(ValueError):
        status_code = 503

    with pytest.raises(ValueError, match="HTTP 503"):
        asyncio.run(
            Investigator(
                GeminiReasoningProvider("key", "model", _GeminiClient(UpstreamError("unavailable")))
            ).investigate(make_context())
        )


def test_malformed_gemini_output_never_uses_local_fallback() -> None:
    with pytest.raises(ValueError, match="malformed structured"):
        asyncio.run(
            Investigator(
                GeminiReasoningProvider("key", "model", _GeminiClient("not-json")),
                gemini_fallback_to_local=True,
            ).investigate(make_context())
        )


def test_missing_gemini_key_does_not_affect_local_provider() -> None:
    context = make_context()
    with pytest.raises(ValueError, match="GEMINI_API_KEY is not configured"):
        asyncio.run(GeminiReasoningProvider("", "model").assess(context))
    assert asyncio.run(Investigator(MockLLMProvider()).investigate(context)).provider == "mock"


def test_provider_configuration_defaults_local_and_can_select_gemini(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("INVESTIGATOR_PROVIDER", raising=False)
    defaults = Settings(_env_file=None)
    assert defaults.investigator_provider == "local"
    assert isinstance(create_investigator_provider(defaults.investigator_provider), LocalInvestigator)

    gemini_settings = Settings(
        _env_file=None,
        investigator_provider="gemini",
        gemini_api_key="secret-test-value",
        gemini_model="gemini-test-model",
    )
    provider = create_investigator_provider(
        gemini_settings.investigator_provider,
        gemini_api_key=gemini_settings.gemini_api_key,
        gemini_model=gemini_settings.gemini_model,
    )
    assert isinstance(provider, GeminiReasoningProvider)
    assert provider.model == "gemini-test-model"


def test_nonexistent_citation_is_rejected_by_result_schema() -> None:
    result = asyncio.run(
        Investigator(MockLLMProvider()).investigate(
            make_context()
        )
    )

    payload = result.model_dump()
    payload["evidence"][0]["evidence_id"] = "ev_not_collected"

    with pytest.raises(ValueError, match="missing evidence ID"):
        type(result).model_validate(payload)


def test_result_schema_rejects_out_of_range_confidence() -> None:
    result = asyncio.run(
        Investigator(MockLLMProvider()).investigate(
            make_context()
        )
    )

    payload = result.model_dump()
    payload["confidence"] = 1.2

    with pytest.raises(
        ValueError,
        match="less than or equal to 1",
    ):
        type(result).model_validate(payload)


def _delay_context(
    *, failure_type: str, fast_success: bool, status_override: int | None = None
) -> InvestigationContext:
    now = datetime.now(UTC)
    started = now - timedelta(seconds=30)
    experiment_id = uuid.uuid4()
    trace_id = "b" * 32
    target = "inventory-service" if failure_type == "latency" else "payment-service"
    route = "/reserve" if failure_type == "latency" else "/pay"
    status_code = (
        200
        if failure_type == "latency"
        else 504
        if failure_type == "timeout"
        else 500
    )
    if status_override is not None:
        status_code = status_override
    duration = 1510.0 if failure_type == "latency" else 4100.0 if failure_type == "timeout" else 12.0
    records = [
        make_record(
            "experiment",
            service=target,
            timestamp=started,
            event="failure_started",
            description="Persisted experiment start.",
            details={"status": "RUNNING"},
        ),
        make_record(
            "service",
            service=target,
            timestamp=now,
            event="health_observation",
            description="Current health observation: healthy.",
            details={"status": "healthy", "status_code": 200, "observation_is_current": True},
        ),
        make_record(
            "log",
            service=target,
            timestamp=now - timedelta(seconds=3),
            event=(
                "latency_observed"
                if failure_type == "latency" and duration >= 1400 and status_code < 500
                else "timeout_observed"
                if failure_type == "timeout" and status_code == 504
                else "http_failure_observed"
                if status_code >= 500
                else "request_completed"
            ),
            description=f"{route} returned HTTP {status_code}.",
            trace_id=trace_id,
            details={"route": route, "status_code": status_code, "duration_ms": duration},
        ),
        make_record(
            "trace",
            service=target,
            timestamp=now - timedelta(seconds=3),
            event=(
                "latency_observed"
                if failure_type == "latency" and duration >= 1400 and status_code < 500
                else "timeout_observed"
                if failure_type == "timeout" and status_code == 504
                else "http_failure_observed"
                if status_code >= 500
                else "span_observed"
            ),
            description=f"POST {route}",
            trace_id=trace_id,
            details={
                "duration_ms": duration,
                "status": "OK" if failure_type == "latency" else "STATUS_CODE_ERROR",
                "http.route": route,
                "http.status_code": status_code,
            },
        ),
    ]
    if failure_type == "latency":
        records.append(
            make_record(
                "trace",
                service=target,
                timestamp=now - timedelta(seconds=3),
                event="span_observed",
                description="POST /reserve http send completed with status UNSET in 0.1ms.",
                trace_id=trace_id,
                details={
                    "duration_ms": 0.1,
                    "status": "UNSET",
                    "http.route": route,
                    "http.status_code": 200,
                    "asgi.event.type": "http.response.start",
                },
            )
        )
    if fast_success:
        records.append(
            make_record(
                "log",
                service=target,
                timestamp=now - timedelta(seconds=1),
                event="request_completed",
                description=f"{route} returned HTTP 200.",
                details={"route": route, "status_code": 200, "duration_ms": 12},
            )
        )
    available = EvidenceState(status="available", available=True)
    return InvestigationContext(
        experiment=ExperimentContext(
            experiment_id=experiment_id,
            target_service=target,
            failure_type=failure_type,
            configured_duration_seconds=30,
            status="RUNNING",
            created_at=started - timedelta(seconds=1),
            started_at=started,
            ended_at=None,
        ),
        service_states=[],
        timeline=[],
        metrics={},
        metrics_observed_at=now,
        metric_window_start=started,
        metric_window_end=now,
        metrics_state=available,
        logs=[],
        logs_state=available,
        traces=[],
        traces_state=available,
        dependencies={"order-service": ["inventory-service", "payment-service"]},
        evidence_records=records,
    )


def test_latency_and_timeout_use_their_own_signals() -> None:
    investigator = Investigator(MockLLMProvider())
    latency = asyncio.run(investigator.investigate(_delay_context(failure_type="latency", fast_success=False)))
    contradicted = asyncio.run(investigator.investigate(_delay_context(failure_type="latency", fast_success=True)))
    timeout = asyncio.run(investigator.investigate(_delay_context(failure_type="timeout", fast_success=False)))
    assert latency.outcome == "root_cause_identified"
    assert latency.root_cause is not None
    assert "elevated request duration" in latency.root_cause
    assert "inventory-service" in latency.root_cause
    assert contradicted.outcome == "insufficient_evidence"
    assert contradicted.root_cause is None
    assert timeout.outcome == "root_cause_identified"
    assert timeout.root_cause is not None
    assert "timeout" in timeout.root_cause
    assert {record.evidence_id for record in latency.evidence_records} >= set(
        latency.root_cause_evidence_ids
    )
    latency_citations = {item.evidence_id for item in latency.evidence}
    cited_records = {
        record.evidence_id: record
        for record in latency.evidence_records
        if record.evidence_id in latency_citations
    }
    assert any(
        record.source == "log"
        and record.event == "latency_observed"
        and record.details.get("status_code") == 200
        and record.details.get("duration_ms", 0) >= 1400
        for record in cited_records.values()
    )
    assert any(record.source == "trace" for record in cited_records.values())
    timeout_cited = {item.evidence_id for item in timeout.evidence}
    assert any(
        record.evidence_id in timeout_cited
        and record.source == "log"
        and record.details.get("status_code") == 504
        for record in timeout.evidence_records
    )


@pytest.mark.parametrize(
    ("failure_type", "claim"),
    [
        ("latency", "inventory-service had elevated request latency during the experiment."),
        ("timeout", "payment-service experienced timeout behavior during the experiment."),
    ],
)
def test_gemini_claims_for_latency_and_timeout_require_matching_telemetry(
    failure_type: str, claim: str
) -> None:
    context = _delay_context(failure_type=failure_type, fast_success=False)
    baseline = asyncio.run(MockLLMProvider().generate("", context))
    support_records = [
        record
        for record in context.evidence_records
        if record.source in {"log", "trace"}
        and record.event in {"latency_observed", "timeout_observed"}
    ]
    assessment = {
        "outcome": "root_cause_identified",
        "root_cause": claim,
        "root_cause_evidence_ids": [record.evidence_id for record in support_records],
        "affected_services": [],
        "timeline_evidence_ids": [],
        "confidence_rationale": "Corroborated by request evidence.",
        "recommended_action": (
            f"Stop the controlled {context.experiment.target_service} failure and verify recovery."
        ),
        "recommended_action_evidence_ids": baseline["recommended_action_evidence_ids"][:1],
        "missing_evidence": [],
        "limitations": [],
    }
    client = _GeminiClient(json.dumps(assessment))
    result = asyncio.run(
        Investigator(GeminiReasoningProvider("test-key", "gemini-test", client)).investigate(context)
    )
    assert result.outcome == "root_cause_identified"
    assert result.root_cause == claim


def test_http_500_uses_http_failure_records_as_cited_root_cause_evidence() -> None:
    context = _delay_context(failure_type="http_500", fast_success=False)
    context = context.model_copy(
        update={
            "evidence_records": [
                record for record in context.evidence_records if record.source != "service"
            ]
        }
    )
    result = asyncio.run(
        Investigator(MockLLMProvider()).investigate(
            context
        )
    )
    assert result.outcome == "root_cause_identified"
    citation_ids = {item.evidence_id for item in result.evidence}
    cited_failure_records = [
        record
        for record in result.evidence_records
        if record.evidence_id in citation_ids
        and record.service == "payment-service"
        and record.source in {"log", "trace"}
        and (
            record.details.get("status_code") == 500
            or record.details.get("http.status_code") == 500
        )
    ]
    assert {record.source for record in cited_failure_records} == {"log", "trace"}


def test_http_500_does_not_prove_timeout_and_unavailable_telemetry_abstains() -> None:
    investigator = Investigator(MockLLMProvider())
    generic_http_error = asyncio.run(
        investigator.investigate(
            _delay_context(failure_type="timeout", fast_success=False, status_override=500)
        )
    )
    assert generic_http_error.outcome == "insufficient_evidence"
    assert generic_http_error.root_cause is None

    for failure_type in ("latency", "timeout"):
        context = _delay_context(failure_type=failure_type, fast_success=False)
        context = context.model_copy(
            update={
                "evidence_records": [
                    record for record in context.evidence_records if record.source == "experiment"
                ],
                "logs_state": EvidenceState(
                    status="unavailable", available=False, message="logs unavailable"
                ),
                "traces_state": EvidenceState(
                    status="unavailable", available=False, message="traces unavailable"
                ),
                "metrics_state": EvidenceState(
                    status="unavailable", available=False, message="metrics unavailable"
                ),
            }
        )
        result = asyncio.run(investigator.investigate(context))
        assert result.outcome == "insufficient_evidence"
        assert result.root_cause is None


def test_insufficient_and_contradictory_evidence_do_not_claim_root_cause() -> None:
    investigator = Investigator(MockLLMProvider())

    insufficient = asyncio.run(
        investigator.investigate(
            make_context(insufficient=True)
        )
    )

    contradictory = asyncio.run(
        investigator.investigate(
            make_context(contradictory=True)
        )
    )

    assert insufficient.outcome == "insufficient_evidence"
    assert insufficient.root_cause is None
    assert insufficient.confidence <= 0.25
    assert insufficient.missing_evidence

    assert contradictory.outcome == "insufficient_evidence"
    assert contradictory.root_cause is None
    assert any(
        "conflict" in message
        for message in contradictory.missing_evidence
    )


def test_confidence_is_deterministic_and_responds_to_corroboration() -> None:
    context = make_context()
    investigator = Investigator(MockLLMProvider())

    full = asyncio.run(
        investigator.investigate(context)
    )

    repeated = asyncio.run(
        investigator.investigate(context)
    )

    reduced_context = context.model_copy(
        update={
            "traces": [],
            "traces_state": EvidenceState(
                status="not_found",
                available=False,
            ),
            "evidence_records": [
                record
                for record in context.evidence_records
                if record.source != "trace"
            ],
            "metric_window_start": (
                context.experiment.started_at
                - timedelta(seconds=1)
            ),
        }
    )

    reduced = asyncio.run(
        investigator.investigate(reduced_context)
    )

    assert full.confidence == repeated.confidence
    assert full.confidence > reduced.confidence
    assert full.confidence_method == "deterministic_heuristic_v1"


def test_trace_normalization_helpers_and_stable_id() -> None:
    assert _attributes(
        [
            {
                "key": "http.method",
                "value": {
                    "stringValue": "POST"
                },
            }
        ]
    ) == {
        "http.method": "POST"
    }

    assert _time("1000000000").year == 1970


def test_tempo_batches_normalize_span_ids_parent_and_status() -> None:
    from app.services.trace_client import normalize_tempo_trace

    payload = {
        "batches": [
            {
                "resource": {
                    "attributes": [
                        {
                            "key": "service.name",
                            "value": {
                                "stringValue": (
                                    "failurelab-payment-service"
                                )
                            },
                        }
                    ]
                },
                "scopeSpans": [
                    {
                        "spans": [
                            {
                                "traceId": (
                                    "AQIDBAUGBwgJCgsMDQ4PEA=="
                                ),
                                "spanId": "ERITFBUWFxg=",
                                "parentSpanId": "iHdmVUQzIhE=",
                                "name": "POST /pay",
                                "startTimeUnixNano": "1000000000",
                                "endTimeUnixNano": "2000000000",
                                "status": {
                                    "code": "STATUS_CODE_ERROR"
                                },
                                "attributes": [
                                    {
                                        "key": "http.status_code",
                                        "value": {
                                            "intValue": "503"
                                        },
                                    }
                                ],
                            }
                        ]
                    }
                ],
            }
        ]
    }

    spans = normalize_tempo_trace(
        payload,
        "requested",
    )

    assert len(spans) == 1
    assert (
        spans[0].trace_id
        == "0102030405060708090a0b0c0d0e0f10"
    )
    assert spans[0].span_id == "1112131415161718"
    assert spans[0].parent_span_id == "8877665544332211"
    assert (
        spans[0].service_name
        == "failurelab-payment-service"
    )
    assert spans[0].duration_ms == 1000


def test_trace_client_distinguishes_not_found_and_unavailable() -> None:
    async def fetch(status_code: int):
        transport = httpx.MockTransport(
            lambda request: httpx.Response(
                status_code,
                json={"batches": []},
            )
        )

        return await TraceClient(
            "http://tempo",
            transport=transport,
        ).get_trace("trace-id")

    not_found = asyncio.run(fetch(404))
    unavailable = asyncio.run(fetch(503))

    assert not_found.status == "not_found"
    assert unavailable.status == "unavailable"


def test_log_provider_reports_each_partial_provider_failure() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host in {
            "inventory",
            "payment",
        }:
            return httpx.Response(503)

        return httpx.Response(
            200,
            json=[],
        )

    provider = LogProvider(
        "http://control",
        service_urls={
            "order-service": "http://order",
            "inventory-service": "http://inventory",
            "payment-service": "http://payment",
        },
        transport=httpx.MockTransport(handler),
    )

    result = asyncio.run(
        provider.collect()
    )

    assert result.status == "partial"

    assert result.failed_providers == [
        "inventory-service",
        "payment-service",
    ]


def test_prometheus_metrics_are_labeled_with_their_actual_query_window() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "data": {
                    "result": [
                        {
                            "value": [
                                "1000",
                                "1.25",
                            ]
                        }
                    ]
                }
            },
        )

    collector = PrometheusEvidenceCollector(
        "http://prometheus",
        transport=httpx.MockTransport(handler),
    )

    metrics = asyncio.run(
        collector.collect_metrics(
            "payment-service"
        )
    )

    assert metrics.observed_at is not None
    assert metrics.window_start is not None
    assert metrics.window_end == metrics.observed_at
    assert metrics.window_seconds == 300
    assert (
        metrics.window_end - metrics.window_start
        == timedelta(seconds=300)
    )


def test_window_metrics_use_the_incident_interval_and_do_not_invent_samples() -> None:
    queries: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        queries.append(request.url.params["query"])
        if "payment-service" in request.url.params["query"]:
            return httpx.Response(200, json={"data": {"result": [{"value": ["1", "4"]}]}})
        return httpx.Response(200, json={"data": {"result": []}})

    collector = PrometheusEvidenceCollector(
        "http://prometheus",
        transport=httpx.MockTransport(handler),
    )
    start = datetime(2026, 10, 11, tzinfo=UTC)
    end = start + timedelta(seconds=30)
    metrics = asyncio.run(collector.collect_window_metrics(start, end))
    by_service = {item.service: item for item in metrics}
    assert by_service["payment-service"].available is True
    assert by_service["payment-service"].request_count == 4
    assert by_service["order-service"].available is False
    assert by_service["order-service"].request_count is None
    assert "increase(failurelab_http_requests_total" in queries[0]
    assert "[30s]" in queries[0]
    assert all("5m" not in query for query in queries)


def test_service_health_observation_has_current_timestamp() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        status = (
            503
            if request.url.port == 8003
            else 200
        )

        return httpx.Response(status)

    collector = PrometheusEvidenceCollector(
        transport=httpx.MockTransport(handler)
    )

    observations = asyncio.run(
        collector.collect_service_observations()
    )

    assert len(observations) == 3

    assert all(
        observation.observed_at.tzinfo == UTC
        for observation in observations
    )

    payment = next(
        item
        for item in observations
        if item.service == "payment-service"
    )

    assert payment.status_code == 503


class FakeMetrics:
    async def collect_metrics(
        self,
        target_service: str,
    ) -> EvidenceMetrics:
        now = datetime.now(UTC)

        return EvidenceMetrics(
            available=True,
            http_5xx_rate=0.1,
            observed_at=now,
            window_start=now - timedelta(minutes=5),
            window_end=now,
            window_seconds=300,
        )

    async def collect_service_observations(
        self,
    ) -> list[ServiceObservation]:
        now = datetime.now(UTC)

        return [
            ServiceObservation(
                service="payment-service",
                url="http://payment",
                status="unavailable",
                status_code=503,
                observed_at=now,
            )
        ]


class FakeLogs:
    async def collect(self) -> LogCollectionResult:
        return LogCollectionResult(
            records=[],
            provider_status={
                "payment-service": "available"
            },
        )


class FakeTraces:
    async def get_trace(
        self,
        trace_id: str,
    ) -> TraceLookupResult:
        return TraceLookupResult(
            status="not_found"
        )


class OneTraceLogProvider:
    async def collect(self) -> LogCollectionResult:
        return LogCollectionResult(
            records=[
                NormalizedLog(
                    timestamp=datetime.now(UTC),
                    service="payment-service",
                    level="info",
                    message="request completed",
                    route="/pay",
                    status_code=503,
                    trace_id="a" * 32,
                )
            ],
            provider_status={
                "payment-service": "available"
            },
        )


class UnavailableTraces:
    async def get_trace(
        self,
        trace_id: str,
    ) -> TraceLookupResult:
        return TraceLookupResult(
            status="unavailable",
            message="Tempo down",
        )


def test_collector_preserves_tempo_not_found_vs_unavailable() -> None:
    now = datetime.now(UTC)

    experiment = Experiment(
        id=uuid.uuid4(),
        target_service="payment-service",
        failure_type="unavailable",
        duration_seconds=30,
        status="RUNNING",
        created_at=now - timedelta(seconds=10),
        started_at=now - timedelta(seconds=5),
    )

    unavailable = asyncio.run(
        InvestigationEvidenceService(
            FakeMetrics(),
            OneTraceLogProvider(),
            UnavailableTraces(),
        ).collect(experiment)
    )

    not_found = asyncio.run(
        InvestigationEvidenceService(
            FakeMetrics(),
            OneTraceLogProvider(),
            FakeTraces(),
        ).collect(experiment)
    )

    assert unavailable.traces_state.status == "unavailable"
    assert "Tempo" in unavailable.traces_state.message
    assert not_found.traces_state.status == "not_found"


def test_evidence_context_preserves_health_timestamp_and_metric_window() -> None:
    now = datetime.now(UTC)

    experiment = Experiment(
        id=uuid.uuid4(),
        target_service="payment-service",
        failure_type="unavailable",
        duration_seconds=30,
        status="RUNNING",
        created_at=now - timedelta(seconds=5),
        started_at=now - timedelta(seconds=4),
    )

    context = asyncio.run(
        InvestigationEvidenceService(
            FakeMetrics(),
            FakeLogs(),
            FakeTraces(),
        ).collect(experiment)
    )

    assert context.service_states[0].observed_at.tzinfo == UTC
    assert context.service_states[0].observation_is_current

    assert context.metric_window_start is not None
    assert (
        context.metric_window_end
        - context.metric_window_start
        == timedelta(minutes=5)
    )

    assert context.timeline == sorted(
        context.timeline,
        key=lambda event: event.timestamp,
    )

    assert all(
        event.evidence_id is None
        or event.evidence_id
        in {
            record.evidence_id
            for record in context.evidence_records
        }
        for event in context.timeline
    )


def _result(context: InvestigationContext):
    return asyncio.run(
        Investigator(
            MockLLMProvider()
        ).investigate(context)
    )


def test_remediation_is_associated_cited_and_rejects_unsupported_content() -> None:
    result = _result(make_context())

    plan = RemediationPlanner().plan(result)

    catalog = {
        record.evidence_id
        for record in result.evidence_records
    }

    assert plan.investigation_id == result.investigation_id
    assert plan.outcome == "planned"
    assert plan.actions

    assert all(
        set(action.evidence_ids) <= catalog
        for action in plan.actions
    )

    stop = next(
        action
        for action in plan.actions
        if action.action == "stop_experiment"
    )

    assert stop.priority == "high"

    invalid = plan.model_dump()
    invalid["actions"][0]["evidence_ids"] = [
        "ev_missing"
    ]

    with pytest.raises(
        ValueError,
        match="references evidence",
    ):
        RemediationPlan.model_validate(invalid)

    unsupported = plan.model_dump()
    unsupported["actions"][0]["rationale"] = (
        "The database was corrupted by an attacker."
    )

    with pytest.raises(
        ValueError,
        match="unsupported claims",
    ):
        RemediationPlan.model_validate(unsupported)

    invalid_priority = plan.model_dump()
    invalid_priority["actions"][0]["priority"] = "low"

    with pytest.raises(
        ValueError,
        match="priority",
    ):
        RemediationPlan.model_validate(invalid_priority)


def test_insufficient_investigation_does_not_generate_remediation() -> None:
    result = _result(
        make_context(insufficient=True)
    )

    plan = RemediationPlanner().plan(result)

    assert plan.outcome == "insufficient_evidence"
    assert plan.actions == []


def test_ground_truth_is_separate_from_investigator_context() -> None:
    context = make_context()

    class Collector:
        async def collect(self, experiment):
            return context

    class MemoryRepository:
        saved = None

        def save(self, result, truth):
            self.saved = (result, truth)

    repository = MemoryRepository()

    class SpyProvider(MockLLMProvider):
        async def generate(
            self,
            prompt,
            provider_context,
        ):
            assert not hasattr(
                provider_context,
                "ground_truth",
            )

            assert not hasattr(
                provider_context.experiment,
                "expected_root_cause",
            )

            return await super().generate(
                prompt,
                provider_context,
            )

    experiment = Experiment(
        id=context.experiment.experiment_id,
        target_service=context.experiment.target_service,
        failure_type=context.experiment.failure_type,
        duration_seconds=context.experiment.configured_duration_seconds,
        mode="failure",
        status=context.experiment.status,
        created_at=context.experiment.created_at,
        started_at=context.experiment.started_at,
        ended_at=context.experiment.ended_at,
    )

    workflow = InvestigationWorkflow(
        Collector(),
        Investigator(SpyProvider()),
        repository,
        GroundTruthBuilder(),
    )

    result = asyncio.run(
        workflow.run(experiment)
    )

    assert (
        repository.saved[0].investigation_id
        == result.investigation_id
    )

    assert isinstance(
        repository.saved[1],
        GroundTruth,
    )

    assert repository.saved[1].expected_root_cause

    assert "ground_truth" not in result.model_dump()


def test_evaluator_scores_correct_and_incorrect_findings_and_abstention() -> None:
    evaluator = InvestigationEvaluator()
    context = make_context()
    truth = GroundTruthBuilder().build(context)
    correct = _result(context)

    evaluated = evaluator.evaluate(
        correct,
        truth,
    )

    assert evaluated.overall == "correct_investigation"
    assert evaluated.root_cause.correct
    assert evaluated.affected_services.correct
    assert evaluated.evidence_citations.correct
    assert evaluated.abstention.correct

    # ---------------------------------------------------------
    # Metric validity
    # ---------------------------------------------------------

    assert 0.0 <= evaluated.evidence_precision.value <= 1.0
    assert 0.0 <= evaluated.evidence_coverage.value <= 1.0
    assert 0.0 <= evaluated.unsupported_claim_rate.value <= 1.0

    assert evaluated.evidence_precision.value > 0.0
    assert evaluated.evidence_coverage.value > 0.0

    # ---------------------------------------------------------
    # Evidence precision
    # ---------------------------------------------------------
    #
    # Deliberately add unrelated evidence.
    #
    # This must not depend on the existing fixture containing
    # an irrelevant record. We explicitly create one here.
    # ---------------------------------------------------------

    irrelevant_record = make_record(
        "log",
        service="unrelated-service",
        timestamp=datetime.now(UTC),
        event="unrelated_event",
        description=(
            "An unrelated service produced a normal log entry."
        ),
        details={
            "route": "/health",
            "status_code": 200,
        },
    )

    irrelevant_citation = EvidenceCitation(
        evidence_id=irrelevant_record.evidence_id,
        claim="Unrelated evidence",
        source=irrelevant_record.source,
        service=irrelevant_record.service,
        description=irrelevant_record.description,
        trace_id=irrelevant_record.trace_id,
    )

    noisy_result = correct.model_copy(
        update={
            "evidence_records": [
                *correct.evidence_records,
                irrelevant_record,
            ],
            "evidence": [
                *correct.evidence,
                irrelevant_citation,
            ],
        }
    )

    noisy_evaluation = evaluator.evaluate(
        noisy_result,
        truth,
    )

    assert noisy_evaluation.root_cause.correct
    assert noisy_evaluation.affected_services.correct
    assert noisy_evaluation.evidence_citations.correct

    assert (
        noisy_evaluation.evidence_precision.value
        < evaluated.evidence_precision.value
    )

    # ---------------------------------------------------------
    # Evidence coverage
    # ---------------------------------------------------------
    #
    # Remove one relevant root-cause citation.
    # Coverage should decrease.
    # ---------------------------------------------------------

    cited_ids = {
        citation.evidence_id
        for citation in correct.evidence
    }

    root_cause_ids = set(
        correct.root_cause_evidence_ids
    )

    removable_root_cause_id = next(
        evidence_id
        for evidence_id in correct.root_cause_evidence_ids
        if evidence_id in cited_ids
        and any(
            record.evidence_id == evidence_id
            and record.source in {"service", "log", "trace", "metric"}
            and record.service in truth.expected_affected_services
            for record in correct.evidence_records
        )
    )

    reduced_evidence = [
        citation
        for citation in correct.evidence
        if citation.evidence_id != removable_root_cause_id
    ]

    reduced_root_cause_ids = [
        evidence_id
        for evidence_id in correct.root_cause_evidence_ids
        if evidence_id != removable_root_cause_id
    ]

    reduced_result = correct.model_copy(
        update={
            "evidence": reduced_evidence,
            "root_cause_evidence_ids": reduced_root_cause_ids,
        }
    )

    reduced_evaluation = evaluator.evaluate(
        reduced_result,
        truth,
    )

    assert (
        reduced_evaluation.evidence_coverage.value
        < evaluated.evidence_coverage.value
    )

    # ---------------------------------------------------------
    # Existing correctness checks
    # ---------------------------------------------------------

    wrong_root = correct.model_copy(
        update={
            "root_cause": "database corruption"
        }
    )

    assert not evaluator.evaluate(
        wrong_root,
        truth,
    ).root_cause.correct

    wrong_affected = correct.model_copy(
        update={
            "affected_services": []
        }
    )

    assert not evaluator.evaluate(
        wrong_affected,
        truth,
    ).affected_services.correct

    bad_citation = correct.evidence[0].model_copy(
        update={
            "evidence_id": "ev_invalid"
        }
    )

    invalid_result = correct.model_copy(
        update={
            "evidence": [
                bad_citation,
                *correct.evidence[1:],
            ]
        }
    )

    assert not evaluator.evaluate(
        invalid_result,
        truth,
    ).evidence_citations.correct

    # ---------------------------------------------------------
    # Correct abstention
    # ---------------------------------------------------------

    sparse = make_context(
        insufficient=True
    )

    sparse_result = _result(sparse)
    sparse_truth = GroundTruthBuilder().build(
        sparse
    )

    assert not sparse_truth.root_cause_determinable

    abstention = evaluator.evaluate(
        sparse_result,
        sparse_truth,
    )

    assert abstention.abstention.correct
    assert abstention.overall == "correct_investigation"

    # ---------------------------------------------------------
    # Incorrect abstention
    # ---------------------------------------------------------

    record = sparse_result.evidence_records[0]

    guessed = sparse_result.model_copy(
        update={
            "outcome": "root_cause_identified",
            "root_cause": (
                "unavailable failure on payment-service"
            ),
            "root_cause_evidence_ids": [
                record.evidence_id
            ],
            "evidence": [
                EvidenceCitation(
                    evidence_id=record.evidence_id,
                    claim="unsupported guess",
                    source=record.source,
                    service=record.service,
                    description=record.description,
                    trace_id=record.trace_id,
                )
            ],
            "recommended_action_evidence_ids": [
                record.evidence_id
            ],
        }
    )

    incorrect_abstention = evaluator.evaluate(
        guessed,
        sparse_truth,
    )

    assert not incorrect_abstention.abstention.correct
    assert (
        incorrect_abstention.overall
        == "unsupported_or_incorrect"
    )


def test_verification_classifies_improvement_no_change_regression_and_sparse_data() -> None:
    verifier = ExperimentVerifier()

    baseline = _result(
        make_context()
    )

    unchanged = _result(
        make_context()
    )

    assert (
        verifier.compare(
            baseline,
            unchanged,
        ).status
        == "no_significant_change"
    )

    healthy_observation = _result(
        make_context(
            contradictory=True
        )
    )

    assert (
        verifier.compare(
            baseline,
            healthy_observation,
        ).status
        == "improved"
    )

    assert (
        verifier.compare(
            healthy_observation,
            baseline,
        ).status
        == "regressed"
    )

    sparse_a = _result(
        make_context(
            insufficient=True
        )
    )

    sparse_b = _result(
        make_context(
            insufficient=True
        )
    )

    assert (
        verifier.compare(
            sparse_a,
            sparse_b,
        ).status
        == "insufficient_data"
    )

    same_experiment = baseline.model_copy(
        update={
            "investigation_id": uuid.uuid4()
        }
    )

    with pytest.raises(
        ValueError,
        match="different experiments",
    ):
        verifier.compare(
            baseline,
            same_experiment,
        )
