import httpx
from failurelab_failure import (
    FailureController,
    apply_request_failure,
    build_failure_router,
    health_status_code,
)
from failurelab_observability import configure_observability
from fastapi import Depends, FastAPI, HTTPException

from app.dependencies import get_http_client
from app.schemas import HealthResponse, OrderRequest, OrderResponse
from app.service import create_order

app = FastAPI(title="FailureLab Order Service", version="0.1.0")
controller = FailureController()
app.include_router(
    build_failure_router(controller, frozenset({"http_500", "latency", "timeout"}))
)


@app.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    status_code = health_status_code(controller.current_state)
    if status_code is not None:
        raise HTTPException(
            status_code=status_code,
            detail={"code": "injected_failure", "message": "Order health reflects the active failure"},
        )
    return HealthResponse(status="ok", service="order-service")


@app.post("/orders", response_model=OrderResponse, status_code=201)
async def place_order(
    request: OrderRequest,
    client: httpx.AsyncClient = Depends(get_http_client),  # noqa: B008
) -> OrderResponse:
    try:
        await apply_request_failure(controller.current_state)
        return await create_order(request, client)
    finally:
        await client.aclose()


configure_observability(app, "failurelab-order-service")
