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
calibrate: ## Certify a threshold from records: make calibrate DATASET=kvkk-tr ALPHA=0.05
	cd python && $(PY) -m aletheia.eval.calibrate \
		--dataset ../eval/datasets/$(DATASET) --records ../eval/results/records/$(DATASET).jsonl \
		--alpha $(or $(ALPHA),0.05) --calibration-fraction 0.6 \
		--curve ../eval/results/$(DATASET)-curve.json

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

# The full-system configuration every headline number is measured under.
MODELS_ENV = EMBEDDING_BACKEND=sentence-transformers RERANKER_BACKEND=cross-encoder \
	VERIFIER_DEVICE=$(or $(DEVICE),auto) VERIFIER_MAX_LENGTH=512 DATABASE_URL=$(DATABASE_URL)
DATASETS ?= kvkk-tr kvkk-en en-public

.PHONY: corpora
corpora: ## Re-fetch the public corpora (KVKK TR/EN, Wikipedia, arXiv)
	$(PY) scripts/fetch_kvkk.py
	$(PY) scripts/fetch_en_public.py

.PHONY: collect
collect: ## Run the pipeline once per dataset and cache responses: make collect DATASETS=kvkk-tr
	cd python && for d in $(DATASETS); do \
		$(MODELS_ENV) $(PY) -m aletheia.eval.collect --dataset ../eval/datasets/$$d --ingest \
			--out ../eval/results/records/$$d.jsonl || exit 1; \
	done

.PHONY: collect-ablations
collect-ablations: ## Records for the reranker ablation and the hallucination-rate sweep
	cd python && for d in $(DATASETS); do \
		$(MODELS_ENV) RERANKER_BACKEND=null $(PY) -m aletheia.eval.collect --dataset ../eval/datasets/$$d --verifiers nli \
			--out ../eval/results/records/$$d__no-rerank.jsonl || exit 1; \
	done
	cd python && for d in $(or $(RATE_DATASETS),kvkk-tr); do for r in 0.05 0.30; do \
		$(MODELS_ENV) $(PY) -m aletheia.eval.collect --dataset ../eval/datasets/$$d --rate $$r \
			--verifiers nli --out ../eval/results/records/$$d__rate$$r.jsonl || exit 1; \
	done; done

.PHONY: eval
eval: ## Every result table, from cached records (no models): writes eval/results/experiments.md
	cd python && $(PY) -m aletheia.eval.experiments --records-dir ../eval/results/records \
		--out-dir ../eval/results

.PHONY: review
review: ## Verify drafted queries by hand: make review DATASET=kvkk-tr PARALLEL=kvkk-en
	cd python && $(PY) -m aletheia.eval.review --dataset ../eval/datasets/$(DATASET) \
		$(if $(PARALLEL),--parallel ../eval/datasets/$(PARALLEL))

.PHONY: loadtest
loadtest: ## Measure end-to-end latency: make loadtest C=20 N=400
	@echo "note: raise GATEWAY_RATE_LIMIT_PER_SECOND above the offered rate first"
	cd gateway && go run ./cmd/loadtest -c $(or $(C),20) -n $(or $(N),400)
