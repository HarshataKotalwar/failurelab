from __future__ import annotations

from typing import Protocol

from app.schemas.investigation import (
    AffectedService,
    EvidenceCitation,
    EvidenceRecord,
    InvestigationContext,
    InvestigationResult,
)
from app.services.gemini_investigator import (
    GeminiReasoningProvider,
    TransientGeminiError,
    assemble_gemini_result,
)
from app.services.investigation_prompt import INVESTIGATION_PROMPT


class LLMProvider(Protocol):
    name: str

    async def generate(
        self,
        prompt: str,
        context: InvestigationContext,
    ) -> dict:
        ...


class LocalInvestigator:
    """Offline rule-based provider; confidence is a deterministic, uncalibrated heuristic."""

    name = "mock"

    async def generate(
        self,
        prompt: str,
        context: InvestigationContext,
    ) -> dict:
        del prompt  # The local provider reasons directly over the typed evidence context.

        experiment = context.experiment
        catalog = {
            record.evidence_id: record
            for record in context.evidence_records
        }
        citations: dict[str, EvidenceCitation] = {}

        def cite(record: EvidenceRecord, claim: str) -> str:
            citations[record.evidence_id] = EvidenceCitation(
                evidence_id=record.evidence_id,
                claim=claim,
                source=record.source,
                service=record.service,
                description=record.description,
                trace_id=record.trace_id,
            )
            return record.evidence_id

        started = experiment.started_at is not None
        running = experiment.status == "RUNNING"
        incident_end = (
            experiment.ended_at
            or context.metrics_observed_at
        )

        incident_records = [
            record
            for record in context.evidence_records
            if record.source in {"service", "log", "trace"}
            and (
                not started
                or record.observed_at >= experiment.started_at
            )
            and (
                incident_end is None
                or record.observed_at <= incident_end
            )
        ]

        # Service-health observations are current snapshots collected
        # during investigation. They should not be discarded merely
        # because their timestamp falls outside the retained
        # log/trace incident window.
        target_services = [
            record
            for record in context.evidence_records
            if (
                record.source == "service"
                and record.service == experiment.target_service
                and record.details.get("status")
                in {"unavailable", "unreachable"}
                and record.details.get("observation_is_current")
                is True
            )
        ]

        target_failed_logs = [
            record
            for record in incident_records
            if (
                record.source == "log"
                and record.service == experiment.target_service
                and _status_code(
                    record.details.get("status_code")
                ) >= 500
            )
        ]

        target_failed_spans = [
            record
            for record in incident_records
            if (
                record.source == "trace"
                and record.service == experiment.target_service
                and _span_is_error(record)
            )
        ]

        failure_type = experiment.failure_type
        if failure_type == "latency":
            support_logs = [
                record
                for record in incident_records
                if (
                    record.source == "log"
                    and record.service == experiment.target_service
                    and _is_business_route(record, experiment.target_service)
                    and (_duration_ms(record) or 0) >= _LATENCY_SUPPORT_MS
                    and _status_code(record.details.get("status_code")) < 500
                )
            ]
            support_spans = [
                record
                for record in incident_records
                if (
                    record.source == "trace"
                    and record.service == experiment.target_service
                    and (_duration_ms(record) or 0) >= _LATENCY_SUPPORT_MS
                    and not _span_is_error(record)
                )
            ]
            contradictions = [
                record
                for record in incident_records
                if record.service == experiment.target_service
                and (
                    (
                        record.source == "log"
                        and _is_business_route(record, experiment.target_service)
                        and 0 < _status_code(record.details.get("status_code")) < 500
                        and _duration_ms(record) is not None
                        and _duration_ms(record) < _LATENCY_FAST_MS
                    )
                    or (
                        record.source == "log"
                        and _is_business_route(record, experiment.target_service)
                        and _status_code(record.details.get("status_code")) >= 500
                    )
                    or (
                        record.source == "trace"
                        and _span_is_successful_business(record, experiment.target_service)
                        and _duration_ms(record) is not None
                        and _duration_ms(record) < _LATENCY_FAST_MS
                    )
                )
            ]
            contradictions.extend(target_services)
            service_support: list[EvidenceRecord] = []
        elif failure_type == "timeout":
            # HTTP 504 is the actual signal produced by the timeout
            # injection. A generic 500/502 is an HTTP failure, not proof
            # of timeout behavior.
            support_logs = [
                record
                for record in incident_records
                if record.source == "log"
                and record.service == experiment.target_service
                and (
                    _status_code(record.details.get("status_code")) == 504
                    or record.event == "timeout_observed"
                )
            ]
            support_spans = [
                record
                for record in incident_records
                if record.source == "trace"
                and record.service == experiment.target_service
                and _span_is_error(record)
                and (
                    _status_code(record.details.get("http.status_code")) == 504
                    or _status_code(record.details.get("http.response.status_code")) == 504
                    or record.event == "timeout_observed"
                    or _record_has_timeout_attribute(record)
                )
            ]
            contradictions = [
                record
                for record in incident_records
                if record.service == experiment.target_service
                and (
                    (
                        record.source == "log"
                        and _is_business_route(record, experiment.target_service)
                        and 0 < _status_code(record.details.get("status_code")) < 500
                    )
                    or (
                        record.source == "trace"
                        and _span_is_successful_business(record, experiment.target_service)
                    )
                )
            ]
            contradictions.extend(target_services)
            service_support = []
        else:
            support_logs = target_failed_logs
            support_spans = target_failed_spans
            contradictions = [
                record
                for record in incident_records
                if record.service == experiment.target_service
                and (
                    (
                        record.source == "service"
                        and record.details.get("status")
                        == "healthy"
                    )
                    or (
                        record.source == "log"
                        and record.details.get("route")
                        in {"/health", "/pay", "/reserve", "/orders"}
                        and 0
                        < _status_code(
                            record.details.get("status_code")
                        )
                        < 400
                    )
                    or (
                        record.source == "trace"
                        and _span_is_successful_business(record, experiment.target_service)
                    )
                )
            ]
            service_support = target_services

        incident_metric = _metric_supports_incident(context)

        signal_sets = {
            "service": service_support,
            "log": support_logs,
            "trace": support_spans,
            "metric": incident_metric,
        }

        supporting_sources = {
            source
            for source, records in signal_sets.items()
            if records
        }

        direct_sources = (
            supporting_sources
            & {"service", "log", "trace"}
        )

        missing = _missing_evidence(
            context,
            started,
            supporting_sources,
            contradictions,
        )

        enough_evidence = (
            started
            and len(supporting_sources) >= 2
            and bool(direct_sources)
        )

        identified = (
            enough_evidence
            and not contradictions
        )

        root_cause_ids: list[str] = []

        if identified:
            root_cause_ids.extend(
                cite(
                    record,
                    "Confirms the configured experiment's lifecycle window.",
                )
                for record in context.evidence_records
                if (
                    record.source == "experiment"
                    and record.event
                    in {"created", "failure_started"}
                )
            )

            for record in [
                *service_support,
                *support_logs,
                *support_spans,
                *incident_metric,
            ]:
                root_cause_ids.append(
                    cite(
                        record,
                        "Supports the configured failure hypothesis.",
                    )
                )

            if failure_type == "latency":
                root_cause = (
                    f"The target service {experiment.target_service} exhibited elevated "
                    "request duration during the incident window."
                )
            elif failure_type == "timeout":
                root_cause = (
                    f"The target service {experiment.target_service} experienced timeout "
                    "behavior during the incident window."
                )
            else:
                root_cause = (
                    f"Incident-window evidence supports the configured "
                    f"{failure_type} failure on {experiment.target_service} as the probable cause."
                )

            outcome = "root_cause_identified"

        else:
            root_cause = None
            outcome = "insufficient_evidence"

        affected: list[AffectedService] = []

        if identified:
            direct_ids = list(
                dict.fromkeys(root_cause_ids)
            )

            affected.append(
                AffectedService(
                    service=experiment.target_service,
                    impact="direct",
                    evidence_ids=direct_ids,
                )
            )

            for service in sorted(context.dependencies):
                if not _depends_on(
                    service,
                    experiment.target_service,
                    context.dependencies,
                ):
                    continue

                dependent_logs = [
                    record
                    for record in incident_records
                    if record.source == "log" and record.service == service and (
                        _status_code(record.details.get("status_code")) >= 500
                        or (
                            failure_type == "latency"
                            and (_duration_ms(record) or 0) >= _LATENCY_SUPPORT_MS
                        )
                    )
                ]

                for service_log in dependent_logs:
                    trace_id = service_log.trace_id

                    target_correlated = [
                        record
                        for record in [
                            *support_logs,
                            *support_spans,
                        ]
                        if (
                            trace_id
                            and record.trace_id == trace_id
                        )
                    ]

                    if not target_correlated:
                        continue

                    ids = [
                        cite(
                            service_log,
                            f"Observed an error in dependent service {service}.",
                        ),
                        *[
                            cite(
                                record,
                                f"Correlated target failure affecting {service}.",
                            )
                            for record in target_correlated
                        ],
                    ]

                    ids.extend(
                        cite(
                            record,
                            f"Configured dependency path from {service} to target.",
                        )
                        for record in context.evidence_records
                        if (
                            record.source == "dependency"
                            and record.service == service
                            and record.details.get(
                                "downstream_service"
                            )
                            == experiment.target_service
                        )
                    )

                    if any(
                        item.service == service
                        for item in affected
                    ):
                        continue

                    affected.append(
                        AffectedService(
                            service=service,
                            impact="indirect",
                            evidence_ids=list(
                                dict.fromkeys(ids)
                            ),
                        )
                    )

        if identified:
            if running:
                recommended = (
                    "Stop or allow the controlled experiment to expire, "
                    "then verify the target service recovers."
                )

                action_records = [
                    *service_support,
                    *support_logs,
                ]

            else:
                recommended = (
                    "Verify current target health separately from the "
                    "retained incident-window logs and correlated traces."
                )

                action_records = [
                    record
                    for record in context.evidence_records
                    if (
                        record.source == "experiment"
                        and record.event == "failure_stopped"
                    )
                ] + [
                    *support_logs,
                    *support_spans,
                ]

            if not action_records:
                action_records = [
                    catalog[item]
                    for item in root_cause_ids
                ]

        else:
            recommended = (
                "Collect an incident-window target health observation "
                "or failed request, and restore unavailable telemetry "
                "providers; then investigate again."
            )

            action_records = [
                record
                for record in incident_records
                if record.service
            ]

        action_ids = list(
            dict.fromkeys(
                cite(
                    record,
                    "Supports the recommended next investigation/action.",
                )
                for record in action_records
            )
        )

        confidence = _confidence(
            identified=identified,
            experiment_started=started,
            supporting_sources=supporting_sources,
            contradictions=contradictions,
            evidence_states=(
                context.logs_state,
                context.traces_state,
                context.metrics_state,
            ),
        )

        degraded = any(
            state.status != "available"
            for state in (
                context.logs_state,
                context.traces_state,
                context.metrics_state,
            )
        )

        limitations = [
            state.message
            for state in (
                context.logs_state,
                context.traces_state,
                context.metrics_state,
            )
            if state.message
        ]

        return {
            "experiment_id": experiment.experiment_id,
            "provider": self.name,
            "outcome": outcome,
            "root_cause": root_cause,
            "root_cause_evidence_ids": root_cause_ids,
            "evidence": list(citations.values()),
            "evidence_records": context.evidence_records,
            "affected_services": affected,
            "timeline": context.timeline,
            "source_status": {
                "logs": context.logs_state,
                "traces": context.traces_state,
                "metrics": context.metrics_state,
            },
            "confidence": confidence,
            "confidence_method": "deterministic_heuristic_v1",
            "recommended_action": recommended,
            "recommended_action_evidence_ids": action_ids,
            "missing_evidence": missing,
            "degraded": degraded,
            "limitations": limitations,
        }


MockLLMProvider = LocalInvestigator


def create_investigator_provider(
    provider: str,
    *,
    gemini_api_key: str = "",
    gemini_model: str = "gemini-3.8-flash",
) -> LocalInvestigator | GeminiReasoningProvider:
    if provider == "local":
        return LocalInvestigator()
    if provider == "gemini":
        return GeminiReasoningProvider(gemini_api_key, gemini_model)
    raise ValueError("INVESTIGATOR_PROVIDER must be 'local' or 'gemini'")


class Investigator:
    def __init__(
        self,
        provider: LLMProvider | GeminiReasoningProvider,
        *,
        gemini_fallback_to_local: bool = False,
    ) -> None:
        self._provider = provider
        self._gemini_fallback_to_local = gemini_fallback_to_local

    async def investigate(
        self,
        context: InvestigationContext,
    ) -> InvestigationResult:
        prompt = INVESTIGATION_PROMPT.format(
            context=context.model_dump_json(indent=2)
        )

        if isinstance(self._provider, GeminiReasoningProvider):
            # The established deterministic logic is the safety envelope for
            # admissible claims and confidence; Gemini contributes the
            # evidence-referenced reasoning text within that envelope.
            baseline_payload = await LocalInvestigator().generate(prompt, context)
            baseline = InvestigationResult.model_validate(baseline_payload)
            try:
                assessment = await self._provider.assess(context)
                result = assemble_gemini_result(assessment, baseline, context)
            except TransientGeminiError as exc:
                if not self._gemini_fallback_to_local:
                    raise
                fallback_payload = baseline.model_dump()
                fallback_payload["provider"] = "local_fallback"
                fallback_payload["degraded"] = True
                fallback_payload["limitations"] = [
                    *fallback_payload["limitations"],
                    f"Gemini unavailable: {exc}",
                ]
                result = InvestigationResult.model_validate(fallback_payload)
        else:
            response = await self._provider.generate(prompt, context)
            result = InvestigationResult.model_validate(response)

        collected = {
            record.evidence_id: record
            for record in context.evidence_records
        }

        returned = {
            record.evidence_id: record
            for record in result.evidence_records
        }

        if collected != returned:
            raise ValueError(
                "Investigation output evidence does not match collected evidence"
            )

        return result


def _status_code(value: object) -> int:
    try:
        return int(value or 0)
    except (ValueError, TypeError):
        return 0


def _record_has_timeout_attribute(record: EvidenceRecord) -> bool:
    return any(
        "timeout" in str(record.details.get(key, "")).lower()
        or "cancelled" in str(record.details.get(key, "")).lower()
        or "canceled" in str(record.details.get(key, "")).lower()
        for key in ("exception.type", "error.type", "exception.message", "error.message")
    )


def _span_is_error(record: EvidenceRecord) -> bool:
    status = str(
        record.details.get("status", "")
    ).upper()

    return (
        status
        in {"2", "ERROR", "STATUS_CODE_ERROR"}
        or _status_code(
            record.details.get("http.status_code")
        ) >= 500
    )


_BUSINESS_ROUTES = {
    "payment-service": "/pay",
    "inventory-service": "/reserve",
    "order-service": "/orders",
}
# The injector's configured delay is 1.5 seconds. This 1.4-second evidence
# floor allows modest scheduling and telemetry measurement variance.
_LATENCY_SUPPORT_MS = 1400.0
# Sub-800ms successful business requests are treated as contradictory
# observations of the incident hypothesis.
_LATENCY_FAST_MS = 800.0


def _duration_ms(record: EvidenceRecord) -> float | None:
    value = record.details.get("duration_ms")
    if isinstance(value, (int, float)):
        return float(value)
    return None


def _is_business_route(record: EvidenceRecord, service: str) -> bool:
    route = _BUSINESS_ROUTES.get(service)
    observed = str(record.details.get("route") or record.details.get("http.route") or "")
    return bool(route) and route in observed


def _span_is_successful_business(record: EvidenceRecord, service: str) -> bool:
    # FastAPI instrumentation also emits tiny child spans for ASGI send/receive
    # events. Their copied HTTP route/status attributes do not represent a
    # complete request and must not contradict its server-span duration.
    if record.details.get("asgi.event.type") is not None:
        return False
    route = str(record.details.get("http.route") or record.description).lower()
    expected = _BUSINESS_ROUTES.get(service, "").lstrip("/")
    if expected and expected not in route:
        return False
    code = _status_code(record.details.get("http.status_code"))
    return 0 < code < 400 and not _span_is_error(record)


def _metric_supports_incident(
    context: InvestigationContext,
) -> list[EvidenceRecord]:
    start = context.experiment.started_at
    end = (
        context.experiment.ended_at
        or context.metrics_observed_at
    )

    window_start = context.metric_window_start
    window_end = context.metric_window_end

    if not all(
        (
            start,
            end,
            window_start,
            window_end,
        )
    ):
        return []

    if window_start < start or window_end > end:
        return []

    if context.experiment.failure_type == "latency":
        metric_name = "p95_latency_seconds"
        supported = lambda value: value >= 1.0
    else:
        metric_name = "http_5xx_rate"
        supported = lambda value: value > 0
    return [
        record
        for record in context.evidence_records
        if (
            record.source == "metric"
            and record.service
            == context.experiment.target_service
            and record.details.get("metric_name")
            == metric_name
            and isinstance(
                record.details.get("value"),
                (int, float),
            )
            and supported(float(record.details["value"]))
        )
    ]


def _depends_on(
    service: str,
    target: str,
    dependencies: dict[str, list[str]],
) -> bool:
    pending = list(
        dependencies.get(service, [])
    )
    visited: set[str] = set()

    while pending:
        downstream = pending.pop()

        if downstream == target:
            return True

        if downstream not in visited:
            visited.add(downstream)
            pending.extend(
                dependencies.get(
                    downstream,
                    [],
                )
            )

    return False


def _missing_evidence(
    context,
    started,
    supporting_sources,
    contradictions,
) -> list[str]:
    missing: list[str] = []

    if not started:
        missing.append(
            "The experiment has no persisted start time; "
            "incident causality is unproven."
        )

    for label, state in (
        ("logs", context.logs_state),
        ("Tempo traces", context.traces_state),
        ("Prometheus metrics", context.metrics_state),
    ):
        if state.status in {
            "unavailable",
            "partial",
            "not_found",
        }:
            missing.append(
                f"{label} source status is {state.status}."
            )

    if not supporting_sources:
        missing.append(
            "No target-service failure signal was observed "
            "in the experiment window."
        )

    if len(supporting_sources) < 2:
        missing.append(
            "At least two corroborating telemetry source "
            "types are required."
        )

    if (
        "metric" not in supporting_sources
        and context.metrics_state.status == "available"
    ):
        missing.append(
            "Metric samples did not fully overlap the "
            "experiment lifecycle window."
        )

    if contradictions:
        missing.append(
            "Target-service success and failure observations "
            "conflict in the incident window."
        )

    return missing


def _confidence(
    *,
    identified: bool,
    experiment_started: bool,
    supporting_sources: set[str],
    contradictions: list[EvidenceRecord],
    evidence_states: tuple,
) -> float:
    """Deterministic heuristic only; these values are not statistically calibrated."""

    if not identified:
        return (
            min(
                0.2,
                0.05 * len(supporting_sources),
            )
            if not contradictions
            else 0.05
        )

    score = (
        0.20
        + 0.16 * min(len(supporting_sources), 4)
    )

    if experiment_started:
        score += 0.08

    score -= 0.04 * sum(
        state.status
        in {
            "partial",
            "unavailable",
            "not_found",
        }
        for state in evidence_states
    )

    return round(
        max(0.0, min(0.92, score)),
        2,
    )
