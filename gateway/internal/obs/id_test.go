package obs

import (
	"strings"
	"testing"
	"time"
)

func TestNewTraceIDFormat(t *testing.T) {
	seen := make(map[string]bool, 1000)
	for i := 0; i < 1000; i++ {
		id := NewTraceID()
		if len(id) != 26 {
			t.Fatalf("len = %d, want 26 (%q)", len(id), id)
		}
		for _, c := range id {
			if !strings.ContainsRune(crockford, c) {
				t.Fatalf("id %q contains %q, which is outside the Crockford alphabet", id, c)
			}
		}
		if seen[id] {
			t.Fatalf("duplicate trace id %q after %d draws", id, i)
		}
		seen[id] = true
	}
}

// Lexicographic order must track time order, or sorted log greps mislead.
func TestNewTraceIDIsMonotonicAcrossMilliseconds(t *testing.T) {
	first := NewTraceID()
	time.Sleep(2 * time.Millisecond)
	second := NewTraceID()
	if !(first < second) {
		t.Errorf("%q >= %q: ids are not time-ordered", first, second)
	}
}
