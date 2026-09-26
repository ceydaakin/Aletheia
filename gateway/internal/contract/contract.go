// Package contract defines the wire types shared between the gateway and the
// Python services. It is the Go half of a contract whose Python half lives in
// python/src/aletheia/contracts.py. The two are kept in sync by a golden-fixture
// round-trip test in CI (see ADR-0001, "Consequences").
package contract

import (
	"errors"
	"fmt"
	"strings"
)

// Mode selects what the gateway does with claims the verifier could not support.
type Mode string

const (
	// ModeStrict removes unsupported claims from the answer entirely.
	ModeStrict Mode = "strict"
	// ModeFlagged keeps unsupported claims but marks them in the response.
	ModeFlagged Mode = "flagged"
	// ModePermissive returns the answer as generated, with scores attached.
	// The risk guarantee does not hold in this mode and the response says so.
	ModePermissive Mode = "permissive"
)

func (m Mode) Valid() bool {
	switch m {
	case ModeStrict, ModeFlagged, ModePermissive:
		return true
	}
	return false
}

// Decision is the risk controller's verdict for a whole response (ADR-0004).
type Decision string

const (
	DecisionAnswer      Decision = "answer"
	DecisionAnswerFlags Decision = "answer_with_flags"
	DecisionAbstain     Decision = "abstain"
)

// AbstainReason tells the caller why no answer was returned. Every one of these
// is a deliberate outcome, not an error.
type AbstainReason string

const (
	// ReasonInsufficientEvidence: retrieved context does not support an answer.
	ReasonInsufficientEvidence AbstainReason = "insufficient_evidence"
	// ReasonConflictingSources: sources disagree and no as_of was given.
	ReasonConflictingSources AbstainReason = "conflicting_sources"
	// ReasonOutOfCorpus: nothing relevant was retrieved at all.
	ReasonOutOfCorpus AbstainReason = "out_of_corpus"
	// ReasonStaleCalibration: no valid threshold, so no guarantee can be made.
	ReasonStaleCalibration AbstainReason = "stale_calibration"
	// ReasonDriftDetected: a threshold exists, but live traffic or the corpus is
	// no longer exchangeable with the data it was certified on, so it is not
	// quoted. Always accompanied by degraded=true.
	ReasonDriftDetected AbstainReason = "drift_detected"
)

// ClaimStatus is the verifier's per-claim verdict.
type ClaimStatus string

const (
	StatusSupported   ClaimStatus = "supported"
	StatusUnsupported ClaimStatus = "unsupported"
)

// ClaimAction is what the action policy did with a claim.
type ClaimAction string

const (
	ActionKept    ClaimAction = "kept"
	ActionFlagged ClaimAction = "flagged"
	ActionRemoved ClaimAction = "removed"
)

const (
	// MaxQueryLen bounds the query to keep prompt cost predictable.
	MaxQueryLen = 4000
	// MinRiskBudget guards against a budget so tight that no answer can ever
	// clear it, which would look like a system fault rather than a choice.
	MinRiskBudget = 0.001
	// MaxRiskBudget: above this the guarantee is not worth quoting.
	MaxRiskBudget = 0.5
)

// AnswerRequest is the public request body for POST /v1/answer.
type AnswerRequest struct {
	Query string `json:"query"`
	// RiskBudget is alpha: the tolerated probability that the returned response
	// contains at least one unsupported claim. Nil means "use the tenant default".
	RiskBudget *float64 `json:"risk_budget,omitempty"`
	// AsOf selects the valid-time slice of the corpus (RFC 3339 date).
	AsOf   string `json:"as_of,omitempty"`
	Mode   Mode   `json:"mode,omitempty"`
	Stream bool   `json:"stream,omitempty"`
}

// Validate reports the first problem with the request, if any.
func (r *AnswerRequest) Validate() error {
	if strings.TrimSpace(r.Query) == "" {
		return errors.New("query must not be empty")
	}
	if len(r.Query) > MaxQueryLen {
		return fmt.Errorf("query exceeds %d characters", MaxQueryLen)
	}
	if r.Mode != "" && !r.Mode.Valid() {
		return fmt.Errorf("mode must be one of strict, flagged, permissive (got %q)", r.Mode)
	}
	if r.RiskBudget != nil {
		b := *r.RiskBudget
		if b < MinRiskBudget || b > MaxRiskBudget {
			return fmt.Errorf("risk_budget must be in [%g, %g] (got %g)", MinRiskBudget, MaxRiskBudget, b)
		}
	}
	if r.AsOf != "" && len(r.AsOf) < 10 {
		return errors.New("as_of must be an RFC 3339 date, e.g. 2026-01-01")
	}
	return nil
}

// Claim is one atomic assertion extracted from the answer, with its evidence.
type Claim struct {
	Text      string   `json:"text"`
	Citations []string `json:"citations"`
	// SupportScore is the NLI entailment score against the cited chunks, in [0,1].
	SupportScore float64     `json:"support_score"`
	Status       ClaimStatus `json:"status"`
	Action       ClaimAction `json:"action,omitempty"`
}

// Risk carries the guarantee and the numbers behind it. Guarantee always names
// the calibration run that produced the threshold (ADR-0004).
type Risk struct {
	Budget        float64 `json:"budget"`
	Statistic     float64 `json:"statistic"`
	Threshold     float64 `json:"threshold"`
	Guarantee     string  `json:"guarantee"`
	CalibrationID string  `json:"calibration_id,omitempty"`
}

// RetrievalInfo is a summary of what retrieval did, for debugging and for the
// research persona.
type RetrievalInfo struct {
	K          int   `json:"k"`
	RerankedTo int   `json:"reranked_to"`
	LatencyMS  int64 `json:"latency_ms"`
}

// Source is a pointer back into the corpus, returned with abstentions so the
// user has somewhere to go.
type Source struct {
	ChunkID string  `json:"chunk_id"`
	DocID   string  `json:"doc_id"`
	Title   string  `json:"title,omitempty"`
	Score   float64 `json:"score"`
	Snippet string  `json:"snippet,omitempty"`
}

// AnswerResponse is the public response body for POST /v1/answer.
type AnswerResponse struct {
	Decision  Decision      `json:"decision"`
	Answer    string        `json:"answer"`
	Claims    []Claim       `json:"claims"`
	Risk      Risk          `json:"risk"`
	Retrieval RetrievalInfo `json:"retrieval"`

	AbstainReason    AbstainReason `json:"abstain_reason,omitempty"`
	SuggestedSources []Source      `json:"suggested_sources,omitempty"`

	// Degraded is set when the guarantee could not be established as stated —
	// drift detected, calibration stale, or a stage failed. Never hidden.
	Degraded bool   `json:"degraded,omitempty"`
	TraceID  string `json:"trace_id"`
}

// ErrorResponse is returned for 4xx/5xx. Note that an abstention is a 200: it is
// a successful, correct answer to the question "should you trust me here?".
type ErrorResponse struct {
	Error   string `json:"error"`
	Message string `json:"message,omitempty"`
	TraceID string `json:"trace_id,omitempty"`
}
