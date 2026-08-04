// Package api is the gateway's HTTP surface: routing, auth, middleware, and the
// JSON/SSE encodings of the answer contract.
package api

import (
	"encoding/json"
	"log/slog"
	"net/http"
	"time"

	"github.com/ceydaakin/aletheia/gateway/internal/config"
	"github.com/ceydaakin/aletheia/gateway/internal/contract"
	"github.com/ceydaakin/aletheia/gateway/internal/obs"
	"github.com/ceydaakin/aletheia/gateway/internal/pipeline"
	"github.com/ceydaakin/aletheia/gateway/internal/tenant"
	"github.com/ceydaakin/aletheia/gateway/internal/upstream"
)

type Server struct {
	cfg      *config.Config
	tenants  *tenant.Registry
	pipeline *pipeline.Pipeline
	log      *slog.Logger
	metrics  *obs.Metrics
	// upstreams is used only by the readiness probe.
	upstreams []*upstream.Client
}

func NewServer(
	cfg *config.Config,
	tenants *tenant.Registry,
	p *pipeline.Pipeline,
	log *slog.Logger,
	m *obs.Metrics,
	upstreams []*upstream.Client,
) *Server {
	return &Server{cfg: cfg, tenants: tenants, pipeline: p, log: log, metrics: m, upstreams: upstreams}
}

func (s *Server) Handler() http.Handler {
	mux := http.NewServeMux()
	mux.HandleFunc("GET /healthz", s.handleHealthz)
	mux.HandleFunc("GET /readyz", s.handleReadyz)
	mux.HandleFunc("GET /metrics", s.handleMetrics)
	mux.HandleFunc("POST /v1/answer", s.handleAnswer)

	return s.recoverer(s.tracer(s.logger(mux)))
}

// HTTPServer returns a server with timeouts set. WriteTimeout must exceed the
// request budget or SSE streams get cut mid-flight by the transport rather than
// by our own deadline logic, which would bypass the abstain-on-failure policy.
func (s *Server) HTTPServer() *http.Server {
	return &http.Server{
		Addr:              s.cfg.Addr,
		Handler:           s.Handler(),
		ReadHeaderTimeout: 5 * time.Second,
		ReadTimeout:       15 * time.Second,
		WriteTimeout:      s.cfg.RequestTimeout + 10*time.Second,
		IdleTimeout:       60 * time.Second,
	}
}

// --- helpers ---

func writeJSON(w http.ResponseWriter, status int, v any) {
	w.Header().Set("Content-Type", "application/json; charset=utf-8")
	w.WriteHeader(status)
	enc := json.NewEncoder(w)
	enc.SetEscapeHTML(false)
	_ = enc.Encode(v)
}

func writeError(w http.ResponseWriter, r *http.Request, status int, code, message string) {
	writeJSON(w, status, contract.ErrorResponse{
		Error:   code,
		Message: message,
		TraceID: obs.TraceID(r.Context()),
	})
}
