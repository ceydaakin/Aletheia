// Command loadtest measures end-to-end latency against a running gateway.
//
//	go run ./cmd/loadtest -url http://localhost:8080 -key demo-key-change-me -c 20 -n 400
//
// The PRD's numbers to hit: p50 ≤ 1.8 s, p95 ≤ 3.5 s with the verifier in the
// path, and ≥ 20 concurrent requests on one node (§4.2).
//
// Two things this deliberately does *not* do. It does not discard abstentions:
// an abstention is a real response with a real cost, and a system under load is
// more likely to produce them, so excluding them would flatter exactly the case
// that matters. And it does not report a mean — a mean latency hides the tail
// that the SLO is written about.
package main

import (
	"bytes"
	"context"
	"encoding/json"
	"flag"
	"fmt"
	"io"
	"net/http"
	"os"
	"sort"
	"sync"
	"time"
)

type result struct {
	duration time.Duration
	status   int
	decision string
	err      error
}

func main() {
	var (
		url         = flag.String("url", "http://localhost:8080", "gateway base URL")
		key         = flag.String("key", "demo-key-change-me", "tenant API key")
		query       = flag.String("query", "termination written notice period", "query to send")
		concurrency = flag.Int("c", 20, "concurrent requests")
		total       = flag.Int("n", 400, "total requests")
		warmup      = flag.Int("warmup", 20, "requests to discard before measuring")
		timeout     = flag.Duration("timeout", 30*time.Second, "per-request timeout")
	)
	flag.Parse()

	client := &http.Client{
		Timeout: *timeout,
		Transport: &http.Transport{
			// Must exceed concurrency, or the client itself becomes the
			// bottleneck and the measurement is of connection queueing rather
			// than of the system under test.
			MaxIdleConnsPerHost: *concurrency * 2,
			MaxConnsPerHost:     *concurrency * 2,
		},
	}

	body, _ := json.Marshal(map[string]any{"query": *query, "mode": "strict"})

	if *warmup > 0 {
		fmt.Fprintf(os.Stderr, "warming up (%d requests)...\n", *warmup)
		run(client, *url, *key, body, *warmup, min(*concurrency, *warmup))
	}

	fmt.Fprintf(os.Stderr, "measuring: n=%d concurrency=%d\n", *total, *concurrency)
	started := time.Now()
	results := run(client, *url, *key, body, *total, *concurrency)
	wall := time.Since(started)

	report(results, wall, *concurrency)
}

func run(client *http.Client, url, key string, body []byte, total, concurrency int) []result {
	jobs := make(chan int, total)
	for i := 0; i < total; i++ {
		jobs <- i
	}
	close(jobs)

	results := make([]result, 0, total)
	var mu sync.Mutex
	var wg sync.WaitGroup

	for w := 0; w < concurrency; w++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			for range jobs {
				r := one(client, url, key, body)
				mu.Lock()
				results = append(results, r)
				mu.Unlock()
			}
		}()
	}
	wg.Wait()
	return results
}

func one(client *http.Client, url, key string, body []byte) result {
	started := time.Now()

	req, err := http.NewRequestWithContext(
		context.Background(), http.MethodPost, url+"/v1/answer", bytes.NewReader(body),
	)
	if err != nil {
		return result{err: err}
	}
	req.Header.Set("Authorization", "Bearer "+key)
	req.Header.Set("Content-Type", "application/json")

	resp, err := client.Do(req)
	if err != nil {
		return result{duration: time.Since(started), err: err}
	}
	defer resp.Body.Close()

	// Read the whole body before stopping the clock: latency the client has not
	// finished receiving is latency the user is still waiting on.
	payload, _ := io.ReadAll(resp.Body)
	elapsed := time.Since(started)

	var decoded struct {
		Decision string `json:"decision"`
	}
	_ = json.Unmarshal(payload, &decoded)

	return result{duration: elapsed, status: resp.StatusCode, decision: decoded.Decision}
}

func report(results []result, wall time.Duration, concurrency int) {
	durations := make([]time.Duration, 0, len(results))
	statuses := map[int]int{}
	decisions := map[string]int{}
	failures := 0

	for _, r := range results {
		if r.err != nil {
			failures++
			continue
		}
		statuses[r.status]++
		if r.decision != "" {
			decisions[r.decision]++
		}
		// Only served requests contribute to latency. A 429 is answered in
		// microseconds because nothing happened, so mixing them in reports the
		// speed of the rejection path and calls it the SLO — the first run of
		// this tool "passed" at 5 ms while the gateway turned away 97% of the
		// load.
		if r.status >= 200 && r.status < 300 {
			durations = append(durations, r.duration)
		}
	}

	if len(durations) == 0 {
		fmt.Printf("no served requests (%d transport failures, %d responses)\n", failures, len(results))
		fmt.Printf("status: %v\n", statuses)
		os.Exit(1)
	}
	sort.Slice(durations, func(i, j int) bool { return durations[i] < durations[j] })

	served := float64(len(durations)) / float64(len(results))
	fmt.Printf("\nrequests            %d in %s (concurrency %d)\n", len(results), wall.Round(time.Millisecond), concurrency)
	fmt.Printf("served              %d (%.0f%%)\n", len(durations), served*100)
	fmt.Printf("throughput          %.1f req/s served\n", float64(len(durations))/wall.Seconds())
	if failures > 0 {
		fmt.Printf("transport failures  %d\n", failures)
	}

	fmt.Printf("\nlatency\n")
	for _, p := range []struct {
		label string
		q     float64
	}{{"p50", 0.50}, {"p90", 0.90}, {"p95", 0.95}, {"p99", 0.99}, {"max", 1.0}} {
		fmt.Printf("  %-4s %8s\n", p.label, quantile(durations, p.q).Round(time.Millisecond))
	}

	fmt.Printf("\nstatus\n")
	for _, code := range sortedKeys(statuses) {
		fmt.Printf("  %-4d %8d\n", code, statuses[code])
	}
	if len(decisions) > 0 {
		fmt.Printf("\ndecision\n")
		for _, d := range sortedStringKeys(decisions) {
			fmt.Printf("  %-18s %6d\n", d, decisions[d])
		}
	}

	// The SLO check, stated as pass/fail rather than left to the reader.
	p50, p95 := quantile(durations, 0.50), quantile(durations, 0.95)
	fmt.Printf("\nPRD §4.2 targets\n")
	fmt.Printf("  p50 <= 1.8s   %-8s %s\n", verdict(p50 <= 1800*time.Millisecond), p50.Round(time.Millisecond))
	fmt.Printf("  p95 <= 3.5s   %-8s %s\n", verdict(p95 <= 3500*time.Millisecond), p95.Round(time.Millisecond))

	// A latency result measured over a small fraction of the offered load says
	// nothing about the system's behaviour at that load. Refusing to certify is
	// the same discipline the risk controller applies to its own bound.
	if served < 0.9 {
		fmt.Printf("\n  INVALID: only %.0f%% of requests were served; the rest were rejected\n", served*100)
		fmt.Printf("  before doing any work. These percentiles describe the reject path,\n")
		fmt.Printf("  not the pipeline. Raise GATEWAY_RATE_LIMIT_PER_SECOND above the\n")
		fmt.Printf("  offered rate, or lower -c, and measure again.\n")
		os.Exit(1)
	}
}

// quantile uses nearest-rank on the sorted sample. No interpolation: with a few
// hundred samples, interpolating between two measured values invents a number
// that was never observed.
func quantile(sorted []time.Duration, q float64) time.Duration {
	if len(sorted) == 0 {
		return 0
	}
	index := int(q * float64(len(sorted)))
	if index >= len(sorted) {
		index = len(sorted) - 1
	}
	return sorted[index]
}

func verdict(ok bool) string {
	if ok {
		return "PASS"
	}
	return "FAIL"
}

func sortedKeys(m map[int]int) []int {
	keys := make([]int, 0, len(m))
	for k := range m {
		keys = append(keys, k)
	}
	sort.Ints(keys)
	return keys
}

func sortedStringKeys(m map[string]int) []string {
	keys := make([]string, 0, len(m))
	for k := range m {
		keys = append(keys, k)
	}
	sort.Strings(keys)
	return keys
}
