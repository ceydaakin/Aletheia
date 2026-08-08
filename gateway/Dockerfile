# syntax=docker/dockerfile:1

FROM golang:1.25-alpine AS build
WORKDIR /src
# Dependencies first so a source-only change reuses the cached module layer.
# The gateway was dependency-free until week 10, when ADR-0001's deferred
# OpenTelemetry SDK landed; that is still the only external dependency tree.
COPY go.mod go.sum ./
RUN go mod download
COPY cmd ./cmd
COPY internal ./internal
RUN CGO_ENABLED=0 go build -trimpath -ldflags="-s -w" -o /out/gateway ./cmd/gateway

FROM gcr.io/distroless/static-debian12:nonroot
COPY --from=build /out/gateway /gateway
EXPOSE 8080
USER nonroot:nonroot
ENTRYPOINT ["/gateway"]
