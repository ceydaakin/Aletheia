package tenant

import "testing"

func TestNewRegistry(t *testing.T) {
	r, err := NewRegistry("acme:key-a:0.05, beta:key-b:0.10")
	if err != nil {
		t.Fatalf("NewRegistry: %v", err)
	}
	if r.Len() != 2 {
		t.Fatalf("Len = %d, want 2", r.Len())
	}

	got, ok := r.Lookup("key-b")
	if !ok {
		t.Fatal("key-b not found")
	}
	if got.ID != "beta" || got.DefaultRiskBudget != 0.10 {
		t.Errorf("got %+v, want {beta 0.1}", got)
	}
	if _, ok := r.Lookup("key-c"); ok {
		t.Error("unknown key resolved to a tenant")
	}
	// An empty key must never match, even though it is a prefix of everything.
	if _, ok := r.Lookup(""); ok {
		t.Error("empty key resolved to a tenant")
	}
}

func TestNewRegistryRejectsBadSpecs(t *testing.T) {
	for _, spec := range []string{
		"",
		"acme:key",
		"acme:key:not-a-number",
		":key:0.05",
		"acme::0.05",
	} {
		if _, err := NewRegistry(spec); err == nil {
			t.Errorf("NewRegistry(%q) succeeded, want error", spec)
		}
	}
}
