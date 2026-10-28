import uuid

import httpx
from failurelab_observability import trace_context_headers
from fastapi import HTTPException

from app.config import settings
from app.schemas import OrderRequest, OrderResponse


async def create_order(request: OrderRequest, client: httpx.AsyncClient) -> OrderResponse:
    order_id = str(uuid.uuid4())
    try:
        reserve = await client.post(
            f"{settings.inventory_service_url.rstrip('/')}/reserve",
            json={"product_id": request.product_id, "quantity": request.quantity},
            headers=trace_context_headers(),
        )
    except httpx.TimeoutException as exc:
        raise HTTPException(
            status_code=504,
            detail={"code": "inventory_timeout", "message": "Inventory service timed out"},
        ) from exc
    except httpx.RequestError as exc:
        raise HTTPException(
            status_code=503,
            detail={"code": "inventory_unavailable", "message": "Inventory service unavailable"},
        ) from exc
    if not reserve.is_success:
        raise HTTPException(
            status_code=reserve.status_code if reserve.status_code in (409, 422) else 502,
            detail={"code": "inventory_rejected", "message": "Inventory reservation failed"},
        )

    try:
        payment = await client.post(
            f"{settings.payment_service_url.rstrip('/')}/pay",
            json={"order_id": order_id, "amount": 100.0 * request.quantity},
            headers=trace_context_headers(),
        )
    except httpx.TimeoutException as exc:
        raise HTTPException(
            status_code=504,
            detail={"code": "payment_timeout", "message": "Payment service timed out"},
        ) from exc
    except httpx.RequestError as exc:
        raise HTTPException(
            status_code=503,
            detail={"code": "payment_unavailable", "message": "Payment service unavailable"},
        ) from exc
    if not payment.is_success:
        raise HTTPException(
            status_code=502,
            detail={"code": "payment_rejected", "message": "Payment processing failed"},
        )

    return OrderResponse(
        order_id=order_id,
        product_id=request.product_id,
        quantity=request.quantity,
        status="created",
        payment_id=payment.json()["payment_id"],
    )
