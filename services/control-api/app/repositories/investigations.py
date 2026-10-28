from __future__ import annotations

import uuid
from collections.abc import Callable
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.entities import InvestigationRecord
from app.schemas.investigation import GroundTruth, InvestigationResult


class InvestigationRepository:
    def __init__(self, session_factory: Callable[[], Session]) -> None:
        self._session_factory = session_factory

    def save(self, result: InvestigationResult, ground_truth: GroundTruth) -> None:
        with self._session_factory() as session:
            session.add(
                InvestigationRecord(
                    id=result.investigation_id,
                    experiment_id=result.experiment_id,
                    result=result.model_dump(mode="json"),
                    ground_truth=ground_truth.model_dump(mode="json"),
                )
            )
            session.commit()

    def get(self, investigation_id: uuid.UUID) -> tuple[InvestigationResult, GroundTruth] | None:
        with self._session_factory() as session:
            record = session.get(InvestigationRecord, investigation_id)
            if record is None:
                return None
            return _decode(record.result, record.ground_truth)

    def list_results(
        self, experiment_id: uuid.UUID | None = None, limit: int = 20
    ) -> list[InvestigationResult]:
        with self._session_factory() as session:
            statement = select(InvestigationRecord).order_by(
                InvestigationRecord.created_at.desc(), InvestigationRecord.id.desc()
            )
            if experiment_id is not None:
                statement = statement.where(InvestigationRecord.experiment_id == experiment_id)
            records = session.scalars(statement.limit(limit)).all()
            return [InvestigationResult.model_validate(record.result) for record in records]

    def latest_for_experiment(
        self, experiment_id: uuid.UUID
    ) -> tuple[InvestigationResult, GroundTruth] | None:
        with self._session_factory() as session:
            record = session.scalar(
                select(InvestigationRecord)
                .where(InvestigationRecord.experiment_id == experiment_id)
                .order_by(InvestigationRecord.created_at.desc(), InvestigationRecord.id.desc())
                .limit(1)
            )
            if record is None:
                return None
            return _decode(record.result, record.ground_truth)


def _decode(result: dict[str, Any], truth: dict[str, Any]):
    return InvestigationResult.model_validate(result), GroundTruth.model_validate(truth)


class VerificationRepository:
    def __init__(self, session_factory: Callable[[], Session]) -> None:
        self._session_factory = session_factory

    def save(self, result) -> None:
        from app.models.entities import VerificationRecord

        with self._session_factory() as session:
            session.add(
                VerificationRecord(
                    baseline_investigation_id=result.baseline_investigation_id,
                    verification_investigation_id=result.verification_investigation_id,
                    result=result.model_dump(mode="json"),
                )
            )
            session.commit()
