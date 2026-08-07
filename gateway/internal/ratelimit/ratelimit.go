// Package ratelimit provides per-tenant admission control.
//
// ADR-0002 gave tenants data isolation. This gives them isolation of *load*,
// which is the other half of multi-tenancy: without it one tenant replaying a
// batch job consumes the whole verifier's throughput and every other tenant's
// requests time out — and a timed-out verifier becomes an abstention, so the
// noisy neighbour does not just slow other tenants down, it silently degrades
// their answers.
//
// Two independent limits, because they fail differently:
//
//   - A token bucket bounds the *rate*, smoothing bursts over time.
//   - A concurrency cap bounds simultaneous in-flight work, which is what
//     actually protects the model services — they are slow and their cost is
//     per-request, so ten concurrent requests hurt regardless of the rate that
//     produced them.
package ratelimit

import (
	"sync"
	"time"
)

// Limits describes one tenant's budget.
type Limits struct {
	// RequestsPerSecond is the sustained refill rate.
	RequestsPerSecond float64
	// Burst is the bucket depth: how many requests may arrive at once.
	Burst int
	// MaxConcurrent bounds simultaneous in-flight requests. Zero means unlimited.
	MaxConcurrent int
}

// bucket is a token bucket that refills lazily.
//
// Lazy refill rather than a background ticker: a ticker per tenant would cost a
// goroutine per tenant and keep waking up for tenants that send nothing.
type bucket struct {
	tokens     float64
	lastRefill time.Time
	inFlight   int
}

type Limiter struct {
	mu      sync.Mutex
	buckets map[string]*bucket
	limits  Limits
	now     func() time.Time
}

func New(limits Limits) *Limiter {
	return &Limiter{
		buckets: make(map[string]*bucket),
		limits:  limits,
		now:     time.Now,
	}
}

// Outcome explains an admission decision. Callers map these to status codes;
// keeping them distinct means a client can tell "slow down" from "too many at
// once", which need different fixes.
type Outcome int

const (
	Allowed Outcome = iota
	RateLimited
	ConcurrencyLimited
)

// Acquire admits a request. When it returns Allowed, the caller must call the
// returned release function exactly once — deferring it at the call site is the
// only safe pattern, because an early return that skips it leaks a concurrency
// slot permanently.
func (l *Limiter) Acquire(tenantID string) (Outcome, func()) {
	l.mu.Lock()
	defer l.mu.Unlock()

	now := l.now()
	b, ok := l.buckets[tenantID]
	if !ok {
		b = &bucket{tokens: float64(l.limits.Burst), lastRefill: now}
		l.buckets[tenantID] = b
	}

	// Refill for elapsed time, capped at the burst depth so an idle tenant
	// cannot bank unlimited credit and then flood.
	elapsed := now.Sub(b.lastRefill).Seconds()
	if elapsed > 0 {
		b.tokens = min(b.tokens+elapsed*l.limits.RequestsPerSecond, float64(l.limits.Burst))
		b.lastRefill = now
	}

	if l.limits.MaxConcurrent > 0 && b.inFlight >= l.limits.MaxConcurrent {
		return ConcurrencyLimited, nil
	}
	if b.tokens < 1 {
		return RateLimited, nil
	}

	b.tokens--
	b.inFlight++
	return Allowed, func() { l.release(tenantID) }
}

func (l *Limiter) release(tenantID string) {
	l.mu.Lock()
	defer l.mu.Unlock()
	if b, ok := l.buckets[tenantID]; ok && b.inFlight > 0 {
		b.inFlight--
	}
}

// RetryAfter estimates how long until a token is available, for the header of
// the same name. Approximate by design: an exact answer would need the request
// to hold the lock until it is admitted.
func (l *Limiter) RetryAfter(tenantID string) time.Duration {
	l.mu.Lock()
	defer l.mu.Unlock()

	b, ok := l.buckets[tenantID]
	if !ok || b.tokens >= 1 || l.limits.RequestsPerSecond <= 0 {
		return 0
	}
	seconds := (1 - b.tokens) / l.limits.RequestsPerSecond
	return time.Duration(seconds * float64(time.Second))
}

// InFlight reports current concurrency for a tenant. Exported for metrics.
func (l *Limiter) InFlight(tenantID string) int {
	l.mu.Lock()
	defer l.mu.Unlock()
	if b, ok := l.buckets[tenantID]; ok {
		return b.inFlight
	}
	return 0
}
