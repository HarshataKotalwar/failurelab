from app.core.config import settings
from app.schemas.api import ServiceResponse

APPLICATION_SERVICES = (
    ("order-service", settings.order_service_url),
    ("inventory-service", settings.inventory_service_url),
    ("payment-service", settings.payment_service_url),
)


def configured_services() -> list[ServiceResponse]:
    return [
        ServiceResponse(id=name, name=name, url=url, status="configured")
        for name, url in APPLICATION_SERVICES
    ]
