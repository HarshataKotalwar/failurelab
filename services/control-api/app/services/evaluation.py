from __future__ import annotations

from app.schemas.investigation import (
    EvaluationDimension,
    EvaluationMetric,
    GroundTruth,
    InvestigationContext,
    InvestigationEvaluation,
    InvestigationResult,
)


class GroundTruthBuilder:
    """Create evaluation metadata after inference; this object is never passed to a provider."""

    def build(self, context: InvestigationContext) -> GroundTruth:
        experiment = context.experiment
        target = experiment.target_service
        start = experiment.started_at
        end = experiment.ended_at or context.metrics_observed_at
        records = context.evidence_records

        incident_records = [
            record
            for record in records
            if (start is None or record.observed_at >= start)
            and (end is None or record.observed_at <= end)
            and record.source in {"service", "log", "trace"}
        ]

        failed_logs = [
            record
            for record in incident_records
            if record.source == "log"
            and record.service == target
            and _status(record.details.get("status_code")) >= 500
        ]

        failed_spans = [
            record
            for record in incident_records
            if record.source == "trace"
            and record.service == target
            and (
                str(record.details.get("status", "")).upper()
                in {"2", "ERROR", "STATUS_CODE_ERROR"}
                or _status(record.details.get("http.status_code")) >= 500
            )
        ]

        failed_health = [
            record
            for record in incident_records
            if record.source == "service"
            and record.service == target
            and record.details.get("status") in {"unavailable", "unreachable"}
        ]

        latency_logs = [
            record
            for record in incident_records
            if record.source == "log"
            and record.service == target
            and _duration(record) is not None
            and _duration(record) >= 1400
            and 0 < _status(record.details.get("status_code")) < 500
        ]

        latency_spans = [
            record
            for record in incident_records
            if record.source == "trace"
            and record.service == target
            and _duration(record) is not None
            and _duration(record) >= 1400
            and str(record.details.get("status", "")).upper()
            not in {"2", "ERROR", "STATUS_CODE_ERROR"}
            and _status(record.details.get("http.status_code")) < 500
        ]

        source_types = {
            "log" if failed_logs else None,
            "trace" if failed_spans else None,
            "service" if failed_health else None,
            "log" if latency_logs else None,
            "trace" if latency_spans else None,
        } - {None}

        successful_payment = any(
            record.source == "log"
            and record.service == target
            and record.details.get("route") == "/pay"
            and 0 < _status(record.details.get("status_code")) < 400
            for record in incident_records
        )

        determinable = (
            experiment.mode == "failure"
            and start is not None
            and len(source_types) >= 2
            and not successful_payment
        )

        expected_affected = (
            {target}
            if failed_logs or failed_spans or failed_health or latency_logs or latency_spans
            else set()
        )

        failed_trace_ids = {
            record.trace_id
            for record in failed_logs
            if record.trace_id
        }

        configured_edges = {
            (record.service, record.details.get("downstream_service"))
            for record in records
            if record.source == "dependency"
        }

        for record in incident_records:
            if (
                record.source == "log"
                and _status(record.details.get("status_code")) >= 500
                and record.trace_id in failed_trace_ids
                and (record.service, target) in configured_edges
            ):
                expected_affected.add(record.service)

        if not determinable:
            expected_affected = set()

        failure_type = experiment.failure_type

        expected_root_cause = (
            f"{failure_type} failure injected into {target}"
            if experiment.mode == "failure"
            else f"No controlled failure injected into {target}"
        )

        return GroundTruth(
            experiment_id=experiment.experiment_id,
            injected_failure_type=(
                failure_type if experiment.mode == "failure" else "none"
            ),
            expected_root_cause=expected_root_cause,
            expected_affected_services=sorted(expected_affected),
            root_cause_determinable=determinable,
        )


class InvestigationEvaluator:
    def evaluate(
        self,
        result: InvestigationResult,
        ground_truth: GroundTruth,
    ) -> InvestigationEvaluation:
        if result.experiment_id != ground_truth.experiment_id:
            raise ValueError("ground truth belongs to a different experiment")

        # ---------------------------------------------------------
        # Root-cause correctness
        # ---------------------------------------------------------

        if ground_truth.root_cause_determinable:
            target = ground_truth.expected_root_cause.rsplit(" into ", 1)[-1]
            failure_type = ground_truth.injected_failure_type

            root_correct = (
                result.outcome == "root_cause_identified"
                and result.root_cause is not None
                and target.casefold() in result.root_cause.casefold()
                and _matches_failure_type(failure_type, result.root_cause)
            )

            root_message = (
                "Conclusion matches the controlled failure and target."
                if root_correct
                else "Conclusion does not match the injected failure and target."
            )
        else:
            root_correct = (
                result.outcome == "insufficient_evidence"
                and result.root_cause is None
            )

            root_message = (
                "Investigator abstained because the evidence was not sufficient."
                if root_correct
                else "Investigator asserted a root cause without determinative evidence."
            )

        # ---------------------------------------------------------
        # Affected-service correctness
        # ---------------------------------------------------------

        actual_affected = {
            item.service
            for item in result.affected_services
        }

        expected_affected = set(
            ground_truth.expected_affected_services
        )

        affected_correct = actual_affected == expected_affected

        # ---------------------------------------------------------
        # Citation validity
        # ---------------------------------------------------------

        valid_ids = {
            record.evidence_id
            for record in result.evidence_records
        }

        citation_ids = {
            citation.evidence_id
            for citation in result.evidence
        }

        citation_valid = (
            all(
                citation.evidence_id in valid_ids
                for citation in result.evidence
            )
            and set(result.root_cause_evidence_ids).issubset(citation_ids)
            and set(result.recommended_action_evidence_ids).issubset(
                citation_ids
            )
            and all(
                set(affected.evidence_ids).issubset(citation_ids)
                for affected in result.affected_services
            )
        )

        # ---------------------------------------------------------
        # Abstention correctness
        # ---------------------------------------------------------

        abstention_correct = (
            result.outcome == "insufficient_evidence"
            if not ground_truth.root_cause_determinable
            else result.outcome == "root_cause_identified"
        )

        dimensions = [
            root_correct,
            affected_correct,
            citation_valid,
            abstention_correct,
        ]

        # ---------------------------------------------------------
        # Evidence relevance
        # ---------------------------------------------------------

        relevant_ids = _relevant_evidence_ids(
            result,
            ground_truth,
        )

        cited_ids = {
            citation.evidence_id
            for citation in result.evidence
        }

        relevant_cited_ids = cited_ids & relevant_ids

        # Evidence precision:
        # Of everything the investigator cited, how much was relevant?
        evidence_precision_value = (
            len(relevant_cited_ids) / len(cited_ids)
            if cited_ids
            else 0.0
        )

        # Evidence coverage:
        # Of the relevant evidence available to the evaluator,
        # how much did the investigator cite?
        evidence_coverage_value = (
            len(relevant_cited_ids) / len(relevant_ids)
            if relevant_ids
            else 0.0
        )

        # ---------------------------------------------------------
        # Unsupported claim rate
        # ---------------------------------------------------------

        claim_groups: list[list[str]] = []

        if result.root_cause_evidence_ids:
            claim_groups.append(
                result.root_cause_evidence_ids
            )

        for affected in result.affected_services:
            if affected.evidence_ids:
                claim_groups.append(
                    affected.evidence_ids
                )

        if result.recommended_action_evidence_ids:
            claim_groups.append(
                result.recommended_action_evidence_ids
            )

        relevant_ids = _relevant_evidence_ids(result, ground_truth)
        unsupported_claims = sum(
            1
            for claim_evidence_ids in claim_groups
            if not any(evidence_id in cited_ids and evidence_id in relevant_ids
                       for evidence_id in claim_evidence_ids)
        )

        unsupported_claim_rate_value = (
            unsupported_claims / len(claim_groups)
            if claim_groups
            else 0.0
        )

        # ---------------------------------------------------------
        # Final evaluation
        # ---------------------------------------------------------

        return InvestigationEvaluation(
            investigation_id=result.investigation_id,
            experiment_id=result.experiment_id,
            root_cause=EvaluationDimension(
                correct=root_correct,
                explanation=root_message,
            ),
            affected_services=EvaluationDimension(
                correct=affected_correct,
                explanation=(
                    "Affected services match the evaluation record."
                    if affected_correct
                    else "Returned affected services differ from the evaluation record."
                ),
            ),
            evidence_citations=EvaluationDimension(
                correct=citation_valid,
                explanation=(
                    "Citations resolve to evidence records in this investigation."
                    if citation_valid
                    else "At least one citation does not resolve to this investigation's evidence."
                ),
            ),
            abstention=EvaluationDimension(
                correct=abstention_correct,
                explanation=(
                    "Abstention behavior matches evidence sufficiency."
                    if abstention_correct
                    else "Investigator's abstention behavior conflicts with evidence sufficiency."
                ),
            ),
            evidence_precision=EvaluationMetric(
                value=evidence_precision_value,
                explanation=(
                    "Proportion of cited evidence that is relevant "
                    "to the controlled incident."
                ),
            ),
            evidence_coverage=EvaluationMetric(
                value=evidence_coverage_value,
                explanation=(
                    "Proportion of relevant incident evidence "
                    "that was cited."
                ),
            ),
            unsupported_claim_rate=EvaluationMetric(
                value=unsupported_claim_rate_value,
                explanation=(
                    "Proportion of structured claim groups without a citation to relevant "
                    "controlled-incident evidence."
                ),
            ),
            overall=(
                "correct_investigation"
                if all(dimensions)
                else "unsupported_or_incorrect"
            ),
        )


def _relevant_evidence_ids(
    result: InvestigationResult,
    ground_truth: GroundTruth,
) -> set[str]:
    """Return evidence records relevant to the controlled incident."""

    if not ground_truth.root_cause_determinable:
        return set()

    target = ground_truth.expected_root_cause.rsplit(
        " into ", 1
    )[-1]

    expected_services = set(
        ground_truth.expected_affected_services
    )

    relevant_ids: set[str] = set()

    for record in result.evidence_records:
        if record.service not in expected_services:
            continue

        if record.source in {
            "service",
            "log",
            "trace",
            "metric",
        }:
            relevant_ids.add(record.evidence_id)

    # The experiment lifecycle and dependency records are useful
    # context but are not treated as incident evidence for precision.
    #
    # If there is no service/log/trace/metric evidence for the target,
    # fall back to target-service records so coverage remains meaningful.
    if not relevant_ids:
        relevant_ids.update(
            record.evidence_id
            for record in result.evidence_records
            if record.service == target
        )

    return relevant_ids


def _status(value: object) -> int:
    try:
        return int(value or 0)
    except (ValueError, TypeError):
        return 0


def _duration(record) -> float | None:
    value = record.details.get("duration_ms")
    try:
        return float(value) if value is not None else None
    except (ValueError, TypeError):
        return None


def _matches_failure_type(failure_type: str, claim: str) -> bool:
    text = claim.casefold()
    if failure_type.casefold() in text:
        return True
    if failure_type == "latency":
        return any(term in text for term in ("latency", "elevated request duration", "slow"))
    if failure_type in {"http_500", "http_503"}:
        status = failure_type.partition("_")[2]
        return "http" in text and status in text
    return False
