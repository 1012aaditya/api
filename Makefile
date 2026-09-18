# Developer entry points. Everything runs from the repository root.

BACKEND := backend
PY      := $(BACKEND)/.venv/bin/python
PIP     := $(BACKEND)/.venv/bin/pip

.PHONY: help setup deps up down migrate revision run test lint fmt purge clean

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

test: ## Run the test suite (needs no services)
	cd $(BACKEND) && .venv/bin/python -m pytest -q

lint: ## Lint
	cd $(BACKEND) && .venv/bin/ruff check .

fmt: ## Format and auto-fix
	cd $(BACKEND) && .venv/bin/ruff check --fix . && .venv/bin/ruff format .

purge: ## Delete documents past their retention window
	$(PY) scripts/purge_expired_documents.py

clean: ## Remove caches
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
	rm -rf $(BACKEND)/.pytest_cache $(BACKEND)/.ruff_cache
