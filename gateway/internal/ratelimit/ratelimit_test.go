package ratelimit

import (
	"sync"
	"testing"
	"time"
)

func fixed(l *Limiter, at *time.Time) {
	l.now = func() time.Time { return *at }
}

func TestBurstIsAdmittedThenRefused(t *testing.T) {
	now := time.Now()
	l := New(Limits{RequestsPerSecond: 1, Burst: 3})
	fixed(l, &now)

	for i := 0; i < 3; i++ {
		outcome, release := l.Acquire("t")
		if outcome != Allowed {
			t.Fatalf("request %d refused: %v", i, outcome)
		}
		release()
	}

	if outcome, _ := l.Acquire("t"); outcome != RateLimited {
		t.Errorf("fourth request = %v, want RateLimited", outcome)
	}
}

func TestTokensRefillOverTime(t *testing.T) {
	now := time.Now()
	l := New(Limits{RequestsPerSecond: 2, Burst: 1})
	fixed(l, &now)

	_, release := l.Acquire("t")
	release()
	if outcome, _ := l.Acquire("t"); outcome != RateLimited {
		t.Fatal("bucket should be empty")
	}

	now = now.Add(500 * time.Millisecond) // exactly one token at 2/s
	if outcome, _ := l.Acquire("t"); outcome != Allowed {
		t.Error("a refilled token was not honoured")
	}
}

func TestIdleTenantsCannotBankUnlimitedCredit(t *testing.T) {
	now := time.Now()
	l := New(Limits{RequestsPerSecond: 10, Burst: 2})
	fixed(l, &now)

	now = now.Add(time.Hour)

	allowed := 0
	for i := 0; i < 10; i++ {
		if outcome, release := l.Acquire("t"); outcome == Allowed {
			allowed++
			release()
		}
	}
	if allowed != 2 {
		t.Errorf("admitted %d after an idle hour, want the burst depth 2", allowed)
	}
}

func TestTenantsHaveSeparateBuckets(t *testing.T) {
	now := time.Now()
	l := New(Limits{RequestsPerSecond: 1, Burst: 1})
	fixed(l, &now)

	if outcome, release := l.Acquire("noisy"); outcome == Allowed {
		release()
	}
	if outcome, _ := l.Acquire("noisy"); outcome != RateLimited {
		t.Fatal("expected the noisy tenant to be limited")
	}

	// The whole point: one tenant exhausting its budget must not affect another.
	if outcome, _ := l.Acquire("quiet"); outcome != Allowed {
		t.Errorf("quiet tenant = %v, want Allowed", outcome)
	}
}

func TestConcurrencyCapIsIndependentOfRate(t *testing.T) {
	now := time.Now()
	l := New(Limits{RequestsPerSecond: 1000, Burst: 1000, MaxConcurrent: 2})
	fixed(l, &now)

	_, first := l.Acquire("t")
	_, second := l.Acquire("t")

	// Plenty of tokens left, but both slots are held.
	if outcome, _ := l.Acquire("t"); outcome != ConcurrencyLimited {
		t.Errorf("third concurrent request = %v, want ConcurrencyLimited", outcome)
	}

	first()
	if outcome, release := l.Acquire("t"); outcome != Allowed {
		t.Errorf("after a release = %v, want Allowed", outcome)
	} else {
		release()
	}
	second()
}

func TestReleaseIsIdempotentEnoughToNotGoNegative(t *testing.T) {
	l := New(Limits{RequestsPerSecond: 10, Burst: 10, MaxConcurrent: 1})
	_, release := l.Acquire("t")
	release()
	release()

	if got := l.InFlight("t"); got != 0 {
		t.Errorf("InFlight = %d, want 0", got)
	}
	if outcome, _ := l.Acquire("t"); outcome != Allowed {
		t.Error("a double release corrupted the slot count")
	}
}

func TestRetryAfterIsZeroWhenTokensRemain(t *testing.T) {
	l := New(Limits{RequestsPerSecond: 1, Burst: 5})
	if got := l.RetryAfter("t"); got != 0 {
		t.Errorf("RetryAfter = %v for an unknown tenant, want 0", got)
	}
}

func TestRetryAfterEstimatesTheWait(t *testing.T) {
	now := time.Now()
	l := New(Limits{RequestsPerSecond: 2, Burst: 1})
	fixed(l, &now)

	_, release := l.Acquire("t")
	release()

	got := l.RetryAfter("t")
	if got < 400*time.Millisecond || got > 600*time.Millisecond {
		t.Errorf("RetryAfter = %v, want about 500ms at 2 req/s", got)
	}
}

func TestConcurrentAccessIsSafe(t *testing.T) {
	l := New(Limits{RequestsPerSecond: 1e6, Burst: 1e6, MaxConcurrent: 8})

	var wg sync.WaitGroup
	for i := 0; i < 200; i++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			if outcome, release := l.Acquire("t"); outcome == Allowed {
				release()
			}
		}()
	}
	wg.Wait()

	if got := l.InFlight("t"); got != 0 {
		t.Errorf("InFlight = %d after all releases, want 0", got)
	}
}
