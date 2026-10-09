import httpx
from fastapi import Depends, FastAPI

from app.dependencies import get_http_client
from app.schemas import HealthResponse, OrderRequest, OrderResponse
from app.service import create_order

app = FastAPI(title="FailureLab Order Service", version="0.1.0")


@app.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    return HealthResponse(status="ok", service="order-service")


@app.post("/orders", response_model=OrderResponse, status_code=201)
async def place_order(
    request: OrderRequest,
    client: httpx.AsyncClient = Depends(get_http_client),  # noqa: B008
) -> OrderResponse:
    try:
        return await create_order(request, client)
    finally:
        await client.aclose()
