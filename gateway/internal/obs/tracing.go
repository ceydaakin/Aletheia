package obs

import (
	"context"
	"fmt"
	"net/http"
	"strings"
	"time"

	"go.opentelemetry.io/otel"
	"go.opentelemetry.io/otel/attribute"
	"go.opentelemetry.io/otel/exporters/otlp/otlptrace/otlptracehttp"
	"go.opentelemetry.io/otel/propagation"
	"go.opentelemetry.io/otel/sdk/resource"
	sdktrace "go.opentelemetry.io/otel/sdk/trace"
	semconv "go.opentelemetry.io/otel/semconv/v1.43.0"
	"go.opentelemetry.io/otel/trace"
)

// ADR-0001 deferred this dependency until there was something to trace to. That
// is now: PRD §4.2 asks for a trace spanning retrieval → generation →
// verification → decision, which is precisely the sequence that has to be
// explicable when a user asks why they got an abstention.

const tracerName = "github.com/ceydaakin/aletheia/gateway"

// Attribute keys. Decision and abstain reason are on the span because "why did
// this abstain" is the question traces get opened for, and having to join
// against logs to answer it defeats the point.
const (
	AttrTenant        = attribute.Key("aletheia.tenant_id")
	AttrDecision      = attribute.Key("aletheia.decision")
	AttrAbstainReason = attribute.Key("aletheia.abstain_reason")
	AttrRiskBudget    = attribute.Key("aletheia.risk_budget")
	AttrStatistic     = attribute.Key("aletheia.risk_statistic")
	AttrThreshold     = attribute.Key("aletheia.risk_threshold")
	AttrCalibrationID = attribute.Key("aletheia.calibration_id")
	AttrDegraded      = attribute.Key("aletheia.degraded")
	AttrChunks        = attribute.Key("aletheia.retrieved_chunks")
	AttrClaims        = attribute.Key("aletheia.claims")
)

// InitTracing configures the global tracer provider.
//
// With no endpoint configured it installs the propagator and nothing else, so
// traceparent still flows to the Python services and joins whatever they export.
// Tracing is observability, not correctness: a collector being down must never
// affect a response, which is why every failure here degrades to no-op rather
// than returning an error that would stop startup.
func InitTracing(
	ctx context.Context, endpoint, serviceName, version string, sampleRatio float64,
) (func(context.Context) error, error) {
	otel.SetTextMapPropagator(propagation.NewCompositeTextMapPropagator(
		propagation.TraceContext{}, propagation.Baggage{},
	))

	if endpoint == "" {
		return func(context.Context) error { return nil }, nil
	}

	// The signal path has to be spelled out. WithEndpointURL uses the URL exactly
	// as given, so a bare "http://collector:4318" POSTs to "/" and every export
	// 404s — silently, because export failures are logged by the SDK and never
	// surface in a response. OTEL_EXPORTER_OTLP_ENDPOINT is defined as a base
	// URL, so appending the standard path is the correct reading of it.
	if !strings.HasSuffix(endpoint, "/v1/traces") {
		endpoint = strings.TrimRight(endpoint, "/") + "/v1/traces"
	}
	exporter, err := otlptracehttp.New(ctx,
		otlptracehttp.WithEndpointURL(endpoint),
		otlptracehttp.WithTimeout(5*time.Second),
	)
	if err != nil {
		return nil, fmt.Errorf("otlp exporter: %w", err)
	}

	res, err := resource.Merge(resource.Default(), resource.NewWithAttributes(
		semconv.SchemaURL,
		semconv.ServiceName(serviceName),
		semconv.ServiceVersion(version),
	))
	if err != nil {
		return nil, fmt.Errorf("otel resource: %w", err)
	}

	provider := sdktrace.NewTracerProvider(
		sdktrace.WithBatcher(exporter),
		sdktrace.WithResource(res),
		// ParentBased so a sampled decision made upstream is honoured: sampling
		// each service independently would produce traces with holes in them,
		// which is worse than not sampling at all.
		sdktrace.WithSampler(sdktrace.ParentBased(sdktrace.TraceIDRatioBased(sampleRatio))),
	)
	otel.SetTracerProvider(provider)

	return provider.Shutdown, nil
}

func Tracer() trace.Tracer { return otel.Tracer(tracerName) }

// StartSpan begins a child span on ctx.
func StartSpan(ctx context.Context, name string, attrs ...attribute.KeyValue) (context.Context, trace.Span) {
	return Tracer().Start(ctx, name, trace.WithAttributes(attrs...))
}

// Inject writes traceparent onto an outgoing request so the Python services
// continue this trace rather than starting their own.
func Inject(ctx context.Context, r *http.Request) {
	otel.GetTextMapPropagator().Inject(ctx, propagation.HeaderCarrier(r.Header))
}

// Extract adopts an inbound trace context, for the case where the gateway sits
// behind something that already started one.
func Extract(ctx context.Context, r *http.Request) context.Context {
	return otel.GetTextMapPropagator().Extract(ctx, propagation.HeaderCarrier(r.Header))
}

// TraceIDFromContext returns the active OTel trace id, or "" when untraced.
//
// Used to make the response's trace_id the *same* identifier the collector
// knows. A user reporting a bad answer quotes that field, and it has to find
// the trace.
func TraceIDFromContext(ctx context.Context) string {
	if sc := trace.SpanContextFromContext(ctx); sc.IsValid() && sc.HasTraceID() {
		return sc.TraceID().String()
	}
	return ""
}
