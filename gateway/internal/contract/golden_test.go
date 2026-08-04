package contract

import (
	"bytes"
	"encoding/json"
	"os"
	"path/filepath"
	"testing"
)

// goldenDir holds the fixtures both implementations parse. See
// testdata/contracts/README.md.
var goldenDir = filepath.Join("..", "..", "..", "testdata", "contracts")

// decodeStrict fails on any field the Go types do not know about, which is the
// point: silent tolerance is how the two halves of the contract drift apart.
func decodeStrict(t *testing.T, name string, into any) {
	t.Helper()
	raw, err := os.ReadFile(filepath.Join(goldenDir, name))
	if err != nil {
		t.Fatalf("read fixture: %v", err)
	}
	dec := json.NewDecoder(bytes.NewReader(raw))
	dec.DisallowUnknownFields()
	if err := dec.Decode(into); err != nil {
		t.Fatalf("%s: %v", name, err)
	}
}

func TestGoldenRetrieveResponse(t *testing.T) {
	var got RetrieveResponse
	decodeStrict(t, "retrieve_response.json", &got)

	if len(got.Chunks) != 2 {
		t.Fatalf("chunks = %d, want 2", len(got.Chunks))
	}
	if got.Chunks[0].ChunkID != "doc_412:v3:chunk_18" || got.Chunks[0].Version != 3 {
		t.Errorf("chunk identity did not round-trip: %+v", got.Chunks[0])
	}
	if got.RerankedTo != 6 || got.K != 24 {
		t.Errorf("k/reranked_to = %d/%d, want 24/6", got.K, got.RerankedTo)
	}
}

func TestGoldenGenerateResponse(t *testing.T) {
	var got GenerateResponse
	decodeStrict(t, "generate_response.json", &got)

	if len(got.Claims) != 2 {
		t.Fatalf("claims = %d, want 2", len(got.Claims))
	}
	if len(got.Claims[1].Citations) != 0 {
		t.Errorf("the uncited claim came back with citations: %+v", got.Claims[1])
	}
}

func TestGoldenVerifyResponse(t *testing.T) {
	var got VerifyResponse
	decodeStrict(t, "verify_response.json", &got)

	if got.Claims[0].Status != StatusSupported || got.Claims[1].Status != StatusUnsupported {
		t.Errorf("claim statuses did not round-trip: %+v", got.Claims)
	}
	if got.Claims[0].SupportScore != 0.94 {
		t.Errorf("support_score = %v, want 0.94", got.Claims[0].SupportScore)
	}
}

func TestGoldenDecideResponse(t *testing.T) {
	var got DecideResponse
	decodeStrict(t, "decide_response.json", &got)

	if got.Decision != DecisionAnswerFlags {
		t.Errorf("decision = %q, want %q", got.Decision, DecisionAnswerFlags)
	}
	if got.Claims[1].Action != ActionRemoved {
		t.Errorf("action = %q, want %q", got.Claims[1].Action, ActionRemoved)
	}
	if got.CalibrationID == "" {
		t.Error("calibration_id did not round-trip")
	}
}

func TestGoldenAbstainResponse(t *testing.T) {
	var got AnswerResponse
	decodeStrict(t, "answer_response_abstain.json", &got)

	if got.Decision != DecisionAbstain {
		t.Fatalf("decision = %q, want abstain", got.Decision)
	}
	if got.Answer != "" {
		t.Errorf("an abstention carries an answer: %q", got.Answer)
	}
	if got.AbstainReason != ReasonInsufficientEvidence {
		t.Errorf("abstain_reason = %q", got.AbstainReason)
	}
	if len(got.SuggestedSources) == 0 {
		t.Error("an abstention with no suggested sources leaves the user nowhere to go")
	}
}

// Marshalling must produce exactly the field names the fixtures use.
func TestFieldNamesAreStable(t *testing.T) {
	raw, err := json.Marshal(AnswerResponse{Decision: DecisionAbstain, TraceID: "x"})
	if err != nil {
		t.Fatal(err)
	}
	for _, want := range []string{
		`"decision"`, `"answer"`, `"claims"`, `"risk"`, `"retrieval"`, `"trace_id"`,
	} {
		if !bytes.Contains(raw, []byte(want)) {
			t.Errorf("marshalled response is missing %s: %s", want, raw)
		}
	}
	// Optional fields must stay absent when unset, or clients cannot tell
	// "no abstention" from "abstained for the empty reason".
	for _, unwanted := range []string{`"abstain_reason"`, `"suggested_sources"`, `"degraded"`} {
		if bytes.Contains(raw, []byte(unwanted)) {
			t.Errorf("unset optional field %s was serialised: %s", unwanted, raw)
		}
	}
}

func TestAnswerRequestValidation(t *testing.T) {
	budget := func(f float64) *float64 { return &f }

	cases := []struct {
		name    string
		req     AnswerRequest
		wantErr bool
	}{
		{"minimal", AnswerRequest{Query: "hello"}, false},
		{"full", AnswerRequest{Query: "hello", RiskBudget: budget(0.05), Mode: ModeStrict, AsOf: "2026-01-01"}, false},
		{"empty query", AnswerRequest{Query: "   "}, true},
		{"bad mode", AnswerRequest{Query: "hello", Mode: "yolo"}, true},
		{"budget zero", AnswerRequest{Query: "hello", RiskBudget: budget(0)}, true},
		{"budget too high", AnswerRequest{Query: "hello", RiskBudget: budget(0.9)}, true},
		{"budget negative", AnswerRequest{Query: "hello", RiskBudget: budget(-0.1)}, true},
		{"short as_of", AnswerRequest{Query: "hello", AsOf: "2026"}, true},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			if err := tc.req.Validate(); (err != nil) != tc.wantErr {
				t.Errorf("Validate() error = %v, wantErr = %v", err, tc.wantErr)
			}
		})
	}
}
