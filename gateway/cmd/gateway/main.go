// Command gateway is the Aletheia risk-controlled RAG gateway.
package main

import (
	"context"
	"errors"
	"net/http"
	"os"
	"os/signal"
	"syscall"
	"time"

	"github.com/ceydaakin/aletheia/gateway/internal/api"
	"github.com/ceydaakin/aletheia/gateway/internal/config"
	"github.com/ceydaakin/aletheia/gateway/internal/obs"
	"github.com/ceydaakin/aletheia/gateway/internal/pipeline"
	"github.com/ceydaakin/aletheia/gateway/internal/tenant"
	"github.com/ceydaakin/aletheia/gateway/internal/upstream"
)

func main() {
	if err := run(); err != nil {
		// The logger may not exist yet at this point, so use the default one.
		obs.NewLogger("error").Error("fatal", "error", err)
		os.Exit(1)
	}
}

const version = "0.1.0"

func run() error {
	cfg, err := config.Load()
	if err != nil {
		return err
	}

	log := obs.NewLogger(cfg.LogLevel)
	metrics := obs.NewMetrics()

	// Tracing is observability, not correctness: a collector that is down or
	// unconfigured must never stop the gateway serving, so a failure here is
	// logged and the process continues untraced.
	shutdownTracing, err := obs.InitTracing(
		context.Background(), cfg.OTLPEndpoint, "aletheia-gateway", version, cfg.TraceSampleRatio,
	)
	if err != nil {
		log.Warn("tracing disabled", "error", err)
		shutdownTracing = func(context.Context) error { return nil }
	} else if cfg.OTLPEndpoint != "" {
		log.Info("tracing enabled", "endpoint", cfg.OTLPEndpoint, "sample_ratio", cfg.TraceSampleRatio)
	}

	tenants, err := tenant.NewRegistry(cfg.TenantSpec)
	if err != nil {
		return err
	}

	retrieval := upstream.New(cfg.Retrieval.Name, cfg.Retrieval.URL, cfg.Retrieval.Timeout)
	generation := upstream.New(cfg.Generation.Name, cfg.Generation.URL, cfg.Generation.Timeout)
	verifier := upstream.New(cfg.Verifier.Name, cfg.Verifier.URL, cfg.Verifier.Timeout)
	risk := upstream.New(cfg.Risk.Name, cfg.Risk.URL, cfg.Risk.Timeout)

	p := pipeline.New(retrieval, generation, verifier, risk, cfg.RetrievalK, log, metrics)
	srv := api.NewServer(cfg, tenants, p, log, metrics,
		[]*upstream.Client{retrieval, generation, verifier, risk}).HTTPServer()

	errCh := make(chan error, 1)
	go func() {
		log.Info("gateway listening",
			"addr", cfg.Addr,
			"tenants", tenants.Len(),
			"request_timeout_ms", cfg.RequestTimeout.Milliseconds(),
		)
		if err := srv.ListenAndServe(); err != nil && !errors.Is(err, http.ErrServerClosed) {
			errCh <- err
		}
	}()

	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer stop()

	select {
	case err := <-errCh:
		return err
	case <-ctx.Done():
	}

	// Drain in-flight requests. The window is the request budget plus slack, so
	// a shutdown never turns a nearly-finished answer into a dropped connection.
	log.Info("shutting down")
	shutdownCtx, cancel := context.WithTimeout(context.Background(), cfg.RequestTimeout+5*time.Second)
	defer cancel()

	shutdownErr := srv.Shutdown(shutdownCtx)
	// After the server, so spans from in-flight requests are recorded before the
	// batch processor is flushed.
	if err := shutdownTracing(shutdownCtx); err != nil {
		log.Warn("tracing shutdown", "error", err)
	}
	return shutdownErr
}
