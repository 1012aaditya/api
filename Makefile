# Developer entry points. Everything runs from the repository root.

BACKEND  := backend
FRONTEND := frontend
SDK      := clients/python
PY       := $(BACKEND)/.venv/bin/python
PIP      := $(BACKEND)/.venv/bin/pip

.PHONY: help setup setup-sdk setup-web up down stack stack-down stack-logs \
	migrate revision run worker web build-web test test-pg test-sdk test-web \
	lint lint-sdk lint-web preflight accuracy purge clean

help:
	@grep -E '^[a-z-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-12s\033[0m %s\n", $$1, $$2}'

setup: ## Create the virtualenv and install the backend with dev extras
	python3 -m venv $(BACKEND)/.venv
	$(PIP) install --upgrade pip
	$(PIP) install -e "$(BACKEND)[dev]"
	@test -f .env || (cp .env.example .env && echo "Created .env from .env.example — fill it in.")

stack: ## Run everything in Docker — API, worker, dashboard, Postgres
	docker compose up --build

stack-down: ## Stop it and remove the containers
	docker compose down

stack-logs: ## Follow the logs
	docker compose logs -f

up: ## Start only the backing services (Postgres), for host-side development
	docker compose up -d postgres

down: ## Stop them
	docker compose down

migrate: ## Apply database migrations
	cd $(BACKEND) && .venv/bin/alembic upgrade head

revision: ## Autogenerate a migration: make revision m="add webhooks"
	cd $(BACKEND) && .venv/bin/alembic revision --autogenerate -m "$(m)"

run: ## Run the API with reload on http://localhost:8000
	cd $(BACKEND) && .venv/bin/uvicorn app.main:app --reload --port 8000

setup-web: ## Install the dashboard's dependencies
	cd $(FRONTEND) && npm install
	@test -f $(FRONTEND)/.env.local || (cp $(FRONTEND)/.env.local.example $(FRONTEND)/.env.local \
		&& echo "Created frontend/.env.local from the example.")

worker: ## Run the background worker (async jobs + webhook delivery)
	$(PY) scripts/run_worker.py

web: ## Run the dashboard on http://localhost:3000 (needs `make run` too)
	cd $(FRONTEND) && npm run dev

build-web: ## Production build of the dashboard
	cd $(FRONTEND) && npm run build

test: ## Run the backend test suite (needs no services)
	cd $(BACKEND) && .venv/bin/python -m pytest -q

test-pg: ## Run the same suite against PostgreSQL (needs `make up`)
	# Where the dialect-only bugs are: JSONB, SKIP LOCKED, boolean defaults.
	cd $(BACKEND) && DOCUPARSE_TEST_DATABASE_URL=$${DOCUPARSE_TEST_DATABASE_URL:-postgresql+asyncpg://docuparse:docuparse@localhost:5432/docuparse} \
		.venv/bin/python -m pytest -q

setup-sdk: ## Install the Python client in editable mode, with dev extras
	$(PIP) install -e "$(SDK)[dev]"

test-sdk: ## Run the Python client's test suite (needs no services)
	# PYTHONPATH rather than an install, so this works straight after `make setup`.
	cd $(SDK) && PYTHONPATH=src ../../$(PY) -m pytest -q

test-web: ## Typecheck the dashboard, then smoke-test it in a real browser
	cd $(FRONTEND) && npm run typecheck
	cd $(FRONTEND) && npm run smoke

lint: ## Lint the backend
	cd $(BACKEND) && .venv/bin/ruff check .

lint-sdk: ## Lint the Python client
	cd $(SDK) && ../../$(BACKEND)/.venv/bin/ruff check . && ../../$(BACKEND)/.venv/bin/ruff format --check .

lint-web: ## Typecheck the dashboard
	cd $(FRONTEND) && npm run typecheck

fmt: ## Format and auto-fix
	cd $(BACKEND) && .venv/bin/ruff check --fix . && .venv/bin/ruff format .

preflight: ## Check every external dependency is actually working
	$(PY) scripts/preflight.py

accuracy: ## Measure extraction accuracy: make accuracy dir=~/truth
	@test -n "$(dir)" || (echo "Usage: make accuracy dir=~/truth" && exit 1)
	$(PY) scripts/measure_accuracy.py $(dir)

purge: ## Delete documents past their retention window
	$(PY) scripts/purge_expired_documents.py

clean: ## Remove caches
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
	rm -rf $(BACKEND)/.pytest_cache $(BACKEND)/.ruff_cache $(FRONTEND)/.next
	rm -rf $(SDK)/.pytest_cache $(SDK)/build $(SDK)/dist
