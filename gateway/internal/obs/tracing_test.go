package obs

import (
	"context"
	"net/http"
	"net/http/httptest"
	"testing"
)

// InitTracing degrades to a no-op on any failure, which is right for
// availability and dangerous for confidence: a schema mismatch between
// resource.Default() and the semconv import disabled tracing entirely with
// nothing but a WARN in the log, and it took reading container logs to notice.
// These tests assert that the configured path actually configures.

func TestInitTracingWithAnEndpointSucceeds(t *testing.T) {
	collector := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusOK)
	}))
	defer collector.Close()

	shutdown, err := InitTracing(context.Background(), collector.URL, "test-service", "0.0.1", 1.0)
	if err != nil {
		t.Fatalf("InitTracing: %v", err)
	}
	t.Cleanup(func() { _ = shutdown(context.Background()) })

	// A configured provider must actually produce recording spans; the no-op
	// provider silently does not, which is exactly how this failed before.
	ctx, span := StartSpan(context.Background(), "probe")
	defer span.End()

	if !span.SpanContext().IsValid() {
		t.Fatal("span context is invalid: the tracer provider was not installed")
	}
	if !span.IsRecording() {
		t.Error("span is not recording")
	}
	if TraceIDFromContext(ctx) == "" {
		t.Error("no trace id on the context")
	}
}

func TestInitTracingWithoutAnEndpointIsQuiet(t *testing.T) {
	shutdown, err := InitTracing(context.Background(), "", "test-service", "0.0.1", 1.0)
	if err != nil {
		t.Fatalf("InitTracing with no endpoint returned an error: %v", err)
	}
	if err := shutdown(context.Background()); err != nil {
		t.Errorf("shutdown: %v", err)
	}
}

func TestPropagationRoundTrips(t *testing.T) {
	// Installs the propagator.
	if _, err := InitTracing(context.Background(), "", "test-service", "0.0.1", 1.0); err != nil {
		t.Fatal(err)
	}

	ctx, span := StartSpan(context.Background(), "outgoing")
	defer span.End()

	req := httptest.NewRequest(http.MethodPost, "/v1/answer", nil)
	Inject(ctx, req)

	// Without an exporter the span is not sampled, so traceparent may be absent —
	// what must hold is that whatever is injected is what comes back out.
	extracted := Extract(context.Background(), req)
	if got := req.Header.Get("traceparent"); got != "" {
		if TraceIDFromContext(extracted) == "" {
			t.Error("traceparent was written but did not extract back")
		}
	}
}

func TestExtractAdoptsAnUpstreamTrace(t *testing.T) {
	if _, err := InitTracing(context.Background(), "", "test-service", "0.0.1", 1.0); err != nil {
		t.Fatal(err)
	}

	req := httptest.NewRequest(http.MethodPost, "/v1/answer", nil)
	req.Header.Set("traceparent", "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01")

	if got := TraceIDFromContext(Extract(context.Background(), req)); got != "4bf92f3577b34da6a3ce929d0e0e4736" {
		t.Errorf("trace id = %q, want the id from the inbound traceparent", got)
	}
}

func TestTraceIDIsEmptyWithoutASpan(t *testing.T) {
	if got := TraceIDFromContext(context.Background()); got != "" {
		t.Errorf("TraceIDFromContext = %q on a bare context, want empty", got)
	}
}
