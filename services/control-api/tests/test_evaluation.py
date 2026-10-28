from __future__ import annotations

import asyncio
import pytest

from app.services.evaluation import GroundTruthBuilder, InvestigationEvaluator
from app.services.gemini_investigator import GeminiReasoningProvider, TransientGeminiError
from app.services.investigator import Investigator, LocalInvestigator
from benchmarks.comparison import compare_scenarios, render_comparison
from benchmarks.scenarios import make_scenarios


def test_controlled_scenarios_have_known_ground_truth() -> None:
    scenarios = make_scenarios()
    assert [scenario.name for scenario in scenarios] == [
        "HTTP 500",
        "Latency",
        "Timeout",
        "Insufficient evidence",
    ]

    builder = GroundTruthBuilder()
    truths = [builder.build(scenario.context) for scenario in scenarios]
    assert [truth.injected_failure_type for truth in truths] == [
        "http_500",
        "latency",
        "timeout",
        "http_500",
    ]
    assert [truth.root_cause_determinable for truth in truths] == [True, True, True, False]
    assert truths[1].expected_affected_services == ["inventory-service"]


def test_provider_comparison_uses_same_context_and_ground_truth() -> None:
    scenarios = make_scenarios()
    entries = asyncio.run(compare_scenarios(scenarios, gemini_mode="mock"))

    assert len(entries) == len(scenarios) * 2
    for index, scenario in enumerate(scenarios):
        local, gemini = entries[index * 2 : index * 2 + 2]
        assert local.scenario == gemini.scenario == scenario.name
        assert local.provider == "Local"
        assert gemini.provider == "Gemini (mock)"
        assert local.ground_truth is gemini.ground_truth
        assert local.result.experiment_id == gemini.result.experiment_id
        assert local.result.evidence_records == scenario.context.evidence_records
        assert gemini.result.evidence_records == scenario.context.evidence_records
        assert {item.evidence_id for item in local.result.evidence_records} == {
            item.evidence_id for item in gemini.result.evidence_records
        }

    output = render_comparison(entries)
    assert "Gemini figures above are mocked" in output
    assert "Root accuracy" in output
    assert "Latency" in output


def test_evaluator_reports_precision_coverage_and_unsupported_claims() -> None:
    scenario = make_scenarios()[0]
    ground_truth = GroundTruthBuilder().build(scenario.context)
    result = asyncio.run(Investigator(LocalInvestigator()).investigate(scenario.context))
    assert ground_truth.root_cause_determinable

    experiment_evidence = next(
        record for record in result.evidence_records if record.source == "experiment"
    )
    altered = result.model_copy(
        update={"root_cause_evidence_ids": [experiment_evidence.evidence_id]}
    )
    evaluation = InvestigationEvaluator().evaluate(altered, ground_truth)

    assert evaluation.evidence_precision.value < 1.0
    assert evaluation.evidence_coverage.value == 1.0
    assert evaluation.unsupported_claim_rate.value > 0.0


def test_real_comparison_requires_explicit_credentials() -> None:
    try:
        asyncio.run(compare_scenarios(make_scenarios()[:1], gemini_mode="real"))
    except ValueError as error:
        assert "requires GEMINI_API_KEY" in str(error)
    else:
        raise AssertionError("real comparison should require an API key")


def test_comparison_finishes_on_gemini_outage_without_scoring_fallback(monkeypatch) -> None:
    async def unavailable(self, context):
        raise TransientGeminiError("Gemini investigation failed transiently (HTTP 503)")

    monkeypatch.setattr(GeminiReasoningProvider, "assess", unavailable)
    scenarios = make_scenarios()[:1]
    entries = asyncio.run(
        compare_scenarios(scenarios, gemini_mode="real", api_key="test-key")
    )

    assert [entry.provider for entry in entries] == ["Local", "Gemini unavailable", "Local fallback"]
    assert entries[1].evaluation is None
    assert entries[2].evaluation is None
    assert entries[2].result.provider == "local_fallback"
    output = render_comparison(entries)
    assert "no real Gemini quality comparison" in output
    assert "real successful scenarios: 0" in output
    assert "fallback scenarios: 1" in output

    disabled = asyncio.run(
        compare_scenarios(
            scenarios,
            gemini_mode="real",
            api_key="test-key",
            fallback_to_local=False,
        )
    )
    assert [entry.provider for entry in disabled] == ["Local", "Gemini unavailable"]
    assert "Fallback: local" not in render_comparison(disabled)
