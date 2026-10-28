from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, status

from app.repositories.incidents import IncidentRepository
from app.repositories.investigations import InvestigationRepository
from app.schemas.api import (
    ExperimentCreate,
    ExperimentEvidence,
    ExperimentResponse,
    HealthResponse,
    IncidentResponse,
    ScenarioResponse,
    ServiceObservation,
    ServiceResponse,
)
from app.schemas.investigation import (
    ExperimentVerification,
    ExperimentVerificationRequest,
    InvestigationEvaluation,
    InvestigationResult,
    RemediationPlan,
)
from app.services.evaluation import InvestigationEvaluator
from app.services.evidence import PrometheusEvidenceCollector
from app.services.experiments import (
    ExperimentNotFoundError,
    ExperimentService,
    ExperimentStateError,
)
from app.services.failure_control import FailureControlError
from app.services.gemini_investigator import InvestigatorProviderError
from app.services.investigation_evidence import DEPENDENCIES
from app.services.investigation_workflow import InvestigationWorkflow
from app.services.registry import configured_services
from app.services.remediation import RemediationPlanner
from app.services.scenarios import SCENARIOS
from app.services.verification import ExperimentVerifier

router = APIRouter()


async def get_experiment_service(request: Request) -> ExperimentService:
    return request.app.state.experiment_service


ExperimentServiceDependency = Annotated[ExperimentService, Depends(get_experiment_service)]


async def get_evidence_collector(request: Request) -> PrometheusEvidenceCollector:
    return request.app.state.evidence_collector


EvidenceCollectorDependency = Annotated[
    PrometheusEvidenceCollector, Depends(get_evidence_collector)
]


async def get_investigation_workflow(request: Request) -> InvestigationWorkflow:
    return request.app.state.investigation_workflow


async def get_investigation_repository(request: Request) -> InvestigationRepository:
    return request.app.state.investigation_repository


async def get_incident_repository(request: Request) -> IncidentRepository:
    return request.app.state.incident_repository


async def get_remediation_planner(request: Request) -> RemediationPlanner:
    return request.app.state.remediation_planner


async def get_investigation_evaluator(request: Request) -> InvestigationEvaluator:
    return request.app.state.investigation_evaluator


async def get_experiment_verifier(request: Request) -> ExperimentVerifier:
    return request.app.state.experiment_verifier


def raise_experiment_error(error: Exception) -> None:
    if isinstance(error, ExperimentNotFoundError):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "experiment_not_found", "message": "Experiment was not found"},
        ) from error
    if isinstance(error, ExperimentStateError):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "invalid_experiment_state", "message": str(error)},
        ) from error
    if isinstance(error, FailureControlError):
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={"code": "failure_control_unavailable", "message": str(error)},
        ) from error
    raise error


@router.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    return HealthResponse(status="ok", service="control-api")


@router.get("/services", response_model=list[ServiceResponse])
def list_services() -> list[ServiceResponse]:
    return configured_services()


@router.get("/scenarios", response_model=list[ScenarioResponse])
def list_scenarios() -> list[ScenarioResponse]:
    return [
        ScenarioResponse(
            id=scenario.id,
            name=scenario.name,
            summary=scenario.summary,
            target_service=scenario.target_service,
            failure_type=scenario.failure_type,
            duration_seconds=scenario.duration_seconds,
            expected_behavior=scenario.expected_behavior,
            direct_services=list(scenario.direct_services),
            indirect_services=list(scenario.indirect_services),
        )
        for scenario in SCENARIOS
    ]


@router.get("/dependencies")
def service_dependencies() -> dict[str, list[str]]:
    return {service: list(downstreams) for service, downstreams in DEPENDENCIES.items()}


@router.get("/services/health", response_model=list[ServiceObservation])
async def service_health(
    collector: EvidenceCollectorDependency,
) -> list[ServiceObservation]:
    return await collector.collect_service_observations()


@router.get("/experiments", response_model=list[ExperimentResponse])
def list_experiments(service: ExperimentServiceDependency) -> list[ExperimentResponse]:
    return service.list_experiments()


@router.post(
    "/experiments",
    response_model=ExperimentResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_experiment(
    values: ExperimentCreate,
    service: ExperimentServiceDependency,
) -> ExperimentResponse:
    return service.create_experiment(values)


@router.get("/experiments/{experiment_id}", response_model=ExperimentResponse)
def get_experiment(
    experiment_id: UUID,
    service: ExperimentServiceDependency,
) -> ExperimentResponse:
    try:
        return service.get_experiment(experiment_id)
    except ExperimentNotFoundError as exc:
        raise_experiment_error(exc)


@router.get("/experiments/{experiment_id}/evidence", response_model=ExperimentEvidence)
async def get_experiment_evidence(
    experiment_id: UUID,
    service: ExperimentServiceDependency,
    collector: EvidenceCollectorDependency,
) -> ExperimentEvidence:
    try:
        experiment = service.get_experiment(experiment_id)
    except ExperimentNotFoundError as exc:
        raise_experiment_error(exc)
    return await collector.collect(experiment)


@router.post("/experiments/{experiment_id}/investigate", response_model=InvestigationResult)
async def investigate_experiment(
    experiment_id: UUID,
    service: ExperimentServiceDependency,
    workflow: Annotated[InvestigationWorkflow, Depends(get_investigation_workflow)],
) -> InvestigationResult:
    try:
        experiment = service.get_experiment(experiment_id)
    except ExperimentNotFoundError as exc:
        raise_experiment_error(exc)
    try:
        return await workflow.run(experiment)
    except InvestigatorProviderError as exc:
        raise HTTPException(
            status_code=(
                status.HTTP_503_SERVICE_UNAVAILABLE
                if "GEMINI_API_KEY is not configured" in str(exc)
                else status.HTTP_502_BAD_GATEWAY
            ),
            detail={"code": "investigator_provider_error", "message": str(exc)},
        ) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={"code": "investigation_invalid_result", "message": str(exc)},
        ) from exc


@router.get("/investigations/{investigation_id}", response_model=InvestigationResult)
def get_investigation(
    investigation_id: UUID,
    repository: Annotated[InvestigationRepository, Depends(get_investigation_repository)],
) -> InvestigationResult:
    saved = repository.get(investigation_id)
    if saved is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "investigation_not_found", "message": "Investigation was not found"},
        )
    return saved[0]


@router.get("/investigations", response_model=list[InvestigationResult])
def list_investigations(
    repository: Annotated[InvestigationRepository, Depends(get_investigation_repository)],
    experiment_id: UUID | None = None,
    limit: int = 20,
) -> list[InvestigationResult]:
    if limit < 1 or limit > 100:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"code": "invalid_limit", "message": "limit must be between 1 and 100"},
        )
    return repository.list_results(experiment_id=experiment_id, limit=limit)


@router.get("/investigations/{investigation_id}/remediation", response_model=RemediationPlan)
def get_remediation_plan(
    investigation_id: UUID,
    repository: Annotated[InvestigationRepository, Depends(get_investigation_repository)],
    planner: Annotated[RemediationPlanner, Depends(get_remediation_planner)],
) -> RemediationPlan:
    saved = repository.get(investigation_id)
    if saved is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "investigation_not_found", "message": "Investigation was not found"},
        )
    try:
        return planner.plan(saved[0])
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"code": "invalid_remediation_data", "message": str(exc)},
        ) from exc


@router.post("/investigations/{investigation_id}/evaluate", response_model=InvestigationEvaluation)
def evaluate_investigation(
    investigation_id: UUID,
    repository: Annotated[InvestigationRepository, Depends(get_investigation_repository)],
    evaluator: Annotated[InvestigationEvaluator, Depends(get_investigation_evaluator)],
) -> InvestigationEvaluation:
    saved = repository.get(investigation_id)
    if saved is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "investigation_not_found", "message": "Investigation was not found"},
        )
    try:
        return evaluator.evaluate(*saved)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"code": "invalid_evaluation_data", "message": str(exc)},
        ) from exc


@router.post("/experiments/{experiment_id}/verify", response_model=ExperimentVerification)
def verify_experiments(
    experiment_id: UUID,
    values: ExperimentVerificationRequest,
    service: ExperimentServiceDependency,
    repository: Annotated[InvestigationRepository, Depends(get_investigation_repository)],
    verifier: Annotated[ExperimentVerifier, Depends(get_experiment_verifier)],
    request: Request,
) -> ExperimentVerification:
    if experiment_id == values.verification_experiment_id:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "experiments_must_differ", "message": "Use distinct experiments"},
        )
    try:
        baseline_experiment = service.get_experiment(experiment_id)
        verification_experiment = service.get_experiment(values.verification_experiment_id)
    except ExperimentNotFoundError as exc:
        raise_experiment_error(exc)
    if baseline_experiment.mode != "failure" or verification_experiment.mode != "verification":
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "code": "invalid_verification_pair",
                "message": (
                    "Verification compares a failure experiment with a verification experiment"
                ),
            },
        )
    baseline_saved = repository.latest_for_experiment(experiment_id)
    verification_saved = repository.latest_for_experiment(values.verification_experiment_id)
    if baseline_saved is None or verification_saved is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "code": "investigation_snapshot_not_found",
                "message": "Investigate both experiments before comparing them",
            },
        )
    try:
        result = verifier.compare(baseline_saved[0], verification_saved[0])
        request.app.state.verification_repository.save(result)
        return result
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"code": "invalid_verification_data", "message": str(exc)},
        ) from exc


@router.post("/experiments/{experiment_id}/start", response_model=ExperimentResponse)
async def start_experiment(
    experiment_id: UUID,
    service: ExperimentServiceDependency,
) -> ExperimentResponse:
    try:
        return await service.start_experiment(experiment_id)
    except (ExperimentNotFoundError, ExperimentStateError, FailureControlError) as exc:
        raise_experiment_error(exc)


@router.post("/experiments/{experiment_id}/stop", response_model=ExperimentResponse)
async def stop_experiment(
    experiment_id: UUID,
    service: ExperimentServiceDependency,
) -> ExperimentResponse:
    try:
        return await service.stop_experiment(experiment_id)
    except (ExperimentNotFoundError, ExperimentStateError, FailureControlError) as exc:
        raise_experiment_error(exc)


@router.get("/incidents", response_model=list[IncidentResponse])
def list_incidents(
    repository: Annotated[IncidentRepository, Depends(get_incident_repository)],
) -> list[IncidentResponse]:
    return repository.list()
