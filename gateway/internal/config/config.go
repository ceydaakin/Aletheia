// Package config loads gateway configuration from the environment. There is no
// config file: the gateway runs in containers, and twelve-factor beats a parser.
package config

import (
	"fmt"
	"os"
	"strconv"
	"time"
)

// Upstream is one Python service the gateway talks to.
type Upstream struct {
	Name    string
	URL     string
	Timeout time.Duration
}

type Config struct {
	Addr     string
	LogLevel string

	// RequestTimeout is the wall-clock budget for one /v1/answer. Every stage
	// deadline is derived from what remains of it, never from a fixed value, so
	// a slow retrieval eats into generation rather than blowing the total.
	RequestTimeout time.Duration

	Retrieval  Upstream
	Generation Upstream
	Verifier   Upstream
	Risk       Upstream

	// TenantSpec is the static tenant registry (scaffold only; see ADR-0001).
	TenantSpec        string
	DefaultRiskBudget float64

	// RetrievalK is how many chunks to ask retrieval for before reranking.
	RetrievalK int

	MaxBodyBytes int64

	// Per-tenant admission control. Without it one tenant's batch job consumes
	// the verifier's throughput and every other tenant's requests time out —
	// and a timed-out verifier becomes an abstention, so the noisy neighbour
	// silently degrades other tenants' answers rather than just slowing them.
	RateLimitPerSecond     float64
	RateLimitBurst         int
	MaxConcurrentPerTenant int
}

func Load() (*Config, error) {
	c := &Config{
		Addr:               env("GATEWAY_ADDR", ":8080"),
		LogLevel:           env("GATEWAY_LOG_LEVEL", "info"),
		RequestTimeout:     envDuration("GATEWAY_REQUEST_TIMEOUT_MS", 6000),
		TenantSpec:         env("ALETHEIA_TENANTS", "demo:demo-key-change-me:0.05"),
		DefaultRiskBudget:  envFloat("DEFAULT_RISK_BUDGET", 0.05),
		RetrievalK:         envInt("GATEWAY_RETRIEVAL_K", 24),
		MaxBodyBytes:       int64(envInt("GATEWAY_MAX_BODY_BYTES", 64<<10)),
		RateLimitPerSecond: envFloat("GATEWAY_RATE_LIMIT_PER_SECOND", 10),
		RateLimitBurst:     envInt("GATEWAY_RATE_LIMIT_BURST", 20),
		// Defaults to the NFR throughput target: >= 20 concurrent requests on one
		// node (PRD §4.2). Per tenant, so the total is higher with many tenants —
		// this bounds the blast radius of one, not the node.
		MaxConcurrentPerTenant: envInt("GATEWAY_MAX_CONCURRENT_PER_TENANT", 20),
		Retrieval: Upstream{
			Name:    "retrieval",
			URL:     env("RETRIEVAL_URL", "http://retrieval:8001"),
			Timeout: envDuration("GATEWAY_RETRIEVAL_TIMEOUT_MS", 1200),
		},
		Generation: Upstream{
			Name:    "generation",
			URL:     env("GENERATION_URL", "http://generation:8002"),
			Timeout: envDuration("GATEWAY_GENERATION_TIMEOUT_MS", 3000),
		},
		Verifier: Upstream{
			Name:    "verifier",
			URL:     env("VERIFIER_URL", "http://verifier:8003"),
			Timeout: envDuration("GATEWAY_VERIFIER_TIMEOUT_MS", 1500),
		},
		Risk: Upstream{
			Name:    "risk",
			URL:     env("RISK_URL", "http://risk:8004"),
			Timeout: envDuration("GATEWAY_RISK_TIMEOUT_MS", 500),
		},
	}
	return c, c.validate()
}

func (c *Config) validate() error {
	if c.RequestTimeout <= 0 {
		return fmt.Errorf("GATEWAY_REQUEST_TIMEOUT_MS must be positive")
	}
	// A total budget smaller than the sum of stage budgets is legal — stages are
	// clamped to what remains — but the reverse of the intent is usually a typo.
	sum := c.Retrieval.Timeout + c.Generation.Timeout + c.Verifier.Timeout + c.Risk.Timeout
	if sum < c.RequestTimeout/2 {
		return fmt.Errorf("stage timeouts (%s) are far below the request budget (%s); check configuration", sum, c.RequestTimeout)
	}
	if c.RetrievalK <= 0 {
		return fmt.Errorf("GATEWAY_RETRIEVAL_K must be positive")
	}
	return nil
}

func env(key, def string) string {
	if v, ok := os.LookupEnv(key); ok && v != "" {
		return v
	}
	return def
}

func envInt(key string, def int) int {
	if v, ok := os.LookupEnv(key); ok && v != "" {
		if n, err := strconv.Atoi(v); err == nil {
			return n
		}
	}
	return def
}

func envFloat(key string, def float64) float64 {
	if v, ok := os.LookupEnv(key); ok && v != "" {
		if f, err := strconv.ParseFloat(v, 64); err == nil {
			return f
		}
	}
	return def
}

func envDuration(key string, defMS int) time.Duration {
	return time.Duration(envInt(key, defMS)) * time.Millisecond
}
