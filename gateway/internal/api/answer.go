package api

import (
	"context"
	"encoding/json"
	"errors"
	"io"
	"math"
	"net/http"
	"strconv"
	"strings"

	"github.com/ceydaakin/aletheia/gateway/internal/contract"
	"github.com/ceydaakin/aletheia/gateway/internal/obs"
	"github.com/ceydaakin/aletheia/gateway/internal/pipeline"
	"github.com/ceydaakin/aletheia/gateway/internal/ratelimit"
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

	// Admission control happens after auth (so an unauthenticated flood cannot
	// consume a real tenant's budget) and before any upstream work.
	outcome, release := s.limiter.Acquire(tnt.ID)
	if outcome != ratelimit.Allowed {
		s.writeThrottled(w, r, tnt.ID, outcome)
		return
	}
	defer release()

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

// writeThrottled reports the two admission failures distinctly. Both are 429,
// but a client that is sending too fast and one that is holding too many open
// connections need different fixes, and a single opaque status tells them
// nothing about which they are doing.
func (s *Server) writeThrottled(
	w http.ResponseWriter, r *http.Request, tenantID string, outcome ratelimit.Outcome,
) {
	code, message := "rate_limited", "request rate exceeded for this tenant"
	if outcome == ratelimit.ConcurrencyLimited {
		code = "too_many_concurrent_requests"
		message = "too many requests in flight for this tenant"
	} else if after := s.limiter.RetryAfter(tenantID); after > 0 {
		w.Header().Set("Retry-After", strconv.Itoa(int(math.Ceil(after.Seconds()))))
	}

	s.metrics.Inc(obs.MetricThrottled, "reason", code)
	obs.LoggerFor(r.Context(), s.log).Warn("request throttled",
		"tenant_id", tenantID, "reason", code)
	writeError(w, r, http.StatusTooManyRequests, code, message)
}

func (s *Server) recordDecision(resp contract.AnswerResponse) {
	s.metrics.Inc(obs.MetricDecisions, "decision", string(resp.Decision))
	if resp.Decision == contract.DecisionAbstain {
		s.metrics.Inc(obs.MetricAbstentions, "reason", string(resp.AbstainReason))
	}
}
