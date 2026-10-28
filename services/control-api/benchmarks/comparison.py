from __future__ import annotations

import time
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Literal

from app.schemas.investigation import (
    GroundTruth,
    InvestigationContext,
    InvestigationEvaluation,
    InvestigationResult,
)
from app.services.evaluation import GroundTruthBuilder, InvestigationEvaluator
from app.services.gemini_investigator import (
    GeminiAssessment,
    GeminiReasoningProvider,
    InvestigatorProviderError,
    TransientGeminiError,
)
from app.services.investigator import Investigator, LocalInvestigator

GeminiMode = Literal["mock", "real"]


@dataclass(frozen=True)
class ComparisonScenario:
    name: str
    context: InvestigationContext


@dataclass(frozen=True)
class ComparisonEntry:
    scenario: str
    provider: str
    evaluation_mode: str
    ground_truth: GroundTruth
    result: InvestigationResult | None
    evaluation: InvestigationEvaluation | None
    latency_ms: float
    error: str | None = None


class _MockGeminiClient:
    """Structured mock derived from the deterministic result; never a model-quality claim."""

    def __init__(self, context: InvestigationContext, local_result: InvestigationResult) -> None:
        failure_type = context.experiment.failure_type
        target = context.experiment.target_service
        root_cause = {
            "latency": (
                f"The {target} exhibited elevated request duration during the incident window."
            ),
            "timeout": (f"The {target} experienced timeout behavior during the incident window."),
            "http_500": (f"The {target} experienced HTTP 500 failure during the incident window."),
            "http_503": (f"The {target} experienced HTTP 503 failure during the incident window."),
        }.get(
            failure_type,
            f"The {target} experienced an HTTP failure during the incident window.",
        )
        assessment = GeminiAssessment(
            outcome=local_result.outcome,
            root_cause=root_cause if local_result.root_cause is not None else None,
            root_cause_evidence_ids=local_result.root_cause_evidence_ids,
            affected_services=[item.model_dump() for item in local_result.affected_services],
            timeline_evidence_ids=[
                item.evidence_id for item in local_result.timeline if item.evidence_id
            ],
            confidence_rationale="Mock assessment for pipeline verification only.",
            recommended_action=(
                f"Inspect {target} {local_result.recommended_action}"
                if local_result.root_cause is not None
                else local_result.recommended_action
            ),
            recommended_action_evidence_ids=local_result.recommended_action_evidence_ids,
            missing_evidence=local_result.missing_evidence,
            limitations=local_result.limitations,
        )
        self._response = assessment.model_dump_json()
        self.models = self
        self.request: dict | None = None

    def generate_content(self, **kwargs):
        self.request = kwargs
        return SimpleNamespace(text=self._response)


async def compare_scenarios(
    scenarios: list[ComparisonScenario],
    *,
    gemini_mode: GeminiMode = "mock",
    api_key: str = "",
    model: str = "gemini-3.8-flash",
    fallback_to_local: bool = True,
) -> list[ComparisonEntry]:
    """Run both providers on each identical context and score against one GroundTruth."""
    if gemini_mode == "real" and not api_key.strip():
        raise ValueError("Real Gemini comparison requires GEMINI_API_KEY in the environment.")

    truth_builder = GroundTruthBuilder()
    evaluator = InvestigationEvaluator()
    entries: list[ComparisonEntry] = []

    for scenario in scenarios:
        context = scenario.context
        ground_truth: GroundTruth = truth_builder.build(context)

        local_started = time.perf_counter()
        local_result = await Investigator(LocalInvestigator()).investigate(context)
        local_latency = (time.perf_counter() - local_started) * 1000
        entries.append(
            ComparisonEntry(
                scenario=scenario.name,
                provider="Local",
                evaluation_mode="deterministic local provider",
                ground_truth=ground_truth,
                result=local_result,
                evaluation=evaluator.evaluate(local_result, ground_truth),
                latency_ms=local_latency,
            )
        )

        client = _MockGeminiClient(context, local_result) if gemini_mode == "mock" else None
        gemini = GeminiReasoningProvider(api_key, model, client=client)
        gemini_started = time.perf_counter()
        try:
            gemini_result = await Investigator(gemini).investigate(context)
        except TransientGeminiError as exc:
            gemini_latency = (time.perf_counter() - gemini_started) * 1000
            entries.append(
                ComparisonEntry(
                    scenario=scenario.name,
                    provider="Gemini unavailable",
                    evaluation_mode="transient upstream failure; no Gemini quality score",
                    ground_truth=ground_truth,
                    result=None,
                    evaluation=None,
                    latency_ms=gemini_latency,
                    error=str(exc),
                )
            )
            if not fallback_to_local:
                continue
            fallback = await Investigator(LocalInvestigator()).investigate(context)
            fallback_data = fallback.model_dump()
            fallback_data["provider"] = "local_fallback"
            fallback_data["degraded"] = True
            fallback_data["limitations"] = [
                *fallback_data["limitations"],
                f"Gemini unavailable: {exc}",
            ]
            fallback = InvestigationResult.model_validate(fallback_data)
            entries.append(
                ComparisonEntry(
                    scenario=scenario.name,
                    provider="Local fallback",
                    evaluation_mode="deterministic fallback after Gemini transient failure; excluded from provider metrics",
                    ground_truth=ground_truth,
                    result=fallback,
                    evaluation=None,
                    latency_ms=0.0,
                    error=str(exc),
                )
            )
            continue
        except InvestigatorProviderError as exc:
            gemini_latency = (time.perf_counter() - gemini_started) * 1000
            entries.append(
                ComparisonEntry(
                    scenario=scenario.name,
                    provider="Gemini error",
                    evaluation_mode="provider error; no Gemini quality score",
                    ground_truth=ground_truth,
                    result=None,
                    evaluation=None,
                    latency_ms=gemini_latency,
                    error=str(exc),
                )
            )
            continue
        except ValueError as exc:
            # Invalid structured output/grounding is recorded as a provider
            # error, never converted into a successful local fallback.
            gemini_latency = (time.perf_counter() - gemini_started) * 1000
            entries.append(
                ComparisonEntry(
                    scenario=scenario.name,
                    provider="Gemini error",
                    evaluation_mode="invalid provider result; no Gemini quality score",
                    ground_truth=ground_truth,
                    result=None,
                    evaluation=None,
                    latency_ms=gemini_latency,
                    error=str(exc),
                )
            )
            continue
        gemini_latency = (time.perf_counter() - gemini_started) * 1000
        entries.append(
            ComparisonEntry(
                scenario=scenario.name,
                provider="Gemini (mock)" if gemini_mode == "mock" else "Gemini",
                evaluation_mode=(
                    "mock derived from Local result; pipeline check only"
                    if gemini_mode == "mock"
                    else "real Gemini API"
                ),
                ground_truth=ground_truth,
                result=gemini_result,
                evaluation=evaluator.evaluate(gemini_result, ground_truth),
                latency_ms=gemini_latency,
            )
        )

    return entries


def render_comparison(entries: list[ComparisonEntry]) -> str:
    if not entries:
        return "No scenarios were evaluated."
    lines = [
        "FailureLab AI Investigator Evaluation",
        "",
        (
            "Scenario               Provider     Result                    Root  Affected  "
            "Cite  Precision  Coverage  Unsupported  Abstain  Confidence  Latency"
        ),
        "------------------------------------------------------------------------------------------------------------------------------------------------",
    ]
    for entry in entries:
        evaluation = entry.evaluation
        if evaluation is None:
            continue
        lines.append(
                f"{entry.scenario:<25} {entry.provider:<20} {evaluation.overall:<30} "
            f"{_yes(evaluation.root_cause.correct):>4}  "
            f"{_yes(evaluation.affected_services.correct):>8}  "
            f"{_yes(evaluation.evidence_citations.correct):>4}  "
            f"{evaluation.evidence_precision.value:>9.2f}  "
            f"{evaluation.evidence_coverage.value:>8.2f}  "
            f"{evaluation.unsupported_claim_rate.value:>11.2f}  "
            f"{_yes(evaluation.abstention.correct):>7}  "
            f"{entry.result.confidence:>10.2f}  {entry.latency_ms:>7.1f} ms"
        )

    lines.extend(
        [
            "",
            "Provider aggregates (mean across evaluated scenarios)",
            "Provider       Root accuracy  Affected accuracy  Citation validity  Precision  "
            "Coverage  Unsupported  Abstention  Confidence  Latency",
            "---------------------------------------------------------------------------------------------------------------------------------------",
        ]
    )
    providers = list(dict.fromkeys(
        entry.provider for entry in entries if entry.evaluation is not None
    ))
    for provider in providers:
        group = [entry for entry in entries if entry.provider == provider and entry.evaluation is not None]
        lines.append(
            f"{provider:<14} "
            f"{_average(group, lambda e: float(e.evaluation.root_cause.correct)):>13.2f}  "
            f"{_average(group, lambda e: float(e.evaluation.affected_services.correct)):>17.2f}  "
            f"{_average(group, lambda e: float(e.evaluation.evidence_citations.correct)):>17.2f}  "
            f"{_average(group, lambda e: e.evaluation.evidence_precision.value):>9.2f}  "
            f"{_average(group, lambda e: e.evaluation.evidence_coverage.value):>8.2f}  "
            f"{_average(group, lambda e: e.evaluation.unsupported_claim_rate.value):>11.2f}  "
            f"{_average(group, lambda e: float(e.evaluation.abstention.correct)):>10.2f}  "
            f"{_average(group, lambda e: e.result.confidence):>10.2f}  "
            f"{_average(group, lambda e: e.latency_ms):>7.1f} ms"
        )

    if any(entry.provider == "Gemini (mock)" for entry in entries):
        lines.extend(
            [
                "",
                "Gemini figures above are mocked from the Local result and verify the "
                "comparison pipeline only.",
                "They are not measurements of Gemini model quality or API latency.",
            ]
        )
    unavailable = [entry for entry in entries if entry.provider == "Gemini unavailable"]
    real_successes = sum(entry.provider == "Gemini" for entry in entries)
    local_count = sum(entry.provider == "Local" for entry in entries)
    fallback_count = sum(entry.provider == "Local fallback" for entry in entries)
    provider_errors = [entry for entry in entries if entry.provider == "Gemini error"]
    if any(entry.provider in {"Gemini", "Gemini unavailable", "Gemini error", "Local fallback"} for entry in entries):
        lines.extend([
            "",
            "Availability summary",
            f"Gemini: {'unavailable' if unavailable and not real_successes else 'partial' if unavailable else 'available'}; "
            f"real successful scenarios: {real_successes}; transient failures: {len(unavailable)}; "
            f"fallback scenarios: {fallback_count}",
            f"Local benchmark scenarios: {local_count}; real Gemini metrics include only successful Gemini responses.",
        ])
        for entry in unavailable:
            has_fallback = any(
                item.scenario == entry.scenario and item.provider == "Local fallback"
                for item in entries
            )
            fallback_label = "Fallback: local" if has_fallback else "Fallback: disabled"
            lines.append(
                f"Scenario: {entry.scenario}; Local: success; Gemini: unavailable ({entry.error}); "
                f"{fallback_label}; Comparison: Gemini unavailable; no real Gemini quality comparison for this scenario"
            )
        for entry in provider_errors:
            lines.append(f"Scenario: {entry.scenario}; Gemini: provider error ({entry.error}); no Gemini quality score")
    return "\n".join(lines)


def _yes(value: bool) -> str:
    return "yes" if value else "no"


def _average(entries: list[ComparisonEntry], value) -> float:
    return sum(value(entry) for entry in entries) / len(entries)
