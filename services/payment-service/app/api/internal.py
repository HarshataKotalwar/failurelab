from failurelab_failure import (
    FailureController,
    build_failure_router,
)

controller = FailureController()
router = build_failure_router(
    controller,
    frozenset({"unavailable", "http_500", "latency", "timeout"}),
)
