import base64
import os
from datetime import UTC, datetime
from typing import Literal

import httpx
from pydantic import BaseModel, Field

from app.schemas.investigation import NormalizedSpan


class TraceLookupResult(BaseModel):
    status: Literal["found", "not_found", "unavailable"]
    spans: list[NormalizedSpan] = Field(default_factory=list)
    message: str | None = None


class TraceClient:
    """Queries Tempo and converts OTLP JSON into the application's span model."""

    def __init__(
        self,
        tempo_url: str | None = None,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._url = (tempo_url or os.getenv("TEMPO_URL", "http://localhost:3200")).rstrip("/")
        self._transport = transport

    async def get_trace(self, trace_id: str) -> TraceLookupResult:
        try:
            async with httpx.AsyncClient(timeout=3, transport=self._transport) as client:
                response = await client.get(f"{self._url}/api/traces/{trace_id}")
            if response.status_code == 404:
                return TraceLookupResult(status="not_found", message="Trace ID was not found")
            response.raise_for_status()
            spans = normalize_tempo_trace(response.json(), trace_id)
            if not spans:
                return TraceLookupResult(status="not_found", message="Trace contained no spans")
            return TraceLookupResult(status="found", spans=spans)
        except (httpx.HTTPError, KeyError, TypeError, ValueError) as exc:
            return TraceLookupResult(status="unavailable", message=f"Tempo query failed: {exc}")


def normalize_tempo_trace(payload: dict, requested_trace_id: str) -> list[NormalizedSpan]:
    spans: list[NormalizedSpan] = []
    batches = payload.get("batches", payload.get("resourceSpans", []))
    for resource in batches:
        attributes = _attributes(resource.get("resource", {}).get("attributes", []))
        service = str(attributes.get("service.name", "unknown"))
        for scope in resource.get("scopeSpans", []):
            for span in scope.get("spans", []):
                start = _time(span["startTimeUnixNano"])
                end = _time(span["endTimeUnixNano"])
                span_attrs = _attributes(span.get("attributes", []))
                spans.append(
                    NormalizedSpan(
                        trace_id=_hex_id(span.get("traceId"), requested_trace_id),
                        span_id=_hex_id(span["spanId"]),
                        parent_span_id=(
                            _hex_id(span["parentSpanId"]) if span.get("parentSpanId") else None
                        ),
                        service_name=service,
                        operation_name=span.get("name", "unknown"),
                        start_time=start,
                        end_time=end,
                        duration_ms=(end - start).total_seconds() * 1000,
                        status=str(span.get("status", {}).get("code", "UNSET")),
                        attributes={
                            k: v
                            for k, v in span_attrs.items()
                            if isinstance(v, (str, int, float, bool))
                        },
                    )
                )
    return spans


def _hex_id(value: str, fallback: str | None = None) -> str:
    if value and all(character in "0123456789abcdefABCDEF" for character in value):
        if len(value) in {16, 32}:
            return value.lower()
    try:
        decoded = base64.b64decode(value, validate=True).hex()
        return decoded or (fallback or value)
    except (ValueError, TypeError):
        return value or fallback or ""


def _attributes(items: list[dict]) -> dict:
    result = {}
    for item in items:
        value = item.get("value", {})
        if not value:
            continue
        result[item.get("key", "")] = next(iter(value.values()))
    return result


def _time(value: str | int) -> datetime:
    return datetime.fromtimestamp(int(value) / 1_000_000_000, UTC)
