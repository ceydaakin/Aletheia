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

func run() error {
	cfg, err := config.Load()
	if err != nil {
		return err
	}

	log := obs.NewLogger(cfg.LogLevel)
	metrics := obs.NewMetrics()

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
	return srv.Shutdown(shutdownCtx)
}
