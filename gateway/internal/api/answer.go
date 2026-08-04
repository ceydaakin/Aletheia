package api

import (
	"context"
	"encoding/json"
	"errors"
	"io"
	"net/http"
	"strings"

	"github.com/ceydaakin/aletheia/gateway/internal/contract"
	"github.com/ceydaakin/aletheia/gateway/internal/obs"
	"github.com/ceydaakin/aletheia/gateway/internal/pipeline"
	"github.com/ceydaakin/aletheia/gateway/internal/tenant"
)

// defaultMode is strict: unsupported claims are removed unless the caller opts
// into seeing them. A product whose premise is "do not emit unsupported claims"
// should not default to emitting them.
const defaultMode = contract.ModeStrict

func (s *Server) handleAnswer(w http.ResponseWriter, r *http.Request) {
	tnt, ok := s.authenticate(r)
	if !ok {
		writeError(w, r, http.StatusUnauthorized, "unauthorized", "missing or invalid bearer token")
		return
	}

	req, err := decodeRequest(r, s.cfg.MaxBodyBytes)
	if err != nil {
		writeError(w, r, http.StatusBadRequest, "invalid_request", err.Error())
		return
	}

	in := pipeline.Input{
		TenantID:   tnt.ID,
		Query:      req.Query,
		AsOf:       req.AsOf,
		Mode:       req.Mode,
		RiskBudget: tnt.DefaultRiskBudget,
	}
	if in.Mode == "" {
		in.Mode = defaultMode
	}
	if req.RiskBudget != nil {
		in.RiskBudget = *req.RiskBudget
	}

	ctx, cancel := context.WithTimeout(r.Context(), s.cfg.RequestTimeout)
	defer cancel()

	if req.Stream {
		s.answerStream(ctx, w, r, in)
		return
	}

	resp, err := s.pipeline.Run(ctx, in, nil)
	if err != nil {
		s.writePipelineError(w, r, err)
		return
	}
	s.recordDecision(resp)
	writeJSON(w, http.StatusOK, resp)
}

// answerStream serves the request over SSE. Content streams with a `provisional`
// event before verification completes; the `decision` event is the only
// authoritative one, and clients are expected to wait for it (PRD F8).
func (s *Server) answerStream(ctx context.Context, w http.ResponseWriter, r *http.Request, in pipeline.Input) {
	flusher, ok := w.(http.Flusher)
	if !ok {
		writeError(w, r, http.StatusInternalServerError, "streaming_unsupported", "")
		return
	}

	h := w.Header()
	h.Set("Content-Type", "text/event-stream; charset=utf-8")
	h.Set("Cache-Control", "no-cache")
	h.Set("Connection", "keep-alive")
	// Nginx and friends will buffer SSE into uselessness without this.
	h.Set("X-Accel-Buffering", "no")
	w.WriteHeader(http.StatusOK)
	flusher.Flush()

	emit := func(e pipeline.Event) {
		writeSSE(w, e.Type, e.Data)
		flusher.Flush()
	}

	resp, err := s.pipeline.Run(ctx, in, emit)
	if err != nil {
		// The status line is long gone, so a stream-level failure has to be
		// reported as an event rather than an HTTP status.
		writeSSE(w, pipeline.EventError, contract.ErrorResponse{
			Error:   pipelineErrorCode(err),
			TraceID: obs.TraceID(ctx),
		})
		flusher.Flush()
		return
	}
	s.recordDecision(resp)
	writeSSE(w, pipeline.EventDone, map[string]string{"trace_id": resp.TraceID})
	flusher.Flush()
}

func writeSSE(w io.Writer, event string, data any) {
	payload, err := json.Marshal(data)
	if err != nil {
		payload = []byte(`{"error":"marshal_failed"}`)
	}
	// Data must not contain raw newlines; JSON encoding already guarantees that.
	_, _ = io.WriteString(w, "event: "+event+"\ndata: ")
	_, _ = w.Write(payload)
	_, _ = io.WriteString(w, "\n\n")
}

func (s *Server) authenticate(r *http.Request) (tenant.Tenant, bool) {
	auth := r.Header.Get("Authorization")
	const prefix = "Bearer "
	if len(auth) <= len(prefix) || !strings.EqualFold(auth[:len(prefix)], prefix) {
		return tenant.Tenant{}, false
	}
	return s.tenants.Lookup(strings.TrimSpace(auth[len(prefix):]))
}

func decodeRequest(r *http.Request, maxBytes int64) (contract.AnswerRequest, error) {
	var req contract.AnswerRequest
	dec := json.NewDecoder(http.MaxBytesReader(nil, r.Body, maxBytes))
	dec.DisallowUnknownFields()
	if err := dec.Decode(&req); err != nil {
		return req, err
	}
	return req, req.Validate()
}

func (s *Server) writePipelineError(w http.ResponseWriter, r *http.Request, err error) {
	var fatal *pipeline.FatalError
	if errors.As(err, &fatal) {
		writeError(w, r, http.StatusServiceUnavailable, "generation_unavailable",
			"the answer could not be generated; no unverified answer is returned by design")
		return
	}
	if errors.Is(err, context.DeadlineExceeded) {
		writeError(w, r, http.StatusGatewayTimeout, "deadline_exceeded", "")
		return
	}
	obs.LoggerFor(r.Context(), s.log).Error("unhandled pipeline error", "error", err)
	writeError(w, r, http.StatusInternalServerError, "internal_error", "")
}

func pipelineErrorCode(err error) string {
	var fatal *pipeline.FatalError
	switch {
	case errors.As(err, &fatal):
		return "generation_unavailable"
	case errors.Is(err, context.DeadlineExceeded):
		return "deadline_exceeded"
	default:
		return "internal_error"
	}
}

func (s *Server) recordDecision(resp contract.AnswerResponse) {
	s.metrics.Inc(obs.MetricDecisions, "decision", string(resp.Decision))
	if resp.Decision == contract.DecisionAbstain {
		s.metrics.Inc(obs.MetricAbstentions, "reason", string(resp.AbstainReason))
	}
}
