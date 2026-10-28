from dataclasses import dataclass


@dataclass(frozen=True)
class IncidentScenario:
    id: str
    name: str
    summary: str
    target_service: str
    failure_type: str
    duration_seconds: int
    expected_behavior: str
    direct_services: tuple[str, ...]
    indirect_services: tuple[str, ...]


SUPPORTED_FAILURES: dict[str, frozenset[str]] = {
    "payment-service": frozenset({"unavailable", "http_500", "latency", "timeout"}),
    "inventory-service": frozenset({"unavailable", "http_500", "latency", "timeout"}),
    "order-service": frozenset({"http_500", "latency", "timeout"}),
}


SCENARIOS: tuple[IncidentScenario, ...] = (
    IncidentScenario(
        id="payment-unavailable",
        name="Payment unavailable",
        summary="Payment health and payment requests return HTTP 503.",
        target_service="payment-service",
        failure_type="unavailable",
        duration_seconds=30,
        expected_behavior=(
            "payment-service /health and /pay return HTTP 503. "
            "An order that reaches payment fails at the order service with HTTP 502."
        ),
        direct_services=("payment-service",),
        indirect_services=("order-service",),
    ),
    IncidentScenario(
        id="payment-http-500",
        name="Payment HTTP 500",
        summary="Payment health and payment requests return HTTP 500.",
        target_service="payment-service",
        failure_type="http_500",
        duration_seconds=30,
        expected_behavior=(
            "payment-service /health and /pay return HTTP 500. "
            "An order that reaches payment fails at the order service with HTTP 502."
        ),
        direct_services=("payment-service",),
        indirect_services=("order-service",),
    ),
    IncidentScenario(
        id="payment-latency",
        name="Payment latency",
        summary="Payment requests are delayed. Health stays immediate.",
        target_service="payment-service",
        failure_type="latency",
        duration_seconds=30,
        expected_behavior=(
            "payment-service /pay waits 1.5 seconds and then succeeds. "
            "/health stays available so the process is not restarted."
        ),
        direct_services=("payment-service",),
        indirect_services=("order-service",),
    ),
    IncidentScenario(
        id="payment-timeout",
        name="Payment timeout",
        summary="Payment requests exceed the order client's timeout.",
        target_service="payment-service",
        failure_type="timeout",
        duration_seconds=30,
        expected_behavior=(
            "payment-service /pay waits 4 seconds. The order client times out after 3 seconds "
            "and returns HTTP 504. /health stays available."
        ),
        direct_services=("payment-service",),
        indirect_services=("order-service",),
    ),
    IncidentScenario(
        id="inventory-unavailable",
        name="Inventory unavailable",
        summary="Inventory health and reservations return HTTP 503.",
        target_service="inventory-service",
        failure_type="unavailable",
        duration_seconds=30,
        expected_behavior=(
            "inventory-service /health and /reserve return HTTP 503. "
            "Order placement fails before payment with HTTP 503."
        ),
        direct_services=("inventory-service",),
        indirect_services=("order-service",),
    ),
    IncidentScenario(
        id="inventory-latency",
        name="Inventory latency",
        summary="Inventory reservations are delayed. Health stays immediate.",
        target_service="inventory-service",
        failure_type="latency",
        duration_seconds=30,
        expected_behavior=(
            "inventory-service /reserve waits 1.5 seconds and then reserves stock. "
            "/health stays available."
        ),
        direct_services=("inventory-service",),
        indirect_services=("order-service",),
    ),
    IncidentScenario(
        id="inventory-http-500",
        name="Inventory HTTP 500",
        summary="Inventory health and reservations return HTTP 500.",
        target_service="inventory-service",
        failure_type="http_500",
        duration_seconds=30,
        expected_behavior=(
            "inventory-service /health and /reserve return HTTP 500. "
            "Order placement fails before payment with HTTP 502."
        ),
        direct_services=("inventory-service",),
        indirect_services=("order-service",),
    ),
    IncidentScenario(
        id="inventory-timeout",
        name="Inventory timeout",
        summary="Inventory reservations exceed the order client's timeout.",
        target_service="inventory-service",
        failure_type="timeout",
        duration_seconds=30,
        expected_behavior=(
            "inventory-service /reserve waits 4 seconds. The order client times out after "
            "3 seconds and returns HTTP 504. /health stays available."
        ),
        direct_services=("inventory-service",),
        indirect_services=("order-service",),
    ),
    IncidentScenario(
        id="order-http-500",
        name="Order HTTP 500",
        summary="The order service itself returns HTTP 500 before calling downstream services.",
        target_service="order-service",
        failure_type="http_500",
        duration_seconds=30,
        expected_behavior=(
            "order-service /health and /orders return HTTP 500. "
            "Inventory and payment are not called."
        ),
        direct_services=("order-service",),
        indirect_services=(),
    ),
    IncidentScenario(
        id="order-latency",
        name="Order latency",
        summary="Order requests are delayed before the real downstream calls.",
        target_service="order-service",
        failure_type="latency",
        duration_seconds=30,
        expected_behavior=(
            "order-service /orders waits 1.5 seconds and then calls inventory and payment. "
            "/health stays available."
        ),
        direct_services=("order-service",),
        indirect_services=(),
    ),
    IncidentScenario(
        id="order-timeout",
        name="Order timeout",
        summary="Order requests exceed a caller's wait before downstream work.",
        target_service="order-service",
        failure_type="timeout",
        duration_seconds=30,
        expected_behavior=(
            "order-service /orders waits 4 seconds and then returns HTTP 504. "
            "Inventory and payment are not called. /health stays available."
        ),
        direct_services=("order-service",),
        indirect_services=(),
    ),
)

SCENARIOS_BY_ID = {scenario.id: scenario for scenario in SCENARIOS}


def failure_supported(target_service: str, failure_type: str) -> bool:
    return failure_type in SUPPORTED_FAILURES.get(target_service, frozenset())
