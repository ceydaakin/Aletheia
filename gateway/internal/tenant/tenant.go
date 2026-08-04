// Package tenant resolves an API key to a tenant and its risk defaults.
//
// The registry is currently static, parsed from ALETHEIA_TENANTS. It is replaced
// by a `tenants` table with hashed keys in week 8; the Registry interface below
// is the seam that makes that swap local.
package tenant

import (
	"crypto/subtle"
	"fmt"
	"strconv"
	"strings"
)

type Tenant struct {
	ID string
	// DefaultRiskBudget applies when a request omits risk_budget. Per-tenant
	// because a bank and a docs site do not share a tolerance for being wrong.
	DefaultRiskBudget float64
}

type Registry struct {
	// Keyed by API key. Small enough that a linear constant-time scan is fine
	// and avoids leaking key existence through map timing.
	entries []entry
}

type entry struct {
	key    string
	tenant Tenant
}

// NewRegistry parses a spec of the form
//
//	tenant_id:api_key:default_risk_budget[,tenant_id:api_key:budget...]
func NewRegistry(spec string) (*Registry, error) {
	r := &Registry{}
	for _, part := range strings.Split(spec, ",") {
		part = strings.TrimSpace(part)
		if part == "" {
			continue
		}
		fields := strings.Split(part, ":")
		if len(fields) != 3 {
			return nil, fmt.Errorf("tenant spec %q: want tenant_id:api_key:risk_budget", part)
		}
		budget, err := strconv.ParseFloat(fields[2], 64)
		if err != nil {
			return nil, fmt.Errorf("tenant spec %q: bad risk budget: %w", part, err)
		}
		if fields[0] == "" || fields[1] == "" {
			return nil, fmt.Errorf("tenant spec %q: tenant_id and api_key must be non-empty", part)
		}
		r.entries = append(r.entries, entry{
			key:    fields[1],
			tenant: Tenant{ID: fields[0], DefaultRiskBudget: budget},
		})
	}
	if len(r.entries) == 0 {
		return nil, fmt.Errorf("tenant registry is empty")
	}
	return r, nil
}

// Lookup resolves an API key. The scan does not short-circuit on the first match
// so that response time carries no information about which keys exist.
func (r *Registry) Lookup(key string) (Tenant, bool) {
	var found Tenant
	var ok bool
	for _, e := range r.entries {
		if subtle.ConstantTimeCompare([]byte(e.key), []byte(key)) == 1 {
			found, ok = e.tenant, true
		}
	}
	return found, ok
}

func (r *Registry) Len() int { return len(r.entries) }
