from pydantic import BaseModel, Field


class OrderRequest(BaseModel):
    product_id: str = Field(min_length=1)
    quantity: int = Field(gt=0)


class OrderResponse(BaseModel):
    order_id: str
    product_id: str
    quantity: int
    status: str
    payment_id: str


class HealthResponse(BaseModel):
    status: str
    service: str
