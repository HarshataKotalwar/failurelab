from failurelab_failure import (
    FailureController,
    apply_request_failure,
    build_failure_router,
    health_status_code,
)
from failurelab_observability import configure_observability
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

app = FastAPI(title="FailureLab Inventory Service", version="0.1.0")
controller = FailureController()
app.include_router(
    build_failure_router(
        controller,
        frozenset({"unavailable", "http_500", "latency", "timeout"}),
    )
)
inventory = {"product-1": 100}


class ReservationRequest(BaseModel):
    product_id: str = Field(min_length=1)
    quantity: int = Field(gt=0)


class ReservationResponse(BaseModel):
    product_id: str
    quantity: int
    status: str = "reserved"


class HealthResponse(BaseModel):
    status: str
    service: str


@app.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    status_code = health_status_code(controller.current_state)
    if status_code is not None:
        raise HTTPException(
            status_code=status_code,
            detail={"code": "injected_failure", "message": "Inventory health reflects the active failure"},
        )
    return HealthResponse(status="ok", service="inventory-service")


@app.post("/reserve", response_model=ReservationResponse)
async def reserve(request: ReservationRequest) -> ReservationResponse:
    await apply_request_failure(controller.current_state)
    available = inventory.get(request.product_id, 0)
    if available < request.quantity:
        raise HTTPException(
            status_code=409,
            detail={"code": "insufficient_inventory", "message": "Insufficient stock available"},
        )
    inventory[request.product_id] = available - request.quantity
    return ReservationResponse(product_id=request.product_id, quantity=request.quantity)


configure_observability(app, "failurelab-inventory-service")
