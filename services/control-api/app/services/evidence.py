from __future__ import annotations

import asyncio
import os
from datetime import UTC, datetime, timedelta

import httpx
from failurelab_observability import trace_context_headers

from app.models.entities import Experiment
from app.schemas.api import (
    EvidenceLog,
    EvidenceLogs,
    EvidenceMetrics,
    EvidenceSpan,
    EvidenceTimelineEvent,
    ExperimentEvidence,
    ServiceObservation,
    ServiceResponse,
    ServiceWindowMetric,
    TraceEvidence,
)
from app.services.log_provider import LogProvider
from app.services.registry import configured_services
from app.services.trace_client import TraceClient


class PrometheusEvidenceCollector:
    def __init__(
        self,
        prometheus_url: str | None = None,
        *,
        log_provider: LogProvider | None = None,
        trace_client: TraceClient | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._prometheus_url = (
            prometheus_url or os.getenv("PROMETHEUS_URL", "http://localhost:9090")
        ).rstrip("/")
        self._logs = log_provider or LogProvider()
        self._traces = trace_client or TraceClient()
        self._transport = transport

    async def collect(self, experiment: Experiment) -> ExperimentEvidence:
        from_time = experiment.started_at or experiment.created_at
        to_time = experiment.ended_at or datetime.now(UTC)
        metrics, observations, logs, window_metrics = await asyncio.gather(
            self.collect_metrics(experiment.target_service),
            self.collect_service_observations(),
            self._logs.collect(),
            self.collect_window_metrics(from_time, to_time),
        )
        trace_ids = list(
            dict.fromkeys(
                record.trace_id
                for record in logs.records
                if record.trace_id and from_time <= record.timestamp <= to_time
            )
        )[-50:]
        lookups = await asyncio.gather(
            *(self._traces.get_trace(trace_id) for trace_id in trace_ids)
        )
        found = [lookup for lookup in lookups if lookup.status == "found"]
        relevant_spans = [
            span
            for lookup in found
            for span in lookup.spans
            if from_time <= span.start_time <= to_time
        ]
        failed = [lookup for lookup in lookups if lookup.status == "unavailable"]
        traces_available = bool(relevant_spans)
        failed_sources = list(logs.failed_providers) + [
            trace_id
            for trace_id, lookup in zip(trace_ids, lookups, strict=False)
            if lookup.status == "unavailable"
        ]
        trace_status = (
            "partial"
            if relevant_spans and failed or logs.status == "partial"
            else "available"
            if relevant_spans
            else "unavailable"
            if failed or (not trace_ids and logs.status == "unavailable")
            else "not_found"
        )
        trace_message = (
            "Tempo returned traces"
            if traces_available
            else "Tempo could not be queried"
            if trace_status == "unavailable"
            else "No trace IDs were found in incident-window logs"
            if not trace_ids
            else "Tempo has no trace for the incident-window trace IDs"
        )
        if logs.status == "partial" and not traces_available:
            trace_message = "Trace discovery is partial because some log providers failed."
        incident_logs = [
            record for record in logs.records if from_time <= record.timestamp <= to_time
        ]
        logs_status = (
            logs.status
            if logs.status in {"partial", "unavailable"}
            else "available"
            if incident_logs
            else "not_found"
        )
        logs_message = (
            "Some log providers failed."
            if logs_status == "partial"
            else "No log providers responded."
            if logs_status == "unavailable"
            else "No structured logs were found in this experiment window."
            if logs_status == "not_found"
            else None
        )
        timeline = [
            EvidenceTimelineEvent(event="created", status="CREATED", at=experiment.created_at)
        ]
        if experiment.started_at is not None:
            timeline.append(
                EvidenceTimelineEvent(event="started", status="RUNNING", at=experiment.started_at)
            )
        if experiment.ended_at is not None:
            timeline.append(
                EvidenceTimelineEvent(event="stopped", status="STOPPED", at=experiment.ended_at)
            )
        return ExperimentEvidence(
            experiment_id=experiment.id,
            status=experiment.status,
            target_service=experiment.target_service,
            failure_type=experiment.failure_type,
            timeline=timeline,
            service_observations=observations,
            metrics=metrics,
            window_metrics=window_metrics,
            observation_window_start=from_time,
            observation_window_end=to_time,
            traces=TraceEvidence(
                status=trace_status,
                available=traces_available,
                trace_ids=list(dict.fromkeys(span.trace_id for span in relevant_spans)),
                message=trace_message,
                failed_sources=failed_sources,
                spans=[
                    EvidenceSpan(
                        trace_id=span.trace_id,
                        span_id=span.span_id,
                        parent_span_id=span.parent_span_id,
                        service_name=span.service_name,
                        operation_name=span.operation_name,
                        start_time=span.start_time,
                        end_time=span.end_time,
                        duration_ms=span.duration_ms,
                        status=span.status,
                        attributes=span.attributes,
                    )
                    for span in relevant_spans
                ],
            ),
            logs=EvidenceLogs(
                status=logs_status,
                logs=[
                    EvidenceLog(
                        timestamp=record.timestamp,
                        service=record.service,
                        level=record.level,
                        message=record.message,
                        route=record.route,
                        status_code=record.status_code,
                        duration_ms=record.duration_ms,
                        trace_id=record.trace_id,
                    )
                    for record in incident_logs
                ],
                message=logs_message,
                failed_sources=logs.failed_providers,
            ),
        )

    async def collect_metrics(self, target_service: str) -> EvidenceMetrics:
        service = f"failurelab-{target_service}"
        expressions = {
            "request_rate": f'sum(rate(failurelab_http_requests_total{{service="{service}"}}[5m]))',
            "http_5xx_rate": (
                "sum(rate(failurelab_http_errors_total"
                f'{{service="{service}",status_code=~"5.."}}[5m]))'
            ),
            "p95_latency_seconds": (
                "histogram_quantile(0.95, sum by (le) "
                "(rate(failurelab_http_request_duration_seconds_bucket"
                f'{{service="{service}"}}[5m])))'
            ),
        }
        window_seconds = 300
        try:
            values = await asyncio.gather(
                *(self._query_value(expression) for expression in expressions.values())
            )
        except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
            observed_at = datetime.now(UTC)
            return EvidenceMetrics(
                available=False,
                message=f"Prometheus query failed: {exc}",
                observed_at=observed_at,
                window_start=observed_at - timedelta(seconds=window_seconds),
                window_end=observed_at,
                window_seconds=window_seconds,
            )
        observed_at = datetime.now(UTC)
        return EvidenceMetrics(
            available=True,
            request_rate=values[0],
            http_5xx_rate=values[1],
            p95_latency_seconds=values[2],
            observed_at=observed_at,
            window_start=observed_at - timedelta(seconds=window_seconds),
            window_end=observed_at,
            window_seconds=window_seconds,
        )

    async def _query_value(self, expression: str, at: datetime | None = None) -> float | None:
        params = {"query": expression}
        if at is not None:
            params["time"] = at.isoformat()
        async with httpx.AsyncClient(timeout=2, transport=self._transport) as client:
            response = await client.get(
                f"{self._prometheus_url}/api/v1/query",
                params=params,
                headers=trace_context_headers(),
            )
        response.raise_for_status()
        result = response.json()["data"]["result"]
        if not result:
            return None
        return float(result[0]["value"][1])

    async def collect_window_metrics(
        self, window_start: datetime, window_end: datetime
    ) -> list[ServiceWindowMetric]:
        seconds = max(1, int((window_end - window_start).total_seconds()))
        note = None
        if seconds < 15:
            note = (
                "The incident window is shorter than several Prometheus scrape intervals (5s). "
                "Counts can be missing or incomplete."
            )
        services = ("order-service", "inventory-service", "payment-service")
        return list(
            await asyncio.gather(
                *(
                    self._window_metric(service, seconds, window_end, note)
                    for service in services
                )
            )
        )

    async def _window_metric(
        self, service: str, seconds: int, at: datetime, note: str | None
    ) -> ServiceWindowMetric:
        metric_service = f"failurelab-{service}"
        window = f"{seconds}s"
        expressions = {
            "request_count": (
                f'sum(increase(failurelab_http_requests_total{{service="{metric_service}"}}[{window}]))'
            ),
            "error_count": (
                "sum(increase(failurelab_http_errors_total"
                f'{{service="{metric_service}",status_code=~"5.."}}[{window}]))'
            ),
            "p95_latency_seconds": (
                "histogram_quantile(0.95, sum by (le) (rate("
                f'failurelab_http_request_duration_seconds_bucket{{service="{metric_service}"}}[{window}])))'
            ),
        }
        try:
            request_count, error_count, latency = await asyncio.gather(
                *(
                    self._query_value(expression, at=at)
                    for expression in expressions.values()
                )
            )
        except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
            return ServiceWindowMetric(
                service=service,
                available=False,
                message=f"Prometheus query failed: {exc}",
            )
        error_rate = (
            error_count / request_count
            if request_count not in (None, 0) and error_count is not None
            else None
        )
        if request_count is None and error_count is None and latency is None:
            return ServiceWindowMetric(
                service=service,
                available=False,
                message=note
                or "Prometheus returned no samples for this service in the incident window.",
            )
        return ServiceWindowMetric(
            service=service,
            available=True,
            request_count=request_count,
            error_count=error_count,
            error_rate=error_rate,
            p95_latency_seconds=latency,
            message=note,
        )

    async def collect_service_observations(self) -> list[ServiceObservation]:
        async def observe(service: ServiceResponse) -> ServiceObservation:
            name = service.name
            url = service.url
            try:
                async with httpx.AsyncClient(timeout=2, transport=self._transport) as client:
                    response = await client.get(
                        f"{url.rstrip('/')}/health", headers=trace_context_headers()
                    )
                return ServiceObservation(
                    service=name,
                    url=url,
                    status="healthy" if response.is_success else "unavailable",
                    status_code=response.status_code,
                    observed_at=datetime.now(UTC),
                )
            except httpx.HTTPError:
                return ServiceObservation(
                    service=name, url=url, status="unreachable", observed_at=datetime.now(UTC)
                )

        return await asyncio.gather(*(observe(service) for service in configured_services()))
