import asyncio
import os
from typing import Literal

import httpx
from pydantic import BaseModel, Field

from app.schemas.investigation import NormalizedLog
from app.services.registry import configured_services


class LogCollectionResult(BaseModel):
    records: list[NormalizedLog]
    provider_status: dict[str, Literal["available", "unavailable"]]
    failed_providers: list[str] = Field(default_factory=list)

    @property
    def status(self) -> Literal["available", "partial", "unavailable"]:
        if self.failed_providers:
            return (
                "partial"
                if any(status == "available" for status in self.provider_status.values())
                else "unavailable"
            )
        return "available"


class LogProvider:
    """Retrieves bounded structured request records from each app's ring buffer."""

    def __init__(
        self,
        control_url: str | None = None,
        limit: int = 100,
        *,
        service_urls: dict[str, str] | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._control_url = control_url or os.getenv("CONTROL_API_URL", "http://localhost:8000")
        self._limit = max(1, min(limit, 500))
        self._service_urls = service_urls
        self._transport = transport

    async def collect(self) -> LogCollectionResult:
        endpoints = {
            "control-api": self._control_url,
            **(
                self._service_urls
                if self._service_urls is not None
                else {service.name: service.url for service in configured_services()}
            ),
        }

        async def fetch(name: str, base: str) -> tuple[str, list[dict] | None]:
            try:
                async with httpx.AsyncClient(timeout=2, transport=self._transport) as client:
                    response = await client.get(
                        f"{base.rstrip('/')}/internal/observability/logs",
                        params={"limit": self._limit},
                    )
                response.raise_for_status()
                payload = response.json()
                if not isinstance(payload, list):
                    return name, None
                return name, payload
            except (httpx.HTTPError, ValueError):
                return name, None

        results = await asyncio.gather(*(fetch(name, url) for name, url in endpoints.items()))
        provider_batches = dict(results)
        failed = [name for name, batch in results if batch is None]
        records: list[NormalizedLog] = []
        for batch in provider_batches.values():
            for record in batch or []:
                try:
                    records.append(NormalizedLog.model_validate(record))
                except ValueError:
                    continue
        records.sort(key=lambda record: record.timestamp)
        return LogCollectionResult(
            records=records[-self._limit :],
            provider_status={
                name: "unavailable" if batch is None else "available"
                for name, batch in provider_batches.items()
            },
            failed_providers=failed,
        )
