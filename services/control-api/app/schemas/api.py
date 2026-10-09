from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class HealthResponse(BaseModel):
    status: Literal["ok"]
    service: str


class ServiceResponse(BaseModel):
    id: str
    name: str
    url: str
    status: str


class IncidentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    experiment_id: UUID
    severity: str
    status: str
    started_at: datetime | None = None
    ended_at: datetime | None = None
    created_at: datetime


class ExperimentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    service_id: UUID
    scenario: str
    status: str
    started_at: datetime | None = None
    ended_at: datetime | None = None
    created_at: datetime
