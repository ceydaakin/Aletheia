package api

import (
	"net/http"
	"sync"
)

// handleHealthz is liveness: the process is up. It must not depend on upstreams,
// or a slow verifier would get the gateway killed by the orchestrator.
func (s *Server) handleHealthz(w http.ResponseWriter, r *http.Request) {
	writeJSON(w, http.StatusOK, map[string]string{"status": "ok"})
}

// handleReadyz is readiness: we can serve a request end to end. Upstreams are
// checked concurrently so the probe costs one round trip, not four.
func (s *Server) handleReadyz(w http.ResponseWriter, r *http.Request) {
	type result struct {
		name string
		err  error
	}

	results := make([]result, len(s.upstreams))
	var wg sync.WaitGroup
	for i, c := range s.upstreams {
		wg.Add(1)
		// Loop variables are per-iteration as of Go 1.22, so capturing is safe.
		go func() {
			defer wg.Done()
			results[i] = result{name: c.Name(), err: c.Health(r.Context())}
		}()
	}
	wg.Wait()

	services := make(map[string]string, len(results))
	ready := true
	for _, res := range results {
		if res.err != nil {
			services[res.name] = "unavailable"
			ready = false
			continue
		}
		services[res.name] = "ok"
	}

	status := http.StatusOK
	state := "ready"
	if !ready {
		status = http.StatusServiceUnavailable
		state = "not_ready"
	}
	writeJSON(w, status, map[string]any{"status": state, "services": services})
}

func (s *Server) handleMetrics(w http.ResponseWriter, r *http.Request) {
	w.Header().Set("Content-Type", "text/plain; version=0.0.4; charset=utf-8")
	w.WriteHeader(http.StatusOK)
	s.metrics.Render(w)
}
