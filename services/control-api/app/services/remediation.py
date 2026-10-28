from __future__ import annotations

import hashlib

from app.schemas.investigation import (
    InvestigationResult,
    RemediationAction,
    RemediationPlan,
)


class RemediationPlanner:
    """Deterministic, evidence-linked next-step suggestions; no action is executed."""

    def plan(self, result: InvestigationResult) -> RemediationPlan:
        plan_id = "plan_" + hashlib.sha256(str(result.investigation_id).encode()).hexdigest()[:20]
        if result.outcome == "insufficient_evidence":
            return RemediationPlan(
                plan_id=plan_id,
                investigation_id=result.investigation_id,
                experiment_id=result.experiment_id,
                outcome="insufficient_evidence",
                actions=[],
                evidence_records=result.evidence_records,
                limitations=[
                    "No remediation is recommended until evidence supports a root-cause finding."
                ],
            )

        records = {record.evidence_id: record for record in result.evidence_records}
        lifecycle = next(
            (
                record
                for record in result.evidence_records
                if record.source == "experiment" and record.event == "created"
            ),
            None,
        )
        actions: list[RemediationAction] = []
        if lifecycle and lifecycle.details.get("status") == "RUNNING":
            started = next(
                (
                    record
                    for record in result.evidence_records
                    if record.source == "experiment" and record.event == "failure_started"
                ),
                lifecycle,
            )
            actions.append(
                _action(
                    result,
                    "stop_experiment",
                    "high",
                    "Stop the controlled experiment",
                    "The persisted experiment lifecycle shows that the controlled failure is "
                    "running.",
                    "The injected failure should no longer be active after the experiment is "
                    "stopped.",
                    [started.evidence_id],
                )
            )

        target_service = next(
            (
                record.service
                for record in result.evidence_records
                if record.source == "experiment" and record.event == "created"
            ),
            None,
        )
        target_failure_ids = [
            citation.evidence_id
            for citation in result.evidence
            if citation.service == target_service
            and records[citation.evidence_id].source in {"log", "trace", "service"}
            and citation.evidence_id in result.root_cause_evidence_ids
        ]
        if not target_failure_ids:
            target_failure_ids = result.root_cause_evidence_ids
        actions.append(
            _action(
                result,
                "verify_recovery",
                "medium",
                "Check target health and repeat the affected request",
                "The investigation found target-service failure evidence during this experiment.",
                "A successful health check and request after the experiment would support "
                "recovery; this plan does not assert that recovery has occurred.",
                target_failure_ids,
            )
        )
        for affected in result.affected_services:
            if affected.impact != "indirect":
                continue
            actions.append(
                _action(
                    result,
                    "inspect_dependency",
                    "low",
                    f"Recheck {affected.service} after target recovery",
                    f"The investigation reports indirect impact to {affected.service} with cited "
                    "request and dependency evidence.",
                    "Repeating the dependent request after target recovery can confirm whether "
                    "the observed impact cleared.",
                    affected.evidence_ids,
                )
            )
        return RemediationPlan(
            plan_id=plan_id,
            investigation_id=result.investigation_id,
            experiment_id=result.experiment_id,
            outcome="planned",
            actions=actions,
            evidence_records=result.evidence_records,
        )


def _action(
    result: InvestigationResult,
    action: str,
    priority: str,
    description: str,
    rationale: str,
    expected_effect: str,
    evidence_ids: list[str],
) -> RemediationAction:
    action_id = (
        "act_" + hashlib.sha256(f"{result.investigation_id}:{action}".encode()).hexdigest()[:20]
    )
    return RemediationAction(
        action_id=action_id,
        action=action,
        description=description,
        priority=priority,
        rationale=rationale,
        expected_effect=expected_effect,
        evidence_ids=list(dict.fromkeys(evidence_ids)),
    )
