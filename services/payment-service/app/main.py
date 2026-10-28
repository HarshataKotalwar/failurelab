import uuid

from failurelab_observability import configure_observability
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from failurelab_failure import apply_request_failure, health_status_code

from app.api.internal import controller
from app.api.internal import router as internal_router

app = FastAPI(title="FailureLab Payment Service", version="0.1.0")
app.include_router(internal_router)


class PaymentRequest(BaseModel):
    order_id: str = Field(min_length=1)
    amount: float = Field(gt=0)


class PaymentResponse(BaseModel):
    payment_id: str
    order_id: str
    amount: float
    status: str = "paid"


class HealthResponse(BaseModel):
    status: str
    service: str


@app.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    status_code = health_status_code(controller.current_state)
    if status_code is not None:
        raise HTTPException(
            status_code=status_code,
            detail={"code": "injected_failure", "message": "Payment health reflects the active failure"},
        )
    return HealthResponse(status="ok", service="payment-service")


@app.post("/pay", response_model=PaymentResponse)
async def pay(request: PaymentRequest) -> PaymentResponse:
    await apply_request_failure(controller.current_state)
    return PaymentResponse(
        payment_id=str(uuid.uuid4()), order_id=request.order_id, amount=request.amount
    )


configure_observability(app, "failurelab-payment-service")
