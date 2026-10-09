from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

app = FastAPI(title="FailureLab Inventory Service", version="0.1.0")
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
    return HealthResponse(status="ok", service="inventory-service")


@app.post("/reserve", response_model=ReservationResponse)
def reserve(request: ReservationRequest) -> ReservationResponse:
    available = inventory.get(request.product_id, 0)
    if available < request.quantity:
        raise HTTPException(
            status_code=409,
            detail={"code": "insufficient_inventory", "message": "Insufficient stock available"},
        )
    inventory[request.product_id] = available - request.quantity
    return ReservationResponse(product_id=request.product_id, quantity=request.quantity)
