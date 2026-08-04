.DEFAULT_GOAL := help
SHELL := /bin/sh

COMPOSE ?= docker compose
PY      ?= python
DATABASE_URL ?= postgresql://aletheia:aletheia@localhost:5432/aletheia

.PHONY: help
help: ## Show this help
	@grep -hE '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-14s\033[0m %s\n", $$1, $$2}'

# --- Stack ---------------------------------------------------------------

.PHONY: up
up: ## Build and start the whole stack
	$(COMPOSE) up --build

.PHONY: up-obs
up-obs: ## Start the stack including Prometheus + Grafana
	$(COMPOSE) --profile obs up --build

.PHONY: down
down: ## Stop the stack (keeps volumes)
	$(COMPOSE) down

.PHONY: clean
clean: ## Stop the stack and delete volumes
	$(COMPOSE) down -v

.PHONY: logs
logs: ## Tail logs from all services
	$(COMPOSE) logs -f

.PHONY: migrate
migrate: ## Apply SQL migrations against $$DATABASE_URL
	$(COMPOSE) exec -T postgres psql -v ON_ERROR_STOP=1 -U aletheia -d aletheia \
		-f /docker-entrypoint-initdb.d/0001_init.sql

# --- Build / test --------------------------------------------------------

.PHONY: test
test: test-go test-py ## Run all tests

.PHONY: test-go
test-go: ## Run Go tests
	cd gateway && go test ./...

.PHONY: test-py
test-py: ## Run Python tests
	cd python && $(PY) -m pytest -q

.PHONY: build
build: ## Build the gateway binary into gateway/bin/
	cd gateway && go build -o bin/gateway ./cmd/gateway

.PHONY: fmt
fmt: ## Format Go and Python sources
	cd gateway && go fmt ./...
	cd python && ruff format .

.PHONY: lint
lint: ## Lint Go and Python sources
	cd gateway && go vet ./...
	cd python && ruff check .

# --- Evaluation ----------------------------------------------------------

.PHONY: eval
eval: ## Run the evaluation harness (seeded, cached)
	cd eval && $(PY) -m aletheia_eval.run --config configs/default.yaml
