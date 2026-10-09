import uuid

from fastapi import FastAPI
from pydantic import BaseModel, Field

app = FastAPI(title="FailureLab Payment Service", version="0.1.0")


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
    return HealthResponse(status="ok", service="payment-service")


@app.post("/pay", response_model=PaymentResponse)
def pay(request: PaymentRequest) -> PaymentResponse:
    return PaymentResponse(
        payment_id=str(uuid.uuid4()), order_id=request.order_id, amount=request.amount
    )
