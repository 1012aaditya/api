# Developer entry points. Everything runs from the repository root.

BACKEND  := backend
FRONTEND := frontend
PY       := $(BACKEND)/.venv/bin/python
PIP      := $(BACKEND)/.venv/bin/pip

.PHONY: help setup setup-web up down migrate revision run web build-web test test-web \
	lint lint-web purge clean

help:
	@grep -E '^[a-z-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-12s\033[0m %s\n", $$1, $$2}'

setup: ## Create the virtualenv and install the backend with dev extras
	python3 -m venv $(BACKEND)/.venv
	$(PIP) install --upgrade pip
	$(PIP) install -e "$(BACKEND)[dev]"
	@test -f .env || (cp .env.example .env && echo "Created .env from .env.example — fill it in.")

up: ## Start Postgres, Redis and MinIO
	docker compose up -d

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

web: ## Run the dashboard on http://localhost:3000 (needs `make run` too)
	cd $(FRONTEND) && npm run dev

build-web: ## Production build of the dashboard
	cd $(FRONTEND) && npm run build

test: ## Run the backend test suite (needs no services)
	cd $(BACKEND) && .venv/bin/python -m pytest -q

test-web: ## Typecheck the dashboard, then smoke-test it in a real browser
	cd $(FRONTEND) && npm run typecheck
	cd $(FRONTEND) && npm run smoke

lint: ## Lint the backend
	cd $(BACKEND) && .venv/bin/ruff check .

lint-web: ## Typecheck the dashboard
	cd $(FRONTEND) && npm run typecheck

fmt: ## Format and auto-fix
	cd $(BACKEND) && .venv/bin/ruff check --fix . && .venv/bin/ruff format .

purge: ## Delete documents past their retention window
	$(PY) scripts/purge_expired_documents.py

clean: ## Remove caches
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
	rm -rf $(BACKEND)/.pytest_cache $(BACKEND)/.ruff_cache $(FRONTEND)/.next
