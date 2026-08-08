"""OpenTelemetry setup for the model services.

The gateway starts the trace and sends W3C ``traceparent`` on every upstream
call; these services continue it. Without that continuation the pipeline would
appear in the collector as five unrelated fragments, and the question traces get
opened for — "which stage turned this into an abstention" — would be unanswerable.

Tracing is observability, not correctness. Every failure path here degrades to a
no-op: a collector being unreachable must never change a response, and a service
that refuses to start because its telemetry backend is down has turned a
monitoring outage into a customer-facing one.
"""

from __future__ import annotations

import logging
from typing import Any

log = logging.getLogger("tracing")

_TRACER: Any = None


def configure(service_name: str, endpoint: str, *, sample_ratio: float = 1.0) -> None:
    """Install the tracer provider and the W3C propagator.

    With no endpoint the propagator is still installed, so trace context flows
    through to anything downstream even when this service exports nothing.
    """
    global _TRACER
    try:
        from opentelemetry import trace
        from opentelemetry.propagate import set_global_textmap
        from opentelemetry.trace.propagation.tracecontext import (
            TraceContextTextMapPropagator,
        )

        set_global_textmap(TraceContextTextMapPropagator())

        if not endpoint:
            _TRACER = trace.get_tracer(service_name)
            return

        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
        from opentelemetry.sdk.resources import SERVICE_NAME, SERVICE_VERSION, Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
        from opentelemetry.sdk.trace.sampling import ParentBased, TraceIdRatioBased

        provider = TracerProvider(
            resource=Resource.create(
                {SERVICE_NAME: service_name, SERVICE_VERSION: "0.1.0"}
            ),
            # ParentBased: honour the gateway's sampling decision. Sampling each
            # service independently produces traces with holes, which is worse
            # than not sampling.
            sampler=ParentBased(TraceIdRatioBased(sample_ratio)),
        )
        provider.add_span_processor(
            BatchSpanProcessor(OTLPSpanExporter(endpoint=f"{endpoint}/v1/traces"))
        )
        trace.set_tracer_provider(provider)
        _TRACER = trace.get_tracer(service_name)
        log.info(
            "tracing enabled",
            extra={"extra_fields": {"endpoint": endpoint, "service": service_name}},
        )
    except ImportError:
        # The OTel packages are an extra. Running without them is a supported
        # configuration, not a misconfiguration.
        log.info("opentelemetry not installed; running untraced")
    except Exception:
        log.warning("could not configure tracing; running untraced", exc_info=True)


def context_from_headers(headers: Any):
    """Extract the incoming trace context, or None when tracing is unavailable."""
    try:
        from opentelemetry.propagate import extract

        return extract(dict(headers))
    except Exception:
        return None


class _NullSpan:
    def set_attribute(self, *_: Any) -> None: ...
    def record_exception(self, *_: Any) -> None: ...
    def __enter__(self):
        return self

    def __exit__(self, *_: Any) -> None: ...


def span(name: str, context: Any = None):
    """Start a span, or a no-op when tracing is not configured.

    Returning a null object rather than None keeps every call site free of
    ``if tracing_enabled`` branches — telemetry that complicates the code it
    observes tends to get removed.
    """
    if _TRACER is None:
        return _NullSpan()
    try:
        return _TRACER.start_as_current_span(name, context=context)
    except Exception:
        return _NullSpan()
