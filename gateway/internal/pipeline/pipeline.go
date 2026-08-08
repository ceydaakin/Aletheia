// Package pipeline orchestrates one /v1/answer request across the four model
// services and applies the partial-failure policy from ADR-0001.
//
// The invariant this package exists to protect: no failure path produces an
// unverified answer. Every stage that fails lands on an abstention with a reason
// code, except generation — where there is nothing to degrade to, so we 503.
package pipeline

import (
	"context"
	"errors"
	"log/slog"
	"strings"
	"time"

	"go.opentelemetry.io/otel/codes"

	"github.com/ceydaakin/aletheia/gateway/internal/contract"
	"github.com/ceydaakin/aletheia/gateway/internal/obs"
	"github.com/ceydaakin/aletheia/gateway/internal/upstream"
)

// Event types emitted during streaming. The client may render provisional
// content, but must not treat it as final until EventDecision arrives.
const (
	EventRetrieval   = "retrieval"
	EventProvisional = "provisional"
	EventDecision    = "decision"
	EventDone        = "done"
	EventError       = "error"
)

type Event struct {
	Type string
	Data any
}

// EmitFunc receives streaming events. Nil for non-streaming requests.
type EmitFunc func(Event)

// FatalError means the request cannot be answered and cannot be abstained from
// either — the caller should return 5xx.
type FatalError struct{ Err error }

func (e *FatalError) Error() string { return e.Err.Error() }
func (e *FatalError) Unwrap() error { return e.Err }

type Input struct {
	TenantID   string
	Query      string
	AsOf       string
	Mode       contract.Mode
	RiskBudget float64
}

type Pipeline struct {
	retrieval  *upstream.Client
	generation *upstream.Client
	verifier   *upstream.Client
	risk       *upstream.Client
	k          int
	log        *slog.Logger
	metrics    *obs.Metrics
}

func New(retrieval, generation, verifier, risk *upstream.Client, k int, log *slog.Logger, m *obs.Metrics) *Pipeline {
	return &Pipeline{
		retrieval: retrieval, generation: generation, verifier: verifier, risk: risk,
		k: k, log: log, metrics: m,
	}
}

func (p *Pipeline) Run(ctx context.Context, in Input, emit EmitFunc) (contract.AnswerResponse, error) {
	log := obs.LoggerFor(ctx, p.log)
	resp := contract.AnswerResponse{
		TraceID: obs.TraceID(ctx),
		Claims:  []contract.Claim{},
		Risk:    contract.Risk{Budget: in.RiskBudget},
	}

	// --- Stage 1: retrieval -------------------------------------------------
	// No evidence means no answer, by definition of the product.
	var retrieved contract.RetrieveResponse
	if err := p.call(ctx, p.retrieval, "/retrieve", contract.RetrieveRequest{
		TenantID: in.TenantID,
		Query:    in.Query,
		AsOf:     in.AsOf,
		K:        p.k,
	}, &retrieved); err != nil {
		log.Warn("retrieval failed", "error", err)
		return abstain(resp, contract.ReasonOutOfCorpus, nil, true), nil
	}
	resp.Retrieval = contract.RetrievalInfo{
		K:          retrieved.K,
		RerankedTo: retrieved.RerankedTo,
		LatencyMS:  retrieved.LatencyMS,
	}
	if len(retrieved.Chunks) == 0 {
		log.Info("no chunks retrieved", "query_len", len(in.Query))
		return abstain(resp, contract.ReasonOutOfCorpus, nil, false), nil
	}
	sources := topSources(retrieved.Chunks, 3)
	emitEvent(emit, Event{Type: EventRetrieval, Data: resp.Retrieval})

	// --- Stage 2: generation ------------------------------------------------
	// The only stage with no graceful degradation: without a draft there is
	// nothing to verify and nothing to abstain about.
	var generated contract.GenerateResponse
	if err := p.call(ctx, p.generation, "/generate", contract.GenerateRequest{
		TenantID: in.TenantID,
		Query:    in.Query,
		Chunks:   retrieved.Chunks,
	}, &generated); err != nil {
		log.Error("generation failed", "error", err)
		return resp, &FatalError{Err: err}
	}
	// Provisional: the client may render this, but it is not yet trustworthy.
	emitEvent(emit, Event{Type: EventProvisional, Data: map[string]any{
		"answer": generated.Answer,
		"claims": len(generated.Claims),
	}})

	// --- Stage 3: verification ----------------------------------------------
	// A verifier failure must never downgrade to "answer, unchecked".
	var verified contract.VerifyResponse
	if err := p.call(ctx, p.verifier, "/verify", contract.VerifyRequest{
		TenantID: in.TenantID,
		Claims:   generated.Claims,
		Chunks:   retrieved.Chunks,
	}, &verified); err != nil {
		log.Warn("verification failed; abstaining rather than answering unverified", "error", err)
		return abstain(resp, contract.ReasonInsufficientEvidence, sources, true), nil
	}

	// --- Stage 4: risk decision ---------------------------------------------
	// No threshold means no guarantee, and an answer without a guarantee is not
	// the product we are shipping.
	var decided contract.DecideResponse
	if err := p.call(ctx, p.risk, "/decide", contract.DecideRequest{
		TenantID:   in.TenantID,
		RiskBudget: in.RiskBudget,
		Mode:       in.Mode,
		Claims:     verified.Claims,
	}, &decided); err != nil {
		log.Warn("risk controller failed; no guarantee available", "error", err)
		return abstain(resp, contract.ReasonStaleCalibration, sources, true), nil
	}

	resp.Decision = decided.Decision
	resp.Claims = decided.Claims
	resp.Degraded = decided.Degraded
	resp.Risk = contract.Risk{
		Budget:        in.RiskBudget,
		Statistic:     decided.Statistic,
		Threshold:     decided.Threshold,
		CalibrationID: decided.CalibrationID,
		Guarantee:     decided.Guarantee,
	}

	if decided.Decision == contract.DecisionAbstain {
		resp.AbstainReason = orDefault(decided.AbstainReason, contract.ReasonInsufficientEvidence)
		resp.SuggestedSources = sources
		resp.Answer = ""
	} else {
		// Assemble the answer from surviving claims. Doing it here — rather than
		// letting the risk service return rewritten prose — keeps exactly one
		// place where the text the user sees is constructed, which is the text
		// the guarantee is about (ADR-0004).
		resp.Answer = assemble(decided.Claims)
	}

	emitEvent(emit, Event{Type: EventDecision, Data: resp})
	return resp, nil
}

// call runs one upstream stage, recording its latency, outcome, and span.
//
// One span per stage is what makes the PRD's required trace — retrieval →
// generation → verification → decision — readable: the question a trace gets
// opened for is "which stage turned this into an abstention", and that is
// answerable only if each stage is separately visible.
func (p *Pipeline) call(ctx context.Context, c *upstream.Client, path string, in, out any) error {
	ctx, span := obs.StartSpan(ctx, "aletheia.stage."+c.Name())
	defer span.End()

	start := time.Now()
	err := c.Post(ctx, path, in, out)
	p.metrics.Observe(obs.MetricStageLatency, time.Since(start).Seconds(), "stage", c.Name())
	if err != nil {
		kind := "error"
		var ue *upstream.Error
		if errors.As(err, &ue) && ue.Timeout {
			kind = "timeout"
		}
		p.metrics.Inc(obs.MetricStageErrors, "stage", c.Name(), "kind", kind)
		span.RecordError(err)
		span.SetStatus(codes.Error, kind)
	}
	return err
}

// abstain builds a well-formed abstention. degraded marks the case where we are
// abstaining because a component failed rather than because the evidence was
// genuinely thin — the caller deserves to know the difference.
func abstain(resp contract.AnswerResponse, reason contract.AbstainReason, sources []contract.Source, degraded bool) contract.AnswerResponse {
	resp.Decision = contract.DecisionAbstain
	resp.AbstainReason = reason
	resp.SuggestedSources = sources
	resp.Answer = ""
	resp.Claims = []contract.Claim{}
	resp.Degraded = degraded
	if resp.Risk.Guarantee == "" {
		resp.Risk.Guarantee = "no guarantee issued: abstained"
	}
	return resp
}

// assemble joins the claims that survived the action policy, in order.
func assemble(claims []contract.Claim) string {
	parts := make([]string, 0, len(claims))
	for _, c := range claims {
		if c.Action == contract.ActionRemoved {
			continue
		}
		if t := strings.TrimSpace(c.Text); t != "" {
			parts = append(parts, t)
		}
	}
	return strings.Join(parts, " ")
}

func topSources(chunks []contract.Chunk, n int) []contract.Source {
	if len(chunks) < n {
		n = len(chunks)
	}
	out := make([]contract.Source, 0, n)
	for _, c := range chunks[:n] {
		out = append(out, contract.Source{
			ChunkID: c.ChunkID,
			DocID:   c.DocID,
			Title:   c.Title,
			Score:   c.Score,
			Snippet: snippet(c.Text, 240),
		})
	}
	return out
}

func snippet(s string, max int) string {
	s = strings.TrimSpace(s)
	if len(s) <= max {
		return s
	}
	// Cut on a rune boundary; the corpora are Turkish and English, so naive byte
	// slicing would produce mojibake in exactly the language we care about.
	r := []rune(s)
	if len(r) <= max {
		return s
	}
	return strings.TrimSpace(string(r[:max])) + "…"
}

func orDefault(r, def contract.AbstainReason) contract.AbstainReason {
	if r == "" {
		return def
	}
	return r
}

func emitEvent(emit EmitFunc, e Event) {
	if emit != nil {
		emit(e)
	}
}
