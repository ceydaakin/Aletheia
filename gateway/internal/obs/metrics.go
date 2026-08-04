// Package obs holds the gateway's observability primitives: trace ids, a
// structured logger, and a minimal Prometheus-compatible metrics registry.
//
// The registry is hand-rolled rather than pulled from client_golang because the
// gateway is deliberately dependency-free (ADR-0001) and the exposition format
// for counters and histograms is a few dozen lines. If we ever need exemplars or
// native histograms, that trade flips and we take the dependency.
package obs

import (
	"fmt"
	"io"
	"sort"
	"strconv"
	"strings"
	"sync"
)

// latencyBuckets are chosen around the NFR targets: p50 ≤ 1.8s, p95 ≤ 3.5s.
// The buckets straddling those two numbers are the ones we actually watch.
var latencyBuckets = []float64{0.05, 0.1, 0.25, 0.5, 1.0, 1.8, 2.5, 3.5, 5.0, 10.0}

type seriesKey struct {
	name   string
	labels string
}

type histogram struct {
	counts []uint64
	sum    float64
	total  uint64
}

type Metrics struct {
	mu       sync.Mutex
	counters map[seriesKey]uint64
	hists    map[seriesKey]*histogram
}

func NewMetrics() *Metrics {
	return &Metrics{
		counters: make(map[seriesKey]uint64),
		hists:    make(map[seriesKey]*histogram),
	}
}

// Inc bumps a counter. labels are alternating key/value pairs.
func (m *Metrics) Inc(name string, labels ...string) {
	k := seriesKey{name, formatLabels(labels)}
	m.mu.Lock()
	m.counters[k]++
	m.mu.Unlock()
}

// Observe records a value (seconds) into a histogram.
func (m *Metrics) Observe(name string, v float64, labels ...string) {
	k := seriesKey{name, formatLabels(labels)}
	m.mu.Lock()
	defer m.mu.Unlock()
	h, ok := m.hists[k]
	if !ok {
		h = &histogram{counts: make([]uint64, len(latencyBuckets))}
		m.hists[k] = h
	}
	for i, b := range latencyBuckets {
		if v <= b {
			h.counts[i]++
		}
	}
	h.sum += v
	h.total++
}

// Render writes the Prometheus text exposition format.
func (m *Metrics) Render(w io.Writer) {
	m.mu.Lock()
	counters := make(map[seriesKey]uint64, len(m.counters))
	for k, v := range m.counters {
		counters[k] = v
	}
	hists := make(map[seriesKey]histogram, len(m.hists))
	for k, h := range m.hists {
		cp := histogram{counts: append([]uint64(nil), h.counts...), sum: h.sum, total: h.total}
		hists[k] = cp
	}
	m.mu.Unlock()

	for _, k := range sortedKeys(counters) {
		fmt.Fprintf(w, "%s%s %d\n", k.name, k.labels, counters[k])
	}
	for _, k := range sortedHistKeys(hists) {
		h := hists[k]
		// counts are already cumulative: Observe increments every bucket whose
		// upper bound the value falls under, which is what `le` means.
		for i, b := range latencyBuckets {
			fmt.Fprintf(w, "%s_bucket%s %d\n", k.name, withLabel(k.labels, "le", strconv.FormatFloat(b, 'g', -1, 64)), h.counts[i])
		}
		fmt.Fprintf(w, "%s_bucket%s %d\n", k.name, withLabel(k.labels, "le", "+Inf"), h.total)
		fmt.Fprintf(w, "%s_sum%s %g\n", k.name, k.labels, h.sum)
		fmt.Fprintf(w, "%s_count%s %d\n", k.name, k.labels, h.total)
	}
}

func formatLabels(kv []string) string {
	if len(kv) == 0 {
		return ""
	}
	if len(kv)%2 != 0 {
		kv = kv[:len(kv)-1]
	}
	parts := make([]string, 0, len(kv)/2)
	for i := 0; i < len(kv); i += 2 {
		parts = append(parts, fmt.Sprintf("%s=%q", kv[i], kv[i+1]))
	}
	sort.Strings(parts)
	return "{" + strings.Join(parts, ",") + "}"
}

func withLabel(labels, key, value string) string {
	pair := fmt.Sprintf("%s=%q", key, value)
	if labels == "" {
		return "{" + pair + "}"
	}
	return labels[:len(labels)-1] + "," + pair + "}"
}

func sortedKeys(m map[seriesKey]uint64) []seriesKey {
	keys := make([]seriesKey, 0, len(m))
	for k := range m {
		keys = append(keys, k)
	}
	sortKeys(keys)
	return keys
}

func sortedHistKeys(m map[seriesKey]histogram) []seriesKey {
	keys := make([]seriesKey, 0, len(m))
	for k := range m {
		keys = append(keys, k)
	}
	sortKeys(keys)
	return keys
}

func sortKeys(keys []seriesKey) {
	sort.Slice(keys, func(i, j int) bool {
		if keys[i].name != keys[j].name {
			return keys[i].name < keys[j].name
		}
		return keys[i].labels < keys[j].labels
	})
}

// Metric names used across the gateway.
const (
	MetricRequests      = "aletheia_gateway_requests_total"
	MetricDecisions     = "aletheia_gateway_decisions_total"
	MetricAbstentions   = "aletheia_gateway_abstentions_total"
	MetricStageErrors   = "aletheia_gateway_stage_errors_total"
	MetricRequestLatncy = "aletheia_gateway_request_duration_seconds"
	MetricStageLatency  = "aletheia_gateway_stage_duration_seconds"
)
