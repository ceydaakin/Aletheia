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
migrate: ## Apply every SQL migration, in order, to the running postgres
	@for f in db/migrations/*.sql; do \
		echo "applying $$f"; \
		$(COMPOSE) exec -T postgres psql -q -v ON_ERROR_STOP=1 -U aletheia -d aletheia \
			-f "/docker-entrypoint-initdb.d/$$(basename $$f)" || exit 1; \
	done

.PHONY: testdb
testdb: ## Create and migrate the scratch database the db-marked tests use
	-$(COMPOSE) exec -T postgres psql -U aletheia -c "CREATE DATABASE aletheia_test OWNER aletheia"
	@for f in db/migrations/*.sql; do \
		$(COMPOSE) exec -T postgres psql -q -v ON_ERROR_STOP=1 -U aletheia -d aletheia_test \
			-f "/docker-entrypoint-initdb.d/$$(basename $$f)" || exit 1; \
	done
	@echo 'ready: export ALETHEIA_TEST_DATABASE_URL=postgresql://aletheia:aletheia@localhost:5432/aletheia_test'

.PHONY: ingest
ingest: ## Load a corpus: make ingest DIR=../corpora/demo TENANT=demo LANG=en
	cd python && $(PY) -m aletheia.ingestion.cli load $(DIR) --tenant $(TENANT) --lang $(LANG)

.PHONY: backfill
backfill: ## Embed chunks stored without vectors: make backfill TENANT=demo
	cd python && $(PY) -m aletheia.ingestion.backfill --tenant $(TENANT)

.PHONY: eval-retrieval
eval-retrieval: ## Measure retrieval: make eval-retrieval DATASET=../eval/datasets/bootstrap-tr
	cd python && $(PY) -m aletheia.eval.retrieval --dataset $(DATASET) --ingest

.PHONY: calibrate
calibrate: ## Fit and certify a threshold: make calibrate DATASET=../eval/datasets/bootstrap-tr ALPHA=0.05
	cd python && VERIFIER_BACKEND=nli $(PY) -m aletheia.eval.calibrate \
		--dataset $(DATASET) --alpha $(or $(ALPHA),0.05) \
		--curve ../eval/results/$(notdir $(DATASET))-curve.json

.PHONY: test-slow
test-slow: ## Run the model-backed tests (downloads weights, minutes on CPU)
	cd python && ALETHEIA_SLOW_TESTS=1 $(PY) -m pytest -m slow

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
