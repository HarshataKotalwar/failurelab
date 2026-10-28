from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import NAMESPACE_URL, uuid5

from app.schemas.investigation import (
    EvidenceRecord,
    EvidenceState,
    ExperimentContext,
    InvestigationContext,
    evidence_identifier,
)
from benchmarks.comparison import ComparisonScenario


def make_scenarios() -> list[ComparisonScenario]:
    """Small, fixed evidence fixtures for controlled provider comparisons."""
    return [
        ComparisonScenario("HTTP 500", _incident("http_500")),
        ComparisonScenario("Latency", _incident("latency")),
        ComparisonScenario("Timeout", _incident("timeout")),
        ComparisonScenario("Insufficient evidence", _incident("http_500", insufficient=True)),
    ]


def _record(
    *,
    source: str,
    service: str,
    observed_at: datetime,
    event: str,
    description: str,
    trace_id: str | None,
    details: dict,
) -> EvidenceRecord:
    return EvidenceRecord(
        evidence_id=evidence_identifier(
            source=source,
            service=service,
            observed_at=observed_at,
            event=event,
            description=description,
            trace_id=trace_id,
            details=details,
        ),
        source=source,
        service=service,
        observed_at=observed_at,
        event=event,
        description=description,
        trace_id=trace_id,
        details=details,
    )


def _incident(failure_type: str, *, insufficient: bool = False) -> InvestigationContext:
    now = datetime(2026, 1, 15, 12, 0, tzinfo=UTC)
    started = None if insufficient else now - timedelta(seconds=30)
    target = "inventory-service" if failure_type == "latency" else "payment-service"
    route = "/reserve" if failure_type == "latency" else "/pay"
    status_code = {
        "http_500": 500,
        "latency": 200,
        "timeout": 504,
    }[failure_type]
    duration = {"http_500": 12.0, "latency": 1510.0, "timeout": 4100.0}[failure_type]
    experiment_id = uuid5(NAMESPACE_URL, f"failurelab-phase9:{failure_type}:{insufficient}")
    trace_id = f"{failure_type.replace('_', '')[:8]:0<8}" + "a" * 24
    records = [
        _record(
            source="experiment",
            service=target,
            observed_at=started or now,
            event="failure_started",
            description="Persisted experiment start.",
            trace_id=None,
            details={"status": "RUNNING"},
        )
    ]
    if not insufficient:
        records.extend(
            [
                _record(
                    source="log",
                    service=target,
                    observed_at=now - timedelta(seconds=3),
                    event=(
                        "latency_observed"
                        if failure_type == "latency"
                        else "timeout_observed"
                        if failure_type == "timeout"
                        else "http_failure_observed"
                    ),
                    description=f"{route} returned HTTP {status_code}.",
                    trace_id=trace_id,
                    details={"route": route, "status_code": status_code, "duration_ms": duration},
                ),
                _record(
                    source="trace",
                    service=target,
                    observed_at=now - timedelta(seconds=3),
                    event=(
                        "latency_observed"
                        if failure_type == "latency"
                        else "timeout_observed"
                        if failure_type == "timeout"
                        else "http_failure_observed"
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
        )

    unavailable = EvidenceState(
        status="not_found" if insufficient else "available",
        available=not insufficient,
    )
    return InvestigationContext(
        experiment=ExperimentContext(
            experiment_id=experiment_id,
            mode="failure",
            target_service=target,
            failure_type=failure_type,
            configured_duration_seconds=30,
            status="CREATED" if insufficient else "RUNNING",
            created_at=now - timedelta(minutes=1),
            started_at=started,
            ended_at=None,
        ),
        service_states=[],
        timeline=[],
        metrics={},
        metrics_observed_at=now,
        metric_window_start=started,
        metric_window_end=now,
        metrics_state=unavailable,
        logs=[],
        logs_state=unavailable,
        traces=[],
        traces_state=unavailable,
        dependencies={"order-service": ["inventory-service", "payment-service"]},
        evidence_records=records,
    )
