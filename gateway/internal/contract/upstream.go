package contract

// Types in this file are the gateway↔service contracts. They are internal to the
// deployment (never exposed to clients) but are still a contract: the Python side
// must agree, and CI checks that it does.

// Chunk is a retrieved passage, identified by document, version, and position so
// that a citation stays meaningful after the document is amended (ADR-0002).
type Chunk struct {
	ChunkID string  `json:"chunk_id"`
	DocID   string  `json:"doc_id"`
	Version int     `json:"version"`
	Title   string  `json:"title,omitempty"`
	Text    string  `json:"text"`
	Score   float64 `json:"score"`
}

// --- Retrieval service ---

type RetrieveRequest struct {
	TenantID string `json:"tenant_id"`
	Query    string `json:"query"`
	AsOf     string `json:"as_of,omitempty"`
	K        int    `json:"k"`
}

type RetrieveResponse struct {
	Chunks     []Chunk `json:"chunks"`
	K          int     `json:"k"`
	RerankedTo int     `json:"reranked_to"`
	LatencyMS  int64   `json:"latency_ms"`
}

// --- Generation service ---

// DraftClaim is a claim as produced by citation-constrained decoding: it knows
// what it cites but has not been checked against it yet.
type DraftClaim struct {
	Text      string   `json:"text"`
	Citations []string `json:"citations"`
}

type GenerateRequest struct {
	TenantID string  `json:"tenant_id"`
	Query    string  `json:"query"`
	Chunks   []Chunk `json:"chunks"`
}

type GenerateResponse struct {
	Answer string       `json:"answer"`
	Claims []DraftClaim `json:"claims"`
}

// --- Verifier service ---

type VerifyRequest struct {
	TenantID string       `json:"tenant_id"`
	Claims   []DraftClaim `json:"claims"`
	Chunks   []Chunk      `json:"chunks"`
}

type VerifyResponse struct {
	Claims []Claim `json:"claims"`
}

// --- Risk controller ---

type DecideRequest struct {
	TenantID   string  `json:"tenant_id"`
	RiskBudget float64 `json:"risk_budget"`
	Mode       Mode    `json:"mode"`
	Claims     []Claim `json:"claims"`
}

// DecideResponse returns the verdict plus the claims with actions applied. The
// gateway reassembles the answer text from the surviving claims rather than
// letting the risk service rewrite prose, so there is exactly one place where
// the returned answer is constructed.
type DecideResponse struct {
	Decision      Decision      `json:"decision"`
	Statistic     float64       `json:"statistic"`
	Threshold     float64       `json:"threshold"`
	CalibrationID string        `json:"calibration_id"`
	Guarantee     string        `json:"guarantee"`
	AbstainReason AbstainReason `json:"abstain_reason,omitempty"`
	Degraded      bool          `json:"degraded,omitempty"`
	Claims        []Claim       `json:"claims"`
}
