from app.models.entities import Experiment
from app.repositories.investigations import InvestigationRepository
from app.schemas.investigation import InvestigationResult
from app.services.evaluation import GroundTruthBuilder
from app.services.investigation_evidence import InvestigationEvidenceService
from app.services.investigator import Investigator


class InvestigationWorkflow:
    def __init__(
        self,
        collector: InvestigationEvidenceService,
        investigator: Investigator,
        repository: InvestigationRepository,
        ground_truth_builder: GroundTruthBuilder,
    ) -> None:
        self._collector = collector
        self._investigator = investigator
        self._repository = repository
        self._ground_truth_builder = ground_truth_builder

    async def run(self, experiment: Experiment) -> InvestigationResult:
        context = await self._collector.collect(experiment)
        # Ground truth is built after inference and is never passed into the investigator.
        result = await self._investigator.investigate(context)
        ground_truth = self._ground_truth_builder.build(context)
        self._repository.save(result, ground_truth)
        return result
