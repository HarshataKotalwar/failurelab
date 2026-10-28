from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import datetime

from sqlalchemy import exists, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.entities import Experiment, Incident
from app.schemas.api import ExperimentCreate


class ActiveExperimentExistsError(Exception):
    pass


class ExperimentRepository:
    def __init__(self, session_factory: Callable[[], Session]) -> None:
        self._session_factory = session_factory

    def create(self, values: ExperimentCreate) -> Experiment:
        with self._session_factory() as session:
            experiment = Experiment(
                target_service=values.target_service or "payment-service",
                failure_type=values.failure_type or "none",
                mode=values.mode,
                duration_seconds=values.duration_seconds or 0,
                scenario=values.scenario_id,
                status="CREATED",
            )
            session.add(experiment)
            session.commit()
            session.refresh(experiment)
            return experiment

    def list(self) -> list[Experiment]:
        with self._session_factory() as session:
            return list(
                session.scalars(select(Experiment).order_by(Experiment.created_at.desc())).all()
            )

    def get(self, experiment_id: uuid.UUID) -> Experiment | None:
        with self._session_factory() as session:
            return session.get(Experiment, experiment_id)

    def has_running(self) -> bool:
        with self._session_factory() as session:
            return bool(session.scalar(select(exists().where(Experiment.status == "RUNNING"))))

    def list_running(self) -> list[Experiment]:
        with self._session_factory() as session:
            return list(
                session.scalars(select(Experiment).where(Experiment.status == "RUNNING")).all()
            )

    def mark_running(self, experiment_id: uuid.UUID, started_at: datetime) -> Experiment | None:
        with self._session_factory() as session:
            statement = (
                update(Experiment)
                .where(Experiment.id == experiment_id, Experiment.status == "CREATED")
                .values(status="RUNNING", started_at=started_at)
            )
            try:
                result = session.execute(statement)
                if result.rowcount != 1:
                    session.rollback()
                    return None
                experiment = session.get(Experiment, experiment_id)
                if experiment is not None and experiment.mode == "failure":
                    session.add(
                        Incident(
                            experiment_id=experiment.id,
                            severity=(
                                "medium" if experiment.failure_type == "latency" else "high"
                            ),
                            status="open",
                            started_at=started_at,
                        )
                    )
                session.commit()
            except IntegrityError as exc:
                session.rollback()
                raise ActiveExperimentExistsError from exc
            return session.get(Experiment, experiment_id)

    def mark_stopped(self, experiment_id: uuid.UUID, ended_at: datetime) -> Experiment | None:
        with self._session_factory() as session:
            result = session.execute(
                update(Experiment)
                .where(Experiment.id == experiment_id, Experiment.status == "RUNNING")
                .values(status="STOPPED", ended_at=ended_at)
            )
            if result.rowcount != 1:
                session.rollback()
                return None
            incident = session.scalar(
                select(Incident)
                .where(Incident.experiment_id == experiment_id, Incident.status == "open")
                .order_by(Incident.created_at.desc())
                .limit(1)
            )
            if incident is not None:
                incident.status = "resolved"
                incident.ended_at = ended_at
            session.commit()
            return session.get(Experiment, experiment_id)
