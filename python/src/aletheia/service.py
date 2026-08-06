"""Shared FastAPI application factory.

Every service gets the same operational surface — ``/healthz``, ``/readyz``,
``/metrics`` — plus the two things the gateway relies on: it honours the deadline
header, and it echoes the trace id so service logs join up with gateway logs.
"""

from __future__ import annotations

import json
import logging
import sys
import time
from collections.abc import Awaitable, Callable
from typing import Any

from fastapi import FastAPI, Request, Response

from aletheia.metrics import registry
from aletheia.settings import get_settings

HEADER_DEADLINE = "x-aletheia-deadline-ms"
HEADER_TRACE_ID = "x-aletheia-trace-id"

METRIC_REQUESTS = "aletheia_service_requests_total"
METRIC_LATENCY = "aletheia_service_duration_seconds"


class _JSONFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "level": record.levelname.lower(),
            "msg": record.getMessage(),
            "logger": record.name,
            "time": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
        }
        if trace_id := getattr(record, "trace_id", None):
            payload["trace_id"] = trace_id
        for key, value in getattr(record, "extra_fields", {}).items():
            payload[key] = value
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)


def configure_logging(level: str = "info") -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(_JSONFormatter())
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(getattr(logging, level.upper(), logging.INFO))


def deadline_ms(request: Request) -> int | None:
    """Milliseconds the gateway is still willing to wait, if it said.

    Services should check this before starting expensive work: a rerank with
    40 ms left is wasted compute and a slower failure than necessary.
    """
    raw = request.headers.get(HEADER_DEADLINE)
    if not raw:
        return None
    try:
        return int(raw)
    except ValueError:
        return None


def create_app(
    name: str,
    *,
    ready: Callable[[], bool] | None = None,
    lifespan: Any = None,
) -> FastAPI:
    """Build a service app.

    Args:
        ready: readiness predicate. Returning False makes /readyz 503 without
            affecting /healthz, so a late dependency stops traffic without
            getting the process restart-looped.
        lifespan: async context manager for startup/shutdown work — pools,
            consumers, model loading.
    """
    settings = get_settings()
    configure_logging(settings.log_level)
    log = logging.getLogger(name)

    app = FastAPI(title=f"aletheia-{name}", version="0.1.0", docs_url="/docs", lifespan=lifespan)
    app.state.service_name = name

    @app.middleware("http")
    async def observe(request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
        start = time.perf_counter()
        request.state.trace_id = request.headers.get(HEADER_TRACE_ID, "")
        request.state.deadline_ms = deadline_ms(request)

        response = await call_next(request)

        elapsed = time.perf_counter() - start
        if request.url.path not in ("/healthz", "/metrics"):
            registry.observe(METRIC_LATENCY, elapsed, service=name, path=request.url.path)
            registry.inc(
                METRIC_REQUESTS,
                service=name,
                path=request.url.path,
                status=f"{response.status_code // 100}xx",
            )
            log.info(
                "request",
                extra={
                    "trace_id": request.state.trace_id,
                    "extra_fields": {
                        "path": request.url.path,
                        "status": response.status_code,
                        "duration_ms": round(elapsed * 1000, 1),
                        "deadline_ms": request.state.deadline_ms,
                    },
                },
            )
        if request.state.trace_id:
            response.headers[HEADER_TRACE_ID] = request.state.trace_id
        return response

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        """Liveness. Must not touch dependencies."""
        return {"status": "ok", "service": name}

    @app.get("/readyz")
    async def readyz(response: Response) -> dict[str, str]:
        """Readiness. May touch dependencies; the orchestrator uses it to route."""
        if ready is not None and not ready():
            response.status_code = 503
            return {"status": "not_ready", "service": name}
        return {"status": "ready", "service": name}

    @app.get("/metrics")
    async def metrics() -> Response:
        return Response(
            content=registry.render(),
            media_type="text/plain; version=0.0.4; charset=utf-8",
        )

    return app
