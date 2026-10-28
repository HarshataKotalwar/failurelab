from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Protocol

from app.models.entities import Experiment
from app.repositories.experiments import (
    ActiveExperimentExistsError,
)
from app.schemas.api import ExperimentCreate
from app.services.failure_control import (
    FailureControlConflict,
    FailureControlError,
)

logger = logging.getLogger(__name__)


class ExperimentRepositoryPort(Protocol):
    def create(self, values: ExperimentCreate) -> Experiment: ...

    def list(self) -> list[Experiment]: ...

    def get(self, experiment_id: uuid.UUID) -> Experiment | None: ...

    def has_running(self) -> bool: ...

    def list_running(self) -> list[Experiment]: ...

    def mark_running(self, experiment_id: uuid.UUID, started_at: datetime) -> Experiment | None: ...

    def mark_stopped(self, experiment_id: uuid.UUID, ended_at: datetime) -> Experiment | None: ...


class FailureControlClient(Protocol):
    async def activate(
        self, failure_type: str, experiment_id: uuid.UUID, *, target_service: str
    ) -> None: ...

    async def deactivate(self, experiment_id: uuid.UUID, *, target_service: str) -> None: ...


class ExperimentNotFoundError(Exception):
    pass


class ExperimentStateError(Exception):
    pass


class ExperimentService:
    def __init__(
        self,
        repository: ExperimentRepositoryPort,
        failure_control: FailureControlClient,
        *,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._repository = repository
        self._failure_control = failure_control
        self._sleep = sleep
        self._now = now
        self._lock = asyncio.Lock()
        self._expiry_tasks: dict[uuid.UUID, asyncio.Task[None]] = {}

    def list_experiments(self) -> list[Experiment]:
        return self._repository.list()

    def get_experiment(self, experiment_id: uuid.UUID) -> Experiment:
        experiment = self._repository.get(experiment_id)
        if experiment is None:
            raise ExperimentNotFoundError
        return experiment

    def create_experiment(self, values: ExperimentCreate) -> Experiment:
        experiment = self._repository.create(values)
        logger.info(
            "experiment status transition",
            extra={
                "event": "experiment_status_transition",
                "experiment_id": str(experiment.id),
                "target_service": experiment.target_service,
                "failure_type": experiment.failure_type,
                "experiment_status": experiment.status,
            },
        )
        return experiment

    async def start_experiment(self, experiment_id: uuid.UUID) -> Experiment:
        async with self._lock:
            experiment = self.get_experiment(experiment_id)
            if experiment.status != "CREATED":
                raise ExperimentStateError("Only CREATED experiments can be started")
            if self._repository.has_running():
                raise ExperimentStateError("Another experiment is already RUNNING")

            if experiment.mode == "failure":
                try:
                    await self._failure_control.activate(
                        experiment.failure_type,
                        experiment.id,
                        target_service=experiment.target_service,
                    )
                except FailureControlConflict as exc:
                    raise ExperimentStateError(str(exc)) from exc
                except FailureControlError:
                    raise

            started_at = self._now()
            try:
                running = self._repository.mark_running(experiment.id, started_at)
            except ActiveExperimentExistsError as exc:
                if experiment.mode == "failure":
                    await self._deactivate_after_failed_start(experiment.id, experiment.target_service)
                raise ExperimentStateError("Another experiment is already RUNNING") from exc
            except Exception:
                if experiment.mode == "failure":
                    await self._deactivate_after_failed_start(experiment.id, experiment.target_service)
                raise

            if running is None:
                current = self.get_experiment(experiment_id)
                if current.status == "RUNNING":
                    return current
                if experiment.mode == "failure":
                    await self._deactivate_after_failed_start(experiment.id, experiment.target_service)
                raise ExperimentStateError("Experiment state changed before it could be started")

            self._schedule_expiry(running)
            logger.info(
                "experiment status transition",
                extra={
                    "event": "experiment_status_transition",
                    "experiment_id": str(running.id),
                    "target_service": running.target_service,
                    "failure_type": running.failure_type,
                    "experiment_status": running.status,
                },
            )
            return running

    async def stop_experiment(self, experiment_id: uuid.UUID) -> Experiment:
        async with self._lock:
            experiment = self.get_experiment(experiment_id)
            if experiment.status != "RUNNING":
                raise ExperimentStateError("Only RUNNING experiments can be stopped")

            if experiment.mode == "failure":
                try:
                    await self._failure_control.deactivate(
                        experiment.id, target_service=experiment.target_service
                    )
                except FailureControlError:
                    raise

            stopped = self._repository.mark_stopped(experiment.id, self._now())
            if stopped is None:
                current = self.get_experiment(experiment_id)
                if current.status == "STOPPED":
                    self._cancel_expiry(experiment_id)
                    return current
                if experiment.mode == "failure":
                    try:
                        await self._failure_control.activate(
                        experiment.failure_type,
                        experiment.id,
                        target_service=experiment.target_service,
                    )
                    except FailureControlError:
                        logger.exception(
                            "Failed to restore payment failure after stop persistence failed"
                        )
                raise ExperimentStateError("Experiment state changed before it could be stopped")

            self._cancel_expiry(experiment_id)
            logger.info(
                "experiment status transition",
                extra={
                    "event": "experiment_status_transition",
                    "experiment_id": str(stopped.id),
                    "target_service": stopped.target_service,
                    "failure_type": stopped.failure_type,
                    "experiment_status": stopped.status,
                },
            )
            return stopped

    async def resume_running_experiments(self) -> None:
        for experiment in self._repository.list_running():
            if experiment.mode == "failure":
                try:
                    await self._failure_control.activate(
                        experiment.failure_type,
                        experiment.id,
                        target_service=experiment.target_service,
                    )
                except FailureControlError:
                    logger.exception(
                        "Could not restore failure state for experiment %s", experiment.id
                    )
            self._schedule_expiry(experiment)

    async def shutdown(self) -> None:
        tasks = list(self._expiry_tasks.values())
        self._expiry_tasks.clear()
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _deactivate_after_failed_start(
        self, experiment_id: uuid.UUID, target_service: str
    ) -> None:
        try:
            await self._failure_control.deactivate(
                experiment_id, target_service=target_service
            )
        except FailureControlError:
            logger.exception("Could not roll back payment failure after experiment start failed")

    def _schedule_expiry(self, experiment: Experiment) -> None:
        if experiment.started_at is None:
            return
        old_task = self._expiry_tasks.get(experiment.id)
        if old_task is not None and not old_task.done():
            return
        deadline = experiment.started_at + timedelta(seconds=experiment.duration_seconds)
        remaining = max(0.0, (deadline - self._now()).total_seconds())
        task = asyncio.create_task(self._expire(experiment.id, remaining))
        self._expiry_tasks[experiment.id] = task

    def _cancel_expiry(self, experiment_id: uuid.UUID) -> None:
        task = self._expiry_tasks.pop(experiment_id, None)
        if task is not None and task is not asyncio.current_task():
            task.cancel()

    async def _expire(self, experiment_id: uuid.UUID, delay: float) -> None:
        try:
            await self._sleep(delay)
            await self.stop_experiment(experiment_id)
        except asyncio.CancelledError:
            raise
        except (ExperimentNotFoundError, ExperimentStateError):
            logger.info("Experiment %s was already stopped before duration expiry", experiment_id)
        except Exception:
            logger.exception("Automatic stop failed for experiment %s", experiment_id)
        finally:
            if self._expiry_tasks.get(experiment_id) is asyncio.current_task():
                self._expiry_tasks.pop(experiment_id, None)
