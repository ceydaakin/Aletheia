# syntax=docker/dockerfile:1

FROM golang:1.24-alpine AS build
WORKDIR /src
# No external dependencies (ADR-0001), so there is no module download step and
# the build is fully offline and reproducible.
COPY go.mod ./
COPY cmd ./cmd
COPY internal ./internal
RUN CGO_ENABLED=0 go build -trimpath -ldflags="-s -w" -o /out/gateway ./cmd/gateway

FROM gcr.io/distroless/static-debian12:nonroot
COPY --from=build /out/gateway /gateway
EXPOSE 8080
USER nonroot:nonroot
ENTRYPOINT ["/gateway"]
