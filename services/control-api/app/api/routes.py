from fastapi import APIRouter

from app.schemas.api import (
    ExperimentResponse,
    HealthResponse,
    IncidentResponse,
    ServiceResponse,
)
from app.services.registry import configured_services

router = APIRouter()


@router.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    return HealthResponse(status="ok", service="control-api")


@router.get("/services", response_model=list[ServiceResponse])
def list_services() -> list[ServiceResponse]:
    return configured_services()


@router.get("/experiments", response_model=list[ExperimentResponse])
def list_experiments() -> list[ExperimentResponse]:
    return []


@router.get("/incidents", response_model=list[IncidentResponse])
def list_incidents() -> list[IncidentResponse]:
    return []
