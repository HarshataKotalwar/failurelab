from __future__ import annotations

from datetime import datetime
from typing import Any

from app.schemas.investigation import (
    ExperimentVerification,
    InvestigationResult,
    MetricComparison,
)


class ExperimentVerifier:
    """Compare only observations contained in two saved investigation snapshots."""

    def compare(
        self, baseline: InvestigationResult, verification: InvestigationResult
    ) -> ExperimentVerification:
        if baseline.experiment_id == verification.experiment_id:
            raise ValueError("baseline and verification must be different experiments")
        base_start, base_end = _bounds(baseline)
        verify_start, verify_end = _bounds(verification)
        comparisons: list[MetricComparison] = []

        baseline_logs = _records(baseline, "log")
        verification_logs = _records(verification, "log")
        if _logs_complete(baseline) and _logs_complete(verification):
            base_failures = sum(
                _status(record.details.get("status_code")) >= 500
                and record.service == _target_service(baseline)
                for record in baseline_logs
            )
            verify_failures = sum(
                _status(record.details.get("status_code")) >= 500
                and record.service == _target_service(verification)
                for record in verification_logs
            )
            comparisons.append(
                MetricComparison(
                    metric="target_failed_request_count",
                    baseline_value=base_failures,
                    verification_value=verify_failures,
                    direction=_lower_is_better(base_failures, verify_failures),
                    unit="requests observed in each investigation window",
                )
            )
        else:
            comparisons.append(
                MetricComparison(
                    metric="target_failed_request_count",
                    baseline_value=None,
                    verification_value=None,
                    direction="not_comparable",
                    unit="requests",
                )
            )

        base_health = _health_value(baseline, base_start, base_end)
        verify_health = _health_value(verification, verify_start, verify_end)
        comparisons.append(
            MetricComparison(
                metric="target_service_health",
                baseline_value=base_health,
                verification_value=verify_health,
                direction=_health_direction(base_health, verify_health),
            )
        )

        for metric_name, unit in (
            ("http_5xx_rate", "requests/second"),
            ("p95_latency_seconds", "seconds"),
        ):
            base_value = _aligned_metric(baseline, metric_name, base_start, base_end)
            verify_value = _aligned_metric(verification, metric_name, verify_start, verify_end)
            direction = (
                "not_comparable"
                if base_value is None or verify_value is None
                else _lower_is_better(base_value, verify_value)
            )
            comparisons.append(
                MetricComparison(
                    metric=metric_name,
                    baseline_value=base_value,
                    verification_value=verify_value,
                    direction=direction,
                    unit=unit,
                )
            )

        directions = {item.direction for item in comparisons if item.direction != "not_comparable"}
        if not directions:
            outcome = "insufficient_data"
        elif "regressed" in directions:
            outcome = "regressed"
        elif "improved" in directions:
            outcome = "improved"
        else:
            outcome = "no_significant_change"
        limitations = []
        for result in (baseline, verification):
            metrics = result.source_status.get("metrics")
            if metrics and metrics.status != "available":
                limitations.append(f"{result.experiment_id}: metric source is {metrics.status}.")
            logs = result.source_status.get("logs")
            if logs and logs.status in {"partial", "unavailable"}:
                limitations.append(f"{result.experiment_id}: log source is {logs.status}.")
        if outcome == "insufficient_data":
            limitations.append("No comparable measurements overlap both experiment snapshots.")

        return ExperimentVerification(
            baseline_experiment_id=baseline.experiment_id,
            verification_experiment_id=verification.experiment_id,
            baseline_investigation_id=baseline.investigation_id,
            verification_investigation_id=verification.investigation_id,
            baseline_window_start=base_start,
            baseline_window_end=base_end,
            verification_window_start=verify_start,
            verification_window_end=verify_end,
            status=outcome,
            comparisons=comparisons,
            limitations=list(dict.fromkeys(limitations)),
        )


def _records(result: InvestigationResult, source: str):
    return [record for record in result.evidence_records if record.source == source]


def _bounds(result: InvestigationResult) -> tuple[datetime | None, datetime | None]:
    started = next(
        (
            record.observed_at
            for record in result.evidence_records
            if record.event == "failure_started"
        ),
        None,
    )
    created = next(
        (record.observed_at for record in result.evidence_records if record.event == "created"),
        None,
    )
    stopped = next(
        (
            record.observed_at
            for record in result.evidence_records
            if record.event == "failure_stopped"
        ),
        None,
    )
    start = started or created
    end = stopped or result.created_at
    return start, end


def _target_service(result: InvestigationResult) -> str | None:
    return next(
        (
            record.service
            for record in result.evidence_records
            if record.source == "experiment" and record.event == "created"
        ),
        None,
    )


def _logs_complete(result: InvestigationResult) -> bool:
    state = result.source_status.get("logs")
    return bool(state and state.status in {"available", "not_found"})


def _health_value(
    result: InvestigationResult, start: datetime | None, end: datetime | None
) -> str | None:
    target = _target_service(result)
    observations = [
        record
        for record in result.evidence_records
        if record.source == "service"
        and record.service == target
        and record.details.get("observation_is_current") is True
        and (start is None or start <= record.observed_at)
        and (end is None or record.observed_at <= end)
    ]
    if not observations:
        return None
    latest = max(observations, key=lambda item: item.observed_at)
    return str(latest.details.get("status", "unknown"))


def _aligned_metric(
    result: InvestigationResult,
    name: str,
    start: datetime | None,
    end: datetime | None,
) -> float | None:
    if start is None or end is None:
        return None
    for record in _records(result, "metric"):
        if record.details.get("metric_name") != name:
            continue
        try:
            window_start = datetime.fromisoformat(str(record.details["window_start"]))
            window_end = datetime.fromisoformat(str(record.details["window_end"]))
            value = float(record.details["value"])
        except (KeyError, TypeError, ValueError):
            continue
        if start <= window_start <= window_end <= end:
            return value
    return None


def _lower_is_better(baseline: float | int, verification: float | int) -> str:
    if verification < baseline:
        return "improved"
    if verification > baseline:
        return "regressed"
    return "unchanged"


def _health_direction(baseline: str | None, verification: str | None) -> str:
    if baseline is None or verification is None:
        return "not_comparable"
    health_rank = {"healthy": 0, "degraded": 1, "unavailable": 2, "unreachable": 3}
    before = health_rank.get(baseline, 4)
    after = health_rank.get(verification, 4)
    if after < before:
        return "improved"
    if after > before:
        return "regressed"
    return "unchanged"


def _status(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0
