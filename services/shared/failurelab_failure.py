"""Local, time-limited failure control shared by FailureLab application services.

Health checks stay immediate so a latency or timeout injection cannot stall the
process health endpoint. Business routes call ``apply_request_failure``.
"""

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from threading import Lock
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, field_validator

LATENCY_SECONDS = 1.5
TIMEOUT_SECONDS = 4.0


class FailureType(StrEnum):
    UNAVAILABLE = "unavailable"
    HTTP_500 = "http_500"
    LATENCY = "latency"
    TIMEOUT = "timeout"


@dataclass(frozen=True)
class FailureState:
    failure_type: FailureType | None = None
    experiment_id: UUID | None = None
    started_at: datetime | None = None
    ended_at: datetime | None = None
    active: bool = False


class FailureAlreadyActiveError(Exception):
    pass


class FailureController:
    def __init__(self) -> None:
        self._lock = Lock()
        self._state = FailureState()

    def activate(
        self, failure_type: FailureType | str, experiment_id: UUID | None = None
    ) -> FailureState:
        failure_type = FailureType(failure_type)
        with self._lock:
            if self._state.active:
                if (
                    self._state.failure_type == failure_type
                    and self._state.experiment_id == experiment_id
                ):
                    return self._state
                raise FailureAlreadyActiveError("A different failure experiment is already active")
            self._state = FailureState(
                failure_type=failure_type,
                experiment_id=experiment_id,
                started_at=datetime.now(UTC),
                active=True,
            )
            return self._state

    def deactivate(self, experiment_id: UUID | None = None) -> FailureState:
        with self._lock:
            if not self._state.active:
                return self._state
            if experiment_id is not None and self._state.experiment_id != experiment_id:
                raise FailureAlreadyActiveError("The active failure belongs to another experiment")
            self._state = FailureState(
                failure_type=self._state.failure_type,
                experiment_id=self._state.experiment_id,
                started_at=self._state.started_at,
                ended_at=datetime.now(UTC),
                active=False,
            )
            return self._state

    def is_active(self) -> bool:
        with self._lock:
            return self._state.active

    @property
    def current_state(self) -> FailureState:
        with self._lock:
            return self._state


def health_status_code(state: FailureState) -> int | None:
    """Immediate health status. Latency and timeout do not change it."""
    if not state.active or state.failure_type is None:
        return None
    if state.failure_type == FailureType.UNAVAILABLE:
        return 503
    if state.failure_type == FailureType.HTTP_500:
        return 500
    return None


async def apply_request_failure(state: FailureState) -> None:
    if not state.active or state.failure_type is None:
        return
    if state.failure_type == FailureType.LATENCY:
        await asyncio.sleep(LATENCY_SECONDS)
        return
    if state.failure_type == FailureType.TIMEOUT:
        await asyncio.sleep(TIMEOUT_SECONDS)
        raise HTTPException(
            status_code=504,
            detail={"code": "injected_timeout", "message": "Injected request timeout"},
        )
    if state.failure_type == FailureType.UNAVAILABLE:
        raise HTTPException(
            status_code=503,
            detail={"code": "service_unavailable", "message": "Injected service unavailable"},
        )
    if state.failure_type == FailureType.HTTP_500:
        raise HTTPException(
            status_code=500,
            detail={"code": "injected_http_500", "message": "Injected HTTP 500"},
        )


class FailureStateResponse(BaseModel):
    active: bool
    failure_type: str | None
    experiment_id: UUID | None
    started_at: str | None
    ended_at: str | None


def serialize_state(state: FailureState) -> FailureStateResponse:
    return FailureStateResponse(
        active=state.active,
        failure_type=state.failure_type.value if state.failure_type else None,
        experiment_id=state.experiment_id,
        started_at=state.started_at.isoformat() if state.started_at else None,
        ended_at=state.ended_at.isoformat() if state.ended_at else None,
    )


def build_failure_router(
    controller: FailureController,
    allowed: frozenset[str],
) -> APIRouter:
    router = APIRouter(prefix="/internal/failure-control", include_in_schema=False)

    class ActivateRequest(BaseModel):
        failure_type: str
        experiment_id: UUID

        @field_validator("failure_type")
        @classmethod
        def supported_failure(cls, value: str) -> str:
            if value not in allowed:
                raise ValueError(f"unsupported failure type: {value}")
            return value

    class DeactivateRequest(BaseModel):
        experiment_id: UUID

    def get_controller() -> FailureController:
        return controller

    @router.post("/activate", response_model=FailureStateResponse)
    def activate_failure(
        request: ActivateRequest,
        failure_controller: FailureController = Depends(get_controller),  # noqa: B008
    ) -> FailureStateResponse:
        try:
            state = failure_controller.activate(request.failure_type, request.experiment_id)
        except FailureAlreadyActiveError as exc:
            raise HTTPException(
                status_code=409,
                detail={"code": "failure_already_active", "message": str(exc)},
            ) from exc
        return serialize_state(state)

    @router.post("/deactivate", response_model=FailureStateResponse)
    def deactivate_failure(
        request: DeactivateRequest,
        failure_controller: FailureController = Depends(get_controller),  # noqa: B008
    ) -> FailureStateResponse:
        try:
            state = failure_controller.deactivate(request.experiment_id)
        except FailureAlreadyActiveError as exc:
            raise HTTPException(
                status_code=409,
                detail={"code": "failure_experiment_mismatch", "message": str(exc)},
            ) from exc
        return serialize_state(state)

    return router
