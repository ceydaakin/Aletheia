package api

import (
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"

	"github.com/ceydaakin/aletheia/gateway/internal/config"
	"github.com/ceydaakin/aletheia/gateway/internal/contract"
	"github.com/ceydaakin/aletheia/gateway/internal/obs"
	"github.com/ceydaakin/aletheia/gateway/internal/pipeline"
	"github.com/ceydaakin/aletheia/gateway/internal/tenant"
	"github.com/ceydaakin/aletheia/gateway/internal/upstream"
)

const testKey = "test-key"

// stubs describes the four upstreams. A nil handler means "this service is down",
// which is how the failure-policy tests are written.
type stubs struct {
	retrieval  http.HandlerFunc
	generation http.HandlerFunc
	verifier   http.HandlerFunc
	risk       http.HandlerFunc
}

func newTestServer(t *testing.T, s stubs) http.Handler {
	t.Helper()

	mk := func(h http.HandlerFunc) string {
		if h == nil {
			// A closed listener: connection refused, the realistic "service is
			// down" case rather than a polite 500.
			srv := httptest.NewServer(http.NotFoundHandler())
			url := srv.URL
			srv.Close()
			return url
		}
		mux := http.NewServeMux()
		mux.HandleFunc("GET /healthz", func(w http.ResponseWriter, r *http.Request) {
			w.WriteHeader(http.StatusOK)
		})
		mux.Handle("/", h)
		srv := httptest.NewServer(mux)
		t.Cleanup(srv.Close)
		return srv.URL
	}

	cfg := &config.Config{
		Addr:              ":0",
		RequestTimeout:    3 * time.Second,
		RetrievalK:        24,
		MaxBodyBytes:      64 << 10,
		DefaultRiskBudget: 0.05,
		// Effectively unlimited: these tests are about the pipeline, and a zero
		// value here is an empty token bucket that rejects everything.
		RateLimitPerSecond:     1e6,
		RateLimitBurst:         1e6,
		MaxConcurrentPerTenant: 0,
	}
	stage := 2 * time.Second
	retrieval := upstream.New("retrieval", mk(s.retrieval), stage)
	generation := upstream.New("generation", mk(s.generation), stage)
	verifier := upstream.New("verifier", mk(s.verifier), stage)
	risk := upstream.New("risk", mk(s.risk), stage)

	reg, err := tenant.NewRegistry("acme:" + testKey + ":0.05")
	if err != nil {
		t.Fatalf("registry: %v", err)
	}
	log := obs.NewLogger("error")
	metrics := obs.NewMetrics()
	p := pipeline.New(retrieval, generation, verifier, risk, cfg.RetrievalK, log, metrics)
	return NewServer(cfg, reg, p, log, metrics,
		[]*upstream.Client{retrieval, generation, verifier, risk}).Handler()
}

func jsonHandler(v any) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		_ = json.NewEncoder(w).Encode(v)
	}
}

func healthyStubs() stubs {
	return stubs{
		retrieval: jsonHandler(contract.RetrieveResponse{
			Chunks: []contract.Chunk{
				{ChunkID: "doc_412:v3:chunk_18", DocID: "doc_412", Version: 3, Text: "Notice period is 30 days.", Score: 0.91},
				{ChunkID: "doc_412:v3:chunk_19", DocID: "doc_412", Version: 3, Text: "Applies to private contracts.", Score: 0.72},
			},
			K: 24, RerankedTo: 6, LatencyMS: 310,
		}),
		generation: jsonHandler(contract.GenerateResponse{
			Answer: "The notice period is 30 days. It also applies to public contracts.",
			Claims: []contract.DraftClaim{
				{Text: "The notice period is 30 days.", Citations: []string{"doc_412:v3:chunk_18"}},
				{Text: "It also applies to public contracts.", Citations: nil},
			},
		}),
		verifier: jsonHandler(contract.VerifyResponse{
			Claims: []contract.Claim{
				{Text: "The notice period is 30 days.", Citations: []string{"doc_412:v3:chunk_18"}, SupportScore: 0.94, Status: contract.StatusSupported},
				{Text: "It also applies to public contracts.", Citations: []string{}, SupportScore: 0.11, Status: contract.StatusUnsupported},
			},
		}),
		risk: jsonHandler(contract.DecideResponse{
			Decision:      contract.DecisionAnswerFlags,
			Statistic:     0.11,
			Threshold:     0.38,
			CalibrationID: "cal_test",
			Guarantee:     "P(unsupported_claim) <= 0.05 with 95% confidence, calibration_id=cal_test",
			Claims: []contract.Claim{
				{Text: "The notice period is 30 days.", Citations: []string{"doc_412:v3:chunk_18"}, SupportScore: 0.94, Status: contract.StatusSupported, Action: contract.ActionKept},
				{Text: "It also applies to public contracts.", Citations: []string{}, SupportScore: 0.11, Status: contract.StatusUnsupported, Action: contract.ActionRemoved},
			},
		}),
	}
}

func post(t *testing.T, h http.Handler, body string, key string) *httptest.ResponseRecorder {
	t.Helper()
	req := httptest.NewRequest(http.MethodPost, "/v1/answer", strings.NewReader(body))
	req.Header.Set("Content-Type", "application/json")
	if key != "" {
		req.Header.Set("Authorization", "Bearer "+key)
	}
	rec := httptest.NewRecorder()
	h.ServeHTTP(rec, req)
	return rec
}

func decode(t *testing.T, rec *httptest.ResponseRecorder) contract.AnswerResponse {
	t.Helper()
	var resp contract.AnswerResponse
	if err := json.Unmarshal(rec.Body.Bytes(), &resp); err != nil {
		t.Fatalf("decode response: %v (body=%s)", err, rec.Body.String())
	}
	return resp
}

func TestAnswerHappyPath(t *testing.T) {
	h := newTestServer(t, healthyStubs())
	rec := post(t, h, `{"query":"notice period?","risk_budget":0.05,"mode":"strict"}`, testKey)

	if rec.Code != http.StatusOK {
		t.Fatalf("status = %d, want 200 (body=%s)", rec.Code, rec.Body.String())
	}
	resp := decode(t, rec)
	if resp.Decision != contract.DecisionAnswerFlags {
		t.Errorf("decision = %q, want %q", resp.Decision, contract.DecisionAnswerFlags)
	}
	// The removed claim must not appear in the assembled answer — the guarantee
	// is about the text the user actually receives (ADR-0004).
	if strings.Contains(resp.Answer, "public contracts") {
		t.Errorf("answer contains a removed claim: %q", resp.Answer)
	}
	if !strings.Contains(resp.Answer, "30 days") {
		t.Errorf("answer dropped a supported claim: %q", resp.Answer)
	}
	if resp.Risk.CalibrationID == "" {
		t.Error("risk.calibration_id is empty: a guarantee without provenance is not a guarantee")
	}
	if resp.TraceID == "" {
		t.Error("trace_id is empty")
	}
}

// The core invariant: no upstream failure may produce an unverified answer.
func TestFailurePolicyNeverAnswersUnverified(t *testing.T) {
	cases := []struct {
		name       string
		mutate     func(*stubs)
		wantStatus int
		wantReason contract.AbstainReason
	}{
		{
			name:       "retrieval down",
			mutate:     func(s *stubs) { s.retrieval = nil },
			wantStatus: http.StatusOK,
			wantReason: contract.ReasonOutOfCorpus,
		},
		{
			name:       "verifier down",
			mutate:     func(s *stubs) { s.verifier = nil },
			wantStatus: http.StatusOK,
			wantReason: contract.ReasonInsufficientEvidence,
		},
		{
			name:       "risk controller down",
			mutate:     func(s *stubs) { s.risk = nil },
			wantStatus: http.StatusOK,
			wantReason: contract.ReasonStaleCalibration,
		},
	}

	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			s := healthyStubs()
			tc.mutate(&s)
			rec := post(t, newTestServer(t, s), `{"query":"notice period?"}`, testKey)

			if rec.Code != tc.wantStatus {
				t.Fatalf("status = %d, want %d (body=%s)", rec.Code, tc.wantStatus, rec.Body.String())
			}
			resp := decode(t, rec)
			if resp.Decision != contract.DecisionAbstain {
				t.Fatalf("decision = %q, want abstain", resp.Decision)
			}
			if resp.Answer != "" {
				t.Errorf("abstention carries an answer: %q", resp.Answer)
			}
			if resp.AbstainReason != tc.wantReason {
				t.Errorf("abstain_reason = %q, want %q", resp.AbstainReason, tc.wantReason)
			}
			if !resp.Degraded {
				t.Error("degraded = false; an abstention caused by a component failure must say so")
			}
		})
	}
}

// Generation is the one stage with nothing to degrade to.
func TestGenerationFailureIs503(t *testing.T) {
	s := healthyStubs()
	s.generation = nil
	rec := post(t, newTestServer(t, s), `{"query":"notice period?"}`, testKey)

	if rec.Code != http.StatusServiceUnavailable {
		t.Fatalf("status = %d, want 503 (body=%s)", rec.Code, rec.Body.String())
	}
}

func TestEmptyRetrievalAbstains(t *testing.T) {
	s := healthyStubs()
	s.retrieval = jsonHandler(contract.RetrieveResponse{Chunks: nil, K: 24})
	rec := post(t, newTestServer(t, s), `{"query":"something not in the corpus"}`, testKey)

	resp := decode(t, rec)
	if resp.Decision != contract.DecisionAbstain || resp.AbstainReason != contract.ReasonOutOfCorpus {
		t.Fatalf("got %q/%q, want abstain/out_of_corpus", resp.Decision, resp.AbstainReason)
	}
	// Nothing failed here — the corpus genuinely lacks an answer.
	if resp.Degraded {
		t.Error("degraded = true for an honest out-of-corpus abstention")
	}
}

func TestAuthAndValidation(t *testing.T) {
	h := newTestServer(t, healthyStubs())

	cases := []struct {
		name string
		body string
		key  string
		want int
	}{
		{"no token", `{"query":"x"}`, "", http.StatusUnauthorized},
		{"wrong token", `{"query":"x"}`, "nope", http.StatusUnauthorized},
		{"empty query", `{"query":"   "}`, testKey, http.StatusBadRequest},
		{"bad mode", `{"query":"x","mode":"yolo"}`, testKey, http.StatusBadRequest},
		{"budget too high", `{"query":"x","risk_budget":0.9}`, testKey, http.StatusBadRequest},
		{"budget too low", `{"query":"x","risk_budget":0}`, testKey, http.StatusBadRequest},
		{"unknown field", `{"query":"x","temperature":0.7}`, testKey, http.StatusBadRequest},
		{"malformed json", `{"query":`, testKey, http.StatusBadRequest},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			if got := post(t, h, tc.body, tc.key).Code; got != tc.want {
				t.Errorf("status = %d, want %d", got, tc.want)
			}
		})
	}
}

func TestStreamingEmitsProvisionalThenDecision(t *testing.T) {
	h := newTestServer(t, healthyStubs())
	req := httptest.NewRequest(http.MethodPost, "/v1/answer",
		strings.NewReader(`{"query":"notice period?","stream":true}`))
	req.Header.Set("Authorization", "Bearer "+testKey)
	rec := httptest.NewRecorder()
	h.ServeHTTP(rec, req)

	if ct := rec.Header().Get("Content-Type"); !strings.HasPrefix(ct, "text/event-stream") {
		t.Fatalf("content-type = %q, want text/event-stream", ct)
	}
	body := rec.Body.String()
	wantOrder := []string{
		"event: " + pipeline.EventRetrieval,
		"event: " + pipeline.EventProvisional,
		"event: " + pipeline.EventDecision,
		"event: " + pipeline.EventDone,
	}
	pos := -1
	for _, want := range wantOrder {
		i := strings.Index(body, want)
		if i < 0 {
			t.Fatalf("missing %q in stream:\n%s", want, body)
		}
		if i <= pos {
			t.Fatalf("%q arrived out of order:\n%s", want, body)
		}
		pos = i
	}
}

func TestHealthAndReadiness(t *testing.T) {
	h := newTestServer(t, healthyStubs())

	rec := httptest.NewRecorder()
	h.ServeHTTP(rec, httptest.NewRequest(http.MethodGet, "/healthz", nil))
	if rec.Code != http.StatusOK {
		t.Errorf("healthz = %d, want 200", rec.Code)
	}

	rec = httptest.NewRecorder()
	h.ServeHTTP(rec, httptest.NewRequest(http.MethodGet, "/readyz", nil))
	if rec.Code != http.StatusOK {
		t.Errorf("readyz = %d, want 200 (body=%s)", rec.Code, rec.Body.String())
	}

	// Liveness must not depend on upstreams, or a slow verifier gets us killed.
	s := healthyStubs()
	s.verifier = nil
	down := newTestServer(t, s)

	rec = httptest.NewRecorder()
	down.ServeHTTP(rec, httptest.NewRequest(http.MethodGet, "/healthz", nil))
	if rec.Code != http.StatusOK {
		t.Errorf("healthz with verifier down = %d, want 200", rec.Code)
	}
	rec = httptest.NewRecorder()
	down.ServeHTTP(rec, httptest.NewRequest(http.MethodGet, "/readyz", nil))
	if rec.Code != http.StatusServiceUnavailable {
		t.Errorf("readyz with verifier down = %d, want 503", rec.Code)
	}
}

func TestMetricsExposition(t *testing.T) {
	h := newTestServer(t, healthyStubs())
	post(t, h, `{"query":"notice period?"}`, testKey)

	rec := httptest.NewRecorder()
	h.ServeHTTP(rec, httptest.NewRequest(http.MethodGet, "/metrics", nil))
	body := rec.Body.String()

	for _, want := range []string{
		obs.MetricDecisions,
		obs.MetricStageLatency + "_bucket",
		`le="+Inf"`,
	} {
		if !strings.Contains(body, want) {
			t.Errorf("metrics output missing %q:\n%s", want, body)
		}
	}
}

func TestDeadlinePropagation(t *testing.T) {
	var got string
	s := healthyStubs()
	inner := s.retrieval
	s.retrieval = func(w http.ResponseWriter, r *http.Request) {
		got = r.Header.Get(upstream.HeaderDeadline)
		inner(w, r)
	}
	post(t, newTestServer(t, s), `{"query":"x"}`, testKey)

	if got == "" {
		t.Fatal("upstream call carried no deadline header")
	}
}

func TestBodySizeLimit(t *testing.T) {
	h := newTestServer(t, healthyStubs())
	huge := `{"query":"` + strings.Repeat("a", 200<<10) + `"}`
	if code := post(t, h, huge, testKey).Code; code != http.StatusBadRequest {
		t.Errorf("status = %d, want 400 for an oversized body", code)
	}
}

// --- Admission control ------------------------------------------------------

// newLimitedServer builds a server with a real, tight budget so the throttling
// paths can be exercised.
func newLimitedServer(t *testing.T, limits config.Config) http.Handler {
	t.Helper()

	mux := http.NewServeMux()
	mux.HandleFunc("GET /healthz", func(w http.ResponseWriter, r *http.Request) {})
	mux.Handle("/", jsonHandler(contract.RetrieveResponse{}))
	srv := httptest.NewServer(mux)
	t.Cleanup(srv.Close)

	cfg := limits
	cfg.RequestTimeout = 3 * time.Second
	cfg.RetrievalK = 24
	cfg.MaxBodyBytes = 64 << 10

	stage := 2 * time.Second
	clients := []*upstream.Client{
		upstream.New("retrieval", srv.URL, stage),
		upstream.New("generation", srv.URL, stage),
		upstream.New("verifier", srv.URL, stage),
		upstream.New("risk", srv.URL, stage),
	}
	reg, err := tenant.NewRegistry("acme:" + testKey + ":0.05,beta:other-key:0.05")
	if err != nil {
		t.Fatal(err)
	}
	log := obs.NewLogger("error")
	metrics := obs.NewMetrics()
	p := pipeline.New(clients[0], clients[1], clients[2], clients[3], cfg.RetrievalK, log, metrics)
	return NewServer(&cfg, reg, p, log, metrics, clients).Handler()
}

func TestRateLimitReturns429WithRetryAfter(t *testing.T) {
	h := newLimitedServer(t, config.Config{RateLimitPerSecond: 1, RateLimitBurst: 2})

	for i := 0; i < 2; i++ {
		if code := post(t, h, `{"query":"x"}`, testKey).Code; code == http.StatusTooManyRequests {
			t.Fatalf("request %d was throttled inside the burst", i)
		}
	}

	rec := post(t, h, `{"query":"x"}`, testKey)
	if rec.Code != http.StatusTooManyRequests {
		t.Fatalf("status = %d, want 429", rec.Code)
	}
	if rec.Header().Get("Retry-After") == "" {
		t.Error("no Retry-After header; the client has nothing to back off by")
	}
	if !strings.Contains(rec.Body.String(), "rate_limited") {
		t.Errorf("body does not name the reason: %s", rec.Body.String())
	}
}

func TestOneTenantCannotExhaustAnother(t *testing.T) {
	// The point of per-tenant admission control: a timed-out verifier becomes an
	// abstention, so a noisy neighbour would silently degrade other tenants'
	// answers, not merely slow them down.
	h := newLimitedServer(t, config.Config{RateLimitPerSecond: 1, RateLimitBurst: 1})

	post(t, h, `{"query":"x"}`, testKey)
	if code := post(t, h, `{"query":"x"}`, testKey).Code; code != http.StatusTooManyRequests {
		t.Fatalf("noisy tenant = %d, want 429", code)
	}

	if code := post(t, h, `{"query":"x"}`, "other-key").Code; code == http.StatusTooManyRequests {
		t.Error("the second tenant was throttled by the first tenant's traffic")
	}
}

func TestThrottlingHappensAfterAuthentication(t *testing.T) {
	// Otherwise an unauthenticated flood consumes a real tenant's budget.
	h := newLimitedServer(t, config.Config{RateLimitPerSecond: 1, RateLimitBurst: 1})

	for i := 0; i < 5; i++ {
		if code := post(t, h, `{"query":"x"}`, "not-a-key").Code; code != http.StatusUnauthorized {
			t.Fatalf("unauthenticated request = %d, want 401", code)
		}
	}
	if code := post(t, h, `{"query":"x"}`, testKey).Code; code == http.StatusTooManyRequests {
		t.Error("an unauthenticated flood consumed the real tenant's budget")
	}
}

func TestConcurrencyLimitIsReportedDistinctly(t *testing.T) {
	// A client sending too fast and one holding too many connections open need
	// different fixes, so a single opaque 429 is not enough.
	h := newLimitedServer(t, config.Config{
		RateLimitPerSecond: 1e6, RateLimitBurst: 1e6, MaxConcurrentPerTenant: 1,
	})

	release := make(chan struct{})
	done := make(chan struct{})
	go func() {
		defer close(done)
		req := httptest.NewRequest(http.MethodPost, "/v1/answer", &blockingBody{release: release})
		req.Header.Set("Authorization", "Bearer "+testKey)
		req.Header.Set("Content-Type", "application/json")
		h.ServeHTTP(httptest.NewRecorder(), req)
	}()

	// Wait for the in-flight request to have taken its slot.
	time.Sleep(50 * time.Millisecond)
	rec := post(t, h, `{"query":"x"}`, testKey)
	close(release)
	<-done

	if rec.Code != http.StatusTooManyRequests {
		t.Fatalf("status = %d, want 429", rec.Code)
	}
	if !strings.Contains(rec.Body.String(), "too_many_concurrent_requests") {
		t.Errorf("reason is not distinguishable from a rate limit: %s", rec.Body.String())
	}
}

// blockingBody holds a request open until released, so a concurrency slot stays
// occupied for the duration of the test.
type blockingBody struct {
	release chan struct{}
	done    bool
}

func (b *blockingBody) Read(p []byte) (int, error) {
	if b.done {
		return 0, io.EOF
	}
	<-b.release
	b.done = true
	return copy(p, `{"query":"x"}`), nil
}
