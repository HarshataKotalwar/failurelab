from collections.abc import Callable

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.entities import Incident


class IncidentRepository:
    def __init__(self, session_factory: Callable[[], Session]) -> None:
        self._session_factory = session_factory

    def list(self, limit: int = 100) -> list[Incident]:
        with self._session_factory() as session:
            return list(
                session.scalars(
                    select(Incident)
                    .order_by(Incident.created_at.desc(), Incident.id.desc())
                    .limit(limit)
                ).all()
            )
