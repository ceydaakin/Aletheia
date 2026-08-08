package api

import (
	"net/http"
	"time"

	"github.com/ceydaakin/aletheia/gateway/internal/obs"
	"github.com/ceydaakin/aletheia/gateway/internal/upstream"
)

// tracer adopts any inbound trace context, opens the server span, and settles on
// the id echoed back to the client.
//
// The response's trace_id is the OTel trace id whenever tracing is on. A user
// reporting a bad answer quotes that field, and it has to find the trace in the
// collector — a second, unrelated identifier would make the field decorative.
// With tracing off it falls back to a ULID so the field is never empty.
func (s *Server) tracer(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		ctx := obs.Extract(r.Context(), r)
		ctx, span := obs.StartSpan(ctx, "POST "+r.URL.Path)
		defer span.End()

		id := obs.TraceIDFromContext(ctx)
		if id == "" {
			if id = r.Header.Get(upstream.HeaderTraceID); id == "" {
				id = obs.NewTraceID()
			}
		}
		w.Header().Set(upstream.HeaderTraceID, id)
		next.ServeHTTP(w, r.WithContext(obs.WithTraceID(ctx, id)))
	})
}

// statusRecorder captures the status code for logging and metrics. It forwards
// Flush so SSE still streams through the middleware chain.
type statusRecorder struct {
	http.ResponseWriter
	status int
	wrote  bool
}

func (s *statusRecorder) WriteHeader(code int) {
	if !s.wrote {
		s.status = code
		s.wrote = true
	}
	s.ResponseWriter.WriteHeader(code)
}

func (s *statusRecorder) Write(b []byte) (int, error) {
	if !s.wrote {
		s.status = http.StatusOK
		s.wrote = true
	}
	return s.ResponseWriter.Write(b)
}

func (s *statusRecorder) Flush() {
	if f, ok := s.ResponseWriter.(http.Flusher); ok {
		f.Flush()
	}
}

func (s *Server) logger(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		start := time.Now()
		rec := &statusRecorder{ResponseWriter: w, status: http.StatusOK}
		next.ServeHTTP(rec, r)

		elapsed := time.Since(start)
		// Health and metrics scrapes would drown everything else.
		if r.URL.Path != "/healthz" && r.URL.Path != "/metrics" {
			obs.LoggerFor(r.Context(), s.log).Info("request",
				"method", r.Method,
				"path", r.URL.Path,
				"status", rec.status,
				"duration_ms", elapsed.Milliseconds(),
			)
			s.metrics.Observe(obs.MetricRequestLatncy, elapsed.Seconds(), "path", r.URL.Path)
			s.metrics.Inc(obs.MetricRequests, "path", r.URL.Path, "status", statusClass(rec.status))
		}
	})
}

// recoverer turns a panic into a 500 rather than a dropped connection. A panic
// here is a bug, but a bug that takes down the process takes down every other
// tenant's in-flight request with it.
func (s *Server) recoverer(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		defer func() {
			if rec := recover(); rec != nil {
				obs.LoggerFor(r.Context(), s.log).Error("panic recovered",
					"panic", rec, "path", r.URL.Path)
				writeError(w, r, http.StatusInternalServerError, "internal_error", "")
			}
		}()
		next.ServeHTTP(w, r)
	})
}

func statusClass(code int) string {
	switch {
	case code < 300:
		return "2xx"
	case code < 400:
		return "3xx"
	case code < 500:
		return "4xx"
	default:
		return "5xx"
	}
}
