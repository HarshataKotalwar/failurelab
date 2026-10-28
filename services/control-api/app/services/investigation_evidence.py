from __future__ import annotations

import asyncio
from datetime import UTC, datetime

from app.models.entities import Experiment
from app.schemas.investigation import (
    EvidenceRecord,
    EvidenceState,
    ExperimentContext,
    InvestigationContext,
    InvestigationEvent,
    NormalizedSpan,
    ServiceState,
    evidence_identifier,
)
from app.services.evidence import PrometheusEvidenceCollector
from app.services.log_provider import LogProvider
from app.services.trace_client import TraceClient, TraceLookupResult

DEPENDENCIES = {
    "order-service": ["inventory-service", "payment-service"],
    "inventory-service": [],
    "payment-service": [],
}


class InvestigationEvidenceService:
    def __init__(
        self,
        metrics: PrometheusEvidenceCollector,
        logs: LogProvider,
        traces: TraceClient,
    ) -> None:
        self._metrics = metrics
        self._logs = logs
        self._traces = traces

    async def collect(self, experiment: Experiment) -> InvestigationContext:
        metrics_evidence, service_observations, log_collection = await asyncio.gather(
            self._metrics.collect_metrics(experiment.target_service),
            self._metrics.collect_service_observations(),
            self._logs.collect(),
        )
        now = datetime.now(UTC)
        incident_start = experiment.started_at or experiment.created_at
        incident_end = experiment.ended_at or now
        relevant_logs = [
            log for log in log_collection.records if incident_start <= log.timestamp <= incident_end
        ]

        trace_ids = list(dict.fromkeys(log.trace_id for log in relevant_logs if log.trace_id))[-50:]
        trace_results: list[TraceLookupResult] = await asyncio.gather(
            *(self._traces.get_trace(trace_id) for trace_id in trace_ids)
        )
        spans = _bounded_spans(
            [
                span
                for result in trace_results
                if result.status == "found"
                for span in result.spans
                if incident_start <= span.start_time <= incident_end
            ]
        )
        records = self._build_evidence_records(
            experiment,
            service_observations,
            metrics_evidence,
            relevant_logs,
            spans,
        )

        service_states = [
            ServiceState(
                service=item.service,
                health_state=item.status,
                http_status=item.status_code,
                observed_at=item.observed_at,
                observation_is_current=True,
            )
            for item in service_observations
        ]
        log_state = _log_state(log_collection, relevant_logs)
        trace_state = _trace_state(trace_results, trace_ids, spans)
        metric_state = EvidenceState(
            status=(
                "unavailable"
                if not metrics_evidence.available
                else "available"
                if any(
                    value is not None
                    for value in (
                        metrics_evidence.request_rate,
                        metrics_evidence.http_5xx_rate,
                        metrics_evidence.p95_latency_seconds,
                    )
                )
                else "not_found"
            ),
            available=metrics_evidence.available,
            message=(
                metrics_evidence.message
                or (
                    "Prometheus responded but returned no matching metric series."
                    if metrics_evidence.available
                    and metrics_evidence.request_rate is None
                    and metrics_evidence.http_5xx_rate is None
                    and metrics_evidence.p95_latency_seconds is None
                    else None
                )
            ),
        )

        timeline: list[InvestigationEvent] = []
        for record in records:
            if record.source == "dependency":
                continue
            timeline.append(
                InvestigationEvent(
                    timestamp=record.observed_at,
                    source=record.source,
                    service=record.service,
                    event=record.event,
                    description=record.description,
                    evidence_id=record.evidence_id,
                )
            )
        timeline.sort(key=lambda event: event.timestamp)

        return InvestigationContext(
            experiment=ExperimentContext(
                experiment_id=experiment.id,
                mode=getattr(experiment, "mode", "failure") or "failure",
                target_service=experiment.target_service,
                failure_type=experiment.failure_type,
                configured_duration_seconds=experiment.duration_seconds,
                status=experiment.status,
                created_at=experiment.created_at,
                started_at=experiment.started_at,
                ended_at=experiment.ended_at,
            ),
            service_states=service_states,
            timeline=timeline,
            metrics={
                "request_rate": metrics_evidence.request_rate,
                "http_5xx_rate": metrics_evidence.http_5xx_rate,
                "p95_latency_seconds": metrics_evidence.p95_latency_seconds,
            },
            metrics_observed_at=metrics_evidence.observed_at,
            metric_window_start=metrics_evidence.window_start,
            metric_window_end=metrics_evidence.window_end,
            metrics_state=metric_state,
            logs=relevant_logs,
            logs_state=log_state,
            traces=spans,
            traces_state=trace_state,
            dependencies=DEPENDENCIES,
            evidence_records=records,
        )

    @staticmethod
    def _build_evidence_records(
        experiment: Experiment,
        observations: list,
        metrics,
        logs: list,
        spans: list[NormalizedSpan],
    ) -> list[EvidenceRecord]:
        records: list[EvidenceRecord] = []

        def add(
            *,
            source: str,
            service: str | None,
            observed_at: datetime,
            event: str,
            description: str,
            trace_id: str | None = None,
            details: dict | None = None,
        ) -> None:
            values = details or {}
            identifier = evidence_identifier(
                source=source,
                service=service,
                observed_at=observed_at,
                event=event,
                description=description,
                trace_id=trace_id,
                details=values,
            )
            records.append(
                EvidenceRecord(
                    evidence_id=identifier,
                    source=source,
                    service=service,
                    observed_at=observed_at,
                    event=event,
                    description=description,
                    trace_id=trace_id,
                    details=values,
                )
            )

        add(
            source="experiment",
            service=experiment.target_service,
            observed_at=experiment.created_at,
            event="created",
            description=(
                f"Experiment configured {experiment.failure_type} failure for "
                f"{experiment.target_service}; status at collection is {experiment.status}."
            ),
            details={
                "experiment_id": str(experiment.id),
                "failure_type": experiment.failure_type,
                "status": experiment.status,
                "duration_seconds": experiment.duration_seconds,
            },
        )
        if experiment.started_at:
            add(
                source="experiment",
                service=experiment.target_service,
                observed_at=experiment.started_at,
                event="failure_started",
                description="Persisted experiment lifecycle records the failure start.",
                details={"experiment_id": str(experiment.id), "status": "RUNNING"},
            )
        if experiment.ended_at:
            add(
                source="experiment",
                service=experiment.target_service,
                observed_at=experiment.ended_at,
                event="failure_stopped",
                description="Persisted experiment lifecycle records the failure stop.",
                details={"experiment_id": str(experiment.id), "status": "STOPPED"},
            )

        for observation in observations:
            add(
                source="service",
                service=observation.service,
                observed_at=observation.observed_at,
                event="health_observation",
                description=(
                    f"Current health observation: {observation.status}"
                    + (f" (HTTP {observation.status_code})" if observation.status_code else "")
                    + "; this observation is current, not historical incident state."
                ),
                details={
                    "status": observation.status,
                    "status_code": observation.status_code,
                    "url": observation.url,
                    "observation_is_current": True,
                },
            )

        if metrics.observed_at:
            for metric_name, value in (
                ("request_rate", metrics.request_rate),
                ("http_5xx_rate", metrics.http_5xx_rate),
                ("p95_latency_seconds", metrics.p95_latency_seconds),
            ):
                if value is None:
                    continue
                add(
                    source="metric",
                    service=experiment.target_service,
                    observed_at=metrics.observed_at,
                    event="metric_observation",
                    description=(
                        f"Prometheus {metric_name}={value}; query window "
                        f"{metrics.window_start.isoformat()} to {metrics.window_end.isoformat()} "
                        f"({metrics.window_seconds}s)."
                    ),
                    details={
                        "metric_name": metric_name,
                        "value": value,
                        "window_start": metrics.window_start.isoformat(),
                        "window_end": metrics.window_end.isoformat(),
                        "window_seconds": metrics.window_seconds,
                    },
                )

        for log in logs:
            status_code = log.status_code or 0
            duration_ms = log.duration_ms
            if status_code == 504:
                event = "timeout_observed"
                signal_description = "observed HTTP 504 timeout response"
            elif (
                log.service == experiment.target_service
                and duration_ms is not None
                and duration_ms >= 1400.0
                and status_code < 500
            ):
                # The controlled latency injector sleeps for 1.5 seconds.
                # 1.4 seconds allows small scheduling/measurement variance.
                event = "latency_observed"
                signal_description = f"observed elevated request duration ({duration_ms:.1f} ms)"
            elif status_code >= 500:
                event = "http_failure_observed"
                signal_description = f"observed HTTP {status_code} failure"
            else:
                event = "request_completed"
                signal_description = "request completed"
            add(
                source="log",
                service=log.service,
                observed_at=log.timestamp,
                event=event,
                description=(
                    f"{log.message}: {log.route or 'unknown route'}; {signal_description}"
                    + (f" returned HTTP {log.status_code}" if log.status_code else "")
                ),
                trace_id=log.trace_id or None,
                details={
                    "level": log.level,
                    "route": log.route,
                    "status_code": log.status_code,
                    "duration_ms": log.duration_ms,
                    "experiment_id": log.experiment_id,
                },
            )

        for span in spans:
            status_code = _span_status_code(span.attributes)
            if status_code == 504 or _span_timeout_error(span.attributes):
                event = "timeout_observed"
                signal_description = "observed timeout or aborted request in trace"
            elif (
                span.service_name.removeprefix("failurelab-") == experiment.target_service
                and span.duration_ms >= 1400.0
                and status_code < 500
            ):
                event = "latency_observed"
                signal_description = f"observed elevated span duration ({span.duration_ms:.1f} ms)"
            elif status_code >= 500:
                event = "http_failure_observed"
                signal_description = f"observed HTTP {status_code} failure in trace"
            else:
                event = "span_observed"
                signal_description = "observed request span"
            add(
                source="trace",
                service=span.service_name.removeprefix("failurelab-"),
                observed_at=span.start_time,
                event=event,
                description=(
                    f"{span.operation_name} completed with status {span.status} "
                    f"in {span.duration_ms:.3f}ms; {signal_description}."
                ),
                trace_id=span.trace_id,
                details={
                    "span_id": span.span_id,
                    "parent_span_id": span.parent_span_id,
                    "end_time": span.end_time.isoformat(),
                    "duration_ms": span.duration_ms,
                    "status": span.status,
                    **span.attributes,
                },
            )

        for service, downstream_services in DEPENDENCIES.items():
            for downstream in downstream_services:
                add(
                    source="dependency",
                    service=service,
                    observed_at=experiment.created_at,
                    event="configured_dependency",
                    description=f"Configured service relationship: {service} -> {downstream}.",
                    details={"downstream_service": downstream},
                )
        return records


def _span_status_code(attributes: dict) -> int:
    try:
        return int(attributes.get("http.status_code", attributes.get("http.response.status_code", 0)) or 0)
    except (TypeError, ValueError):
        return 0


def _span_timeout_error(attributes: dict) -> bool:
    values = " ".join(
        str(attributes.get(key, ""))
        for key in ("exception.type", "error.type", "exception.message", "error.message")
    ).lower()
    return "timeout" in values or "cancelled" in values or "canceled" in values


def _log_state(log_collection, incident_logs: list) -> EvidenceState:
    status = log_collection.status
    if status == "unavailable":
        return EvidenceState(
            status="unavailable",
            available=False,
            message="No structured log providers responded.",
            failed_sources=log_collection.failed_providers,
        )
    if status == "partial":
        return EvidenceState(
            status="partial",
            available=True,
            message="Some structured log providers failed.",
            failed_sources=log_collection.failed_providers,
        )
    if not incident_logs:
        return EvidenceState(
            status="not_found",
            available=True,
            message="Providers responded, but no logs fell inside the experiment window.",
        )
    return EvidenceState(status="available", available=True)


def _trace_state(
    lookups: list[TraceLookupResult], trace_ids: list[str], spans: list[NormalizedSpan]
) -> EvidenceState:
    failed = [
        trace_id
        for trace_id, lookup in zip(trace_ids, lookups, strict=False)
        if lookup.status == "unavailable"
    ]
    if spans and failed:
        return EvidenceState(
            status="partial",
            available=True,
            message="Some correlated trace lookups failed.",
            failed_sources=failed,
        )
    if spans:
        return EvidenceState(status="available", available=True)
    if failed:
        return EvidenceState(
            status="unavailable",
            available=False,
            message="Tempo could not be queried for some or all trace IDs.",
            failed_sources=failed,
        )
    return EvidenceState(
        status="not_found",
        available=False,
        message=(
            "No trace IDs were available from incident-window logs."
            if not trace_ids
            else "Tempo returned not-found for all incident-window trace IDs."
        ),
    )


def _bounded_spans(spans: list[NormalizedSpan], limit: int = 200) -> list[NormalizedSpan]:
    return sorted(spans, key=lambda span: span.start_time)[-limit:]
