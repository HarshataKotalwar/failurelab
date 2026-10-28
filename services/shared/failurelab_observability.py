from __future__ import annotations

import json
import logging
import os
import sys
import time
from collections import deque
from datetime import UTC, datetime
from typing import Any

from fastapi import FastAPI
from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
from opentelemetry.propagate import inject
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from prometheus_client import (
    CONTENT_TYPE_LATEST,
    CollectorRegistry,
    Counter,
    Histogram,
    generate_latest,
)
from starlette.responses import Response


def trace_context_headers() -> dict[str, str]:
    carrier: dict[str, str] = {}
    inject(carrier)
    return carrier


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname.lower(),
            "logger": record.name,
            "message": record.getMessage(),
        }

        for field in (
            "service",
            "event",
            "method",
            "route",
            "status_code",
            "duration_ms",
            "trace_id",
            "experiment_id",
            "target_service",
            "failure_type",
            "experiment_status",
        ):
            value = getattr(record, field, None)
            if value is not None:
                payload[field] = value

        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)

        return json.dumps(payload, separators=(",", ":"))


class RequestObservabilityMiddleware:
    def __init__(
        self,
        app: Any,
        *,
        service_name: str,
        registry: CollectorRegistry,
        records: deque[dict[str, Any]] | None = None,
    ) -> None:
        self.app = app
        self.service_name = service_name
        self.logger = logging.getLogger(service_name)
        self.records = records if records is not None else deque(maxlen=500)

        self.requests = Counter(
            "failurelab_http_requests_total",
            "HTTP requests handled by FailureLab services",
            ("service", "method", "route", "status_code"),
            registry=registry,
        )

        self.errors = Counter(
            "failurelab_http_errors_total",
            "HTTP responses with an error status",
            ("service", "method", "route", "status_code"),
            registry=registry,
        )

        self.duration = Histogram(
            "failurelab_http_request_duration_seconds",
            "HTTP request duration in seconds",
            ("service", "method", "route"),
            registry=registry,
        )

    async def __call__(
        self,
        scope: dict[str, Any],
        receive: Any,
        send: Any,
    ) -> None:
        if scope["type"] != "http" or scope.get("path") == "/metrics":
            await self.app(scope, receive, send)
            return

        method = scope.get("method", "UNKNOWN")
        path = scope.get("path", "")
        start = time.perf_counter()
        status_code = 500
        trace_id = ""

        self.logger.info(
            "request received",
            extra={
                "service": self.service_name,
                "event": "request_received",
                "method": method,
                "route": path,
                "trace_id": self._trace_id(),
            },
        )

        async def send_with_trace(message: dict[str, Any]) -> None:
            nonlocal status_code, trace_id

            if message["type"] == "http.response.start":
                status_code = message["status"]
                trace_id = self._trace_id()

                headers = list(message.get("headers", []))

                if trace_id:
                    headers.append(
                        (b"x-trace-id", trace_id.encode("ascii"))
                    )

                message["headers"] = headers

            await send(message)

        try:
            await self.app(scope, receive, send_with_trace)

        except Exception:
            self.logger.exception(
                "request failed",
                extra={
                    "service": self.service_name,
                    "event": "request_failed",
                    "method": method,
                    "route": self._route(scope, path),
                    "status_code": 500,
                    "trace_id": self._trace_id(),
                },
            )
            raise

        finally:
            duration = time.perf_counter() - start
            route = self._route(scope, path)
            final_trace_id = trace_id or self._trace_id()

            labels = (
                self.service_name,
                method,
                route,
                str(status_code),
            )

            self.requests.labels(*labels).inc()

            if status_code >= 400:
                self.errors.labels(*labels).inc()

            self.duration.labels(
                self.service_name,
                method,
                route,
            ).observe(duration)

            self.logger.info(
                "request completed",
                extra={
                    "service": self.service_name,
                    "event": "request_completed",
                    "method": method,
                    "route": route,
                    "status_code": status_code,
                    "duration_ms": round(duration * 1000, 3),
                    "trace_id": final_trace_id,
                },
            )

            self.records.append(
                {
                    "timestamp": datetime.now(UTC)
                    .isoformat()
                    .replace("+00:00", "Z"),
                    "service": self.service_name.removeprefix(
                        "failurelab-"
                    ),
                    "level": "info",
                    "message": "request completed",
                    "route": route,
                    "status_code": status_code,
                    "duration_ms": round(duration * 1000, 3),
                    "trace_id": final_trace_id,
                }
            )

    @staticmethod
    def _route(
        scope: dict[str, Any],
        fallback: str,
    ) -> str:
        route = scope.get("route")
        template = getattr(route, "path", None)

        return (
            template
            if isinstance(template, str)
            else "unmatched"
        )

    @staticmethod
    def _trace_id() -> str:
        span_context = (
            trace.get_current_span()
            .get_span_context()
        )

        if not span_context.is_valid:
            return ""

        return f"{span_context.trace_id:032x}"


def configure_observability(
    app: FastAPI,
    service_name: str,
) -> None:
    root_logger = logging.getLogger()

    if not any(
        getattr(
            handler,
            "_failurelab_json",
            False,
        )
        for handler in root_logger.handlers
    ):
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(JsonFormatter())
        handler._failurelab_json = True  # type: ignore[attr-defined]
        root_logger.addHandler(handler)

    root_logger.setLevel(logging.INFO)

    registry = CollectorRegistry()
    records: deque[dict[str, Any]] = deque(maxlen=500)

    app.add_middleware(
        RequestObservabilityMiddleware,
        service_name=service_name,
        registry=registry,
        records=records,
    )

    async def recent_logs(
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        return list(records)[
            -max(1, min(limit, 500)) :
        ]

    app.add_api_route(
        "/internal/observability/logs",
        recent_logs,
        methods=["GET"],
        include_in_schema=False,
    )

    def metrics_endpoint() -> Response:
        return Response(
            generate_latest(registry),
            media_type=CONTENT_TYPE_LATEST,
        )

    app.add_api_route(
        "/metrics",
        metrics_endpoint,
        include_in_schema=False,
    )

    provider = TracerProvider(
        resource=Resource.create(
            {"service.name": service_name}
        )
    )

    endpoint = os.getenv(
        "OTEL_EXPORTER_OTLP_ENDPOINT"
    )

    if endpoint:
        provider.add_span_processor(
            BatchSpanProcessor(
                OTLPSpanExporter(
                    endpoint=endpoint,
                    insecure=True,
                ),
                schedule_delay_millis=200,
            )
        )

    trace.set_tracer_provider(provider)

    FastAPIInstrumentor.instrument_app(
        app,
        tracer_provider=provider,
    )

    HTTPXClientInstrumentor().instrument(
        tracer_provider=provider,
    )