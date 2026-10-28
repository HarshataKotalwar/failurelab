from app.core.config import settings
from app.core.database import SessionLocal
from app.repositories.experiments import ExperimentRepository
from app.services.experiments import ExperimentService
from app.services.failure_control import RoutedFailureControlClient


def create_experiment_service() -> ExperimentService:
    repository = ExperimentRepository(SessionLocal)
    failure_control = RoutedFailureControlClient(
        {
            "order-service": settings.order_service_url,
            "inventory-service": settings.inventory_service_url,
            "payment-service": settings.payment_service_url,
        }
    )
    return ExperimentService(repository, failure_control)
