# =============================================================================
# NEXUS — task runner.
# =============================================================================
# Every target is a thin wrapper around a real command or a real script; no
# logic is duplicated here. Requires GNU make 4.x (`make` is not shipped with
# Windows — use Git Bash with a make package, or run the scripts directly).
#
#   make            list the targets
#   make install    bootstrap the checkout (venv, .env, npm install)
# =============================================================================

SHELL := /bin/bash
.DEFAULT_GOAL := help

BACKEND  := backend
FRONTEND := frontend
SCRIPTS  := scripts

# Interpreter used for the backend. A Windows virtualenv keeps it in Scripts/,
# a POSIX one in bin/, and neither layout exists on the other platform, so the
# first path that is actually present wins.
#
# Overridable. The value is always read relative to the repository root: the
# targets that `cd backend` prepend a `../` themselves, so the same value works
# from either directory.
#   make migrate PY=backend/.venv/Scripts/python.exe
PY_SCRIPTS := $(wildcard $(BACKEND)/.venv/Scripts/python.exe)
PY_BIN     := $(wildcard $(BACKEND)/.venv/bin/python)
PY ?= $(if $(PY_SCRIPTS),$(PY_SCRIPTS),$(PY_BIN))
NPM ?= npm

# `make install` has to work before the virtualenv exists, so it probes for any
# Python 3.13+ on PATH instead of using $(PY).
PYTHON_BOOTSTRAP ?= $(shell command -v python3 2>/dev/null || command -v python 2>/dev/null || echo python3)

# Seconds `db-wait` keeps retrying before it gives up.
TIMEOUT ?= 60
# Extra flags for `test-db`. Empty on purpose: the target only ever creates the
# test database, so `make test-db TEST_DB_FLAGS=--drop` is how you ask for a
# rebuild rather than having destruction be the default.
TEST_DB_FLAGS ?=

# `alembic.ini` uses paths relative to backend/, so Alembic always runs there.
ALEMBIC := cd $(BACKEND) && ../$(PY) -m alembic

.PHONY: help install bootstrap up down logs migrate migrate-down revision \
        db-wait test-db test test-backend test-frontend lint backend frontend clean

help: ## Show this help
	@echo "NEXUS — available targets:"
	@grep -E '^[a-z][a-zA-Z0-9_-]*:.*?## ' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?## "} {printf "  \033[36m%-16s\033[0m %s\n", $$1, $$2}'
	@echo
	@echo "PY defaults to the interpreter inside backend/.venv/ (Scripts/ on Windows,"
	@echo "bin/ on POSIX). To use another one, give a path relative to this directory:"
	@echo "  make test-backend PY=backend/.venv/Scripts/python.exe"

install: ## Create .env, the backend venv and all dependencies
	$(PYTHON_BOOTSTRAP) $(SCRIPTS)/bootstrap.py

bootstrap: install ## Alias for `make install`

up: ## Build and start the Docker stack in the background
	docker compose up -d --build
	@echo "frontend  http://localhost:5173"
	@echo "api docs  http://localhost:8000/docs"

down: ## Stop the Docker stack (keeps the database volume)
	docker compose down

logs: ## Follow the Docker stack logs
	docker compose logs -f

migrate: ## Apply all pending Alembic migrations
	$(ALEMBIC) upgrade head

migrate-down: ## Roll back the most recent Alembic migration
	$(ALEMBIC) downgrade -1

revision: ## Create a migration:  make revision m="add widgets table"
	@test -n "$(m)" || { echo "usage: make revision m=\"short message\""; exit 1; }
	$(ALEMBIC) revision -m "$(m)"

db-wait: ## Block until PostgreSQL accepts connections (make db-wait TIMEOUT=120)
	$(PY) $(SCRIPTS)/wait_for_db.py --timeout $(TIMEOUT)

test: test-backend test-frontend ## Run every test suite

test-db: ## Create the pytest database; `make test-db TEST_DB_FLAGS=--drop` rebuilds it
	$(PY) $(SCRIPTS)/create_test_database.py $(TEST_DB_FLAGS)

test-backend: ## Run the pytest suite
	cd $(BACKEND) && ../$(PY) -m pytest

test-frontend: ## Run the vitest suite
	cd $(FRONTEND) && $(NPM) run test

lint: ## Lint and typecheck backend and frontend
	cd $(BACKEND) && ../$(PY) -m ruff check .
	cd $(BACKEND) && ../$(PY) -m ruff format --check .
	cd $(FRONTEND) && $(NPM) run lint
	cd $(FRONTEND) && $(NPM) run typecheck

backend: ## Run the FastAPI server in the foreground
	./$(SCRIPTS)/dev.sh backend

frontend: ## Run the Vite dev server in the foreground
	./$(SCRIPTS)/dev.sh frontend

clean: ## Remove build artefacts and tooling caches (never touches .env or data)
	cd $(BACKEND) && ../$(PY) -c "import shutil,pathlib;[shutil.rmtree(p,ignore_errors=True) for p in ['.pytest_cache','.ruff_cache']]"
	cd $(BACKEND) && find app migrations tests -type d -name __pycache__ -prune -exec rm -rf {} + 2>/dev/null || true
	rm -rf $(FRONTEND)/dist $(FRONTEND)/coverage
	rm -f $(FRONTEND)/*.tsbuildinfo
	@echo "cleaned build artefacts"
