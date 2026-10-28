from uuid import UUID

import httpx
from failurelab_observability import trace_context_headers

from app.core.config import settings


class FailureControlError(Exception):
    pass


class FailureControlConflict(FailureControlError):
    pass


class PaymentFailureControlClient:
    """HTTP adapter for one service's private experiment control API."""

    def __init__(self, service_url: str, timeout_seconds: float = 3.0):
        self._base_url = service_url.rstrip("/")
        self._timeout_seconds = timeout_seconds

    async def activate(self, failure_type: str, experiment_id: UUID) -> None:
        await self._post(
            "/internal/failure-control/activate",
            {"failure_type": failure_type, "experiment_id": str(experiment_id)},
        )

    async def deactivate(self, experiment_id: UUID) -> None:
        await self._post(
            "/internal/failure-control/deactivate",
            {"experiment_id": str(experiment_id)},
        )

    async def _post(self, path: str, payload: dict[str, str]) -> None:
        try:
            async with httpx.AsyncClient(timeout=self._timeout_seconds) as client:
                response = await client.post(
                    f"{self._base_url}{path}",
                    json=payload,
                    headers=trace_context_headers(),
                )
        except httpx.HTTPError as exc:
            raise FailureControlError("Service failure control is unavailable") from exc

        if response.status_code == 409:
            raise FailureControlConflict(response.json().get("detail", "Failure state conflict"))
        if not response.is_success:
            raise FailureControlError(
                f"Service failure control returned HTTP {response.status_code}"
            )


class RoutedFailureControlClient:
    """Sends failure control to the experiment's target service."""

    def __init__(self, service_urls: dict[str, str] | None = None, timeout_seconds: float = 3.0):
        urls = service_urls or {
            "order-service": settings.order_service_url,
            "inventory-service": settings.inventory_service_url,
            "payment-service": settings.payment_service_url,
        }
        self._clients = {
            name: PaymentFailureControlClient(url, timeout_seconds) for name, url in urls.items()
        }

    async def activate(self, failure_type: str, experiment_id: UUID, *, target_service: str) -> None:
        await self._client(target_service).activate(failure_type, experiment_id)

    async def deactivate(self, experiment_id: UUID, *, target_service: str) -> None:
        await self._client(target_service).deactivate(experiment_id)

    def _client(self, target_service: str) -> PaymentFailureControlClient:
        client = self._clients.get(target_service)
        if client is None:
            raise FailureControlError(f"No failure control endpoint is configured for {target_service}")
        return client
