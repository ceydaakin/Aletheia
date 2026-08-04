// Package upstream is the gateway's HTTP client for the Python services.
//
// Its one job beyond marshalling is deadline propagation: every call carries the
// remaining request budget in a header so the callee can fail fast instead of
// starting work that will be thrown away (ADR-0001).
package upstream

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net"
	"net/http"
	"time"

	"github.com/ceydaakin/aletheia/gateway/internal/obs"
)

// HeaderDeadline carries the milliseconds remaining before the gateway gives up.
const HeaderDeadline = "X-Aletheia-Deadline-Ms"

// HeaderTraceID propagates the trace id so service logs join up with gateway logs.
const HeaderTraceID = "X-Aletheia-Trace-Id"

// Error carries enough context to decide a fallback without string matching.
type Error struct {
	Service string
	Status  int
	Timeout bool
	Err     error
}

func (e *Error) Error() string {
	if e.Status != 0 {
		return fmt.Sprintf("%s: http %d: %v", e.Service, e.Status, e.Err)
	}
	return fmt.Sprintf("%s: %v", e.Service, e.Err)
}

func (e *Error) Unwrap() error { return e.Err }

type Client struct {
	name    string
	baseURL string
	timeout time.Duration
	http    *http.Client
}

func New(name, baseURL string, timeout time.Duration) *Client {
	return &Client{
		name:    name,
		baseURL: baseURL,
		timeout: timeout,
		http: &http.Client{
			// No client-level Timeout: the context is the single source of truth
			// for deadlines, and two competing timers is how you get confusing
			// cancellation bugs.
			Transport: &http.Transport{
				Proxy: http.ProxyFromEnvironment,
				DialContext: (&net.Dialer{
					Timeout:   2 * time.Second,
					KeepAlive: 30 * time.Second,
				}).DialContext,
				MaxIdleConns:        128,
				MaxIdleConnsPerHost: 32,
				IdleConnTimeout:     90 * time.Second,
			},
		},
	}
}

func (c *Client) Name() string { return c.name }

// Post calls the service, enforcing min(stage budget, remaining request budget).
// A stage never gets more time than the request has left.
func (c *Client) Post(ctx context.Context, path string, in, out any) error {
	budget := c.timeout
	if deadline, ok := ctx.Deadline(); ok {
		if remaining := time.Until(deadline); remaining < budget {
			budget = remaining
		}
	}
	if budget <= 0 {
		return &Error{Service: c.name, Timeout: true, Err: context.DeadlineExceeded}
	}

	ctx, cancel := context.WithTimeout(ctx, budget)
	defer cancel()

	body, err := json.Marshal(in)
	if err != nil {
		return &Error{Service: c.name, Err: fmt.Errorf("marshal request: %w", err)}
	}

	req, err := http.NewRequestWithContext(ctx, http.MethodPost, c.baseURL+path, bytes.NewReader(body))
	if err != nil {
		return &Error{Service: c.name, Err: err}
	}
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("Accept", "application/json")
	req.Header.Set(HeaderDeadline, fmt.Sprintf("%d", budget.Milliseconds()))
	if id := obs.TraceID(ctx); id != "" {
		req.Header.Set(HeaderTraceID, id)
	}

	resp, err := c.http.Do(req)
	if err != nil {
		return &Error{Service: c.name, Timeout: isTimeout(ctx, err), Err: err}
	}
	defer func() {
		// Drain before closing so the connection returns to the idle pool.
		_, _ = io.Copy(io.Discard, io.LimitReader(resp.Body, 64<<10))
		_ = resp.Body.Close()
	}()

	if resp.StatusCode < 200 || resp.StatusCode >= 300 {
		snippet, _ := io.ReadAll(io.LimitReader(resp.Body, 2048))
		return &Error{
			Service: c.name,
			Status:  resp.StatusCode,
			Err:     fmt.Errorf("%s", bytes.TrimSpace(snippet)),
		}
	}

	if out == nil {
		return nil
	}
	if err := json.NewDecoder(resp.Body).Decode(out); err != nil {
		return &Error{Service: c.name, Status: resp.StatusCode, Err: fmt.Errorf("decode response: %w", err)}
	}
	return nil
}

// Health does a GET /healthz, used by the gateway's readiness probe.
func (c *Client) Health(ctx context.Context) error {
	ctx, cancel := context.WithTimeout(ctx, 1*time.Second)
	defer cancel()

	req, err := http.NewRequestWithContext(ctx, http.MethodGet, c.baseURL+"/healthz", nil)
	if err != nil {
		return &Error{Service: c.name, Err: err}
	}
	resp, err := c.http.Do(req)
	if err != nil {
		return &Error{Service: c.name, Timeout: isTimeout(ctx, err), Err: err}
	}
	defer func() {
		_, _ = io.Copy(io.Discard, io.LimitReader(resp.Body, 4<<10))
		_ = resp.Body.Close()
	}()
	if resp.StatusCode != http.StatusOK {
		return &Error{Service: c.name, Status: resp.StatusCode, Err: fmt.Errorf("not healthy")}
	}
	return nil
}

func isTimeout(ctx context.Context, err error) bool {
	if ctx.Err() == context.DeadlineExceeded {
		return true
	}
	var netErr net.Error
	if ok := asNetError(err, &netErr); ok {
		return netErr.Timeout()
	}
	return false
}

func asNetError(err error, target *net.Error) bool {
	for err != nil {
		if ne, ok := err.(net.Error); ok {
			*target = ne
			return true
		}
		u, ok := err.(interface{ Unwrap() error })
		if !ok {
			return false
		}
		err = u.Unwrap()
	}
	return false
}
