from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from failurelab_observability import configure_observability
from fastapi import FastAPI

from app.api.routes import router
from app.core.config import settings
from app.core.database import SessionLocal
from app.repositories.incidents import IncidentRepository
from app.repositories.investigations import InvestigationRepository, VerificationRepository
from app.services.dependencies import create_experiment_service
from app.services.evaluation import GroundTruthBuilder, InvestigationEvaluator
from app.services.evidence import PrometheusEvidenceCollector
from app.services.experiments import ExperimentService
from app.services.investigation_evidence import InvestigationEvidenceService
from app.services.investigation_workflow import InvestigationWorkflow
from app.services.investigator import Investigator, create_investigator_provider
from app.services.log_provider import LogProvider
from app.services.remediation import RemediationPlanner
from app.services.trace_client import TraceClient
from app.services.verification import ExperimentVerifier


@asynccontextmanager
async def lifespan(application: FastAPI) -> AsyncIterator[None]:
    service: ExperimentService = application.state.experiment_service
    await service.resume_running_experiments()
    try:
        yield
    finally:
        await service.shutdown()


app = FastAPI(title="FailureLab Control API", version="0.1.0", lifespan=lifespan)
app.state.experiment_service = create_experiment_service()
app.state.incident_repository = IncidentRepository(SessionLocal)
app.state.log_provider = LogProvider()
app.state.trace_client = TraceClient()
app.state.evidence_collector = PrometheusEvidenceCollector(
    log_provider=app.state.log_provider,
    trace_client=app.state.trace_client,
)
app.state.investigation_evidence = InvestigationEvidenceService(
    app.state.evidence_collector, app.state.log_provider, app.state.trace_client
)
app.state.investigator = Investigator(
    create_investigator_provider(
        settings.investigator_provider,
        gemini_api_key=settings.gemini_api_key,
        gemini_model=settings.gemini_model,
    ),
    gemini_fallback_to_local=settings.gemini_fallback_to_local,
)
app.state.investigation_repository = InvestigationRepository(SessionLocal)
app.state.verification_repository = VerificationRepository(SessionLocal)
app.state.investigation_workflow = InvestigationWorkflow(
    app.state.investigation_evidence,
    app.state.investigator,
    app.state.investigation_repository,
    GroundTruthBuilder(),
)
app.state.remediation_planner = RemediationPlanner()
app.state.investigation_evaluator = InvestigationEvaluator()
app.state.experiment_verifier = ExperimentVerifier()
app.include_router(router)
configure_observability(app, "failurelab-control-api")
