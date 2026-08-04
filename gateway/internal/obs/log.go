package obs

import (
	"context"
	"log/slog"
	"os"
	"strings"
)

type ctxKey int

const traceIDKey ctxKey = iota

// NewLogger returns a JSON logger. Everything downstream of here ships to Loki,
// so structured is not optional.
func NewLogger(level string) *slog.Logger {
	var lvl slog.Level
	switch strings.ToLower(level) {
	case "debug":
		lvl = slog.LevelDebug
	case "warn":
		lvl = slog.LevelWarn
	case "error":
		lvl = slog.LevelError
	default:
		lvl = slog.LevelInfo
	}
	return slog.New(slog.NewJSONHandler(os.Stdout, &slog.HandlerOptions{Level: lvl}))
}

// WithTraceID puts the request's trace id on the context so every log line and
// upstream call can carry it without threading a parameter through everything.
func WithTraceID(ctx context.Context, id string) context.Context {
	return context.WithValue(ctx, traceIDKey, id)
}

// TraceID returns the trace id on the context, or "" if there is none.
func TraceID(ctx context.Context) string {
	if v, ok := ctx.Value(traceIDKey).(string); ok {
		return v
	}
	return ""
}

// LoggerFor returns a logger pre-tagged with the context's trace id.
func LoggerFor(ctx context.Context, base *slog.Logger) *slog.Logger {
	if id := TraceID(ctx); id != "" {
		return base.With("trace_id", id)
	}
	return base
}
