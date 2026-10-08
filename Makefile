.PHONY: setup infra up down migrate seed api worker scheduler test lint format typecheck check \
        backfill dataset baseline train forecast evaluate vapid openapi

COUNTRY ?= IN

setup:            ## Install dependencies into .venv and install git hooks
	uv sync
	uv run pre-commit install

infra:            ## Start PostgreSQL, Redis and Mailpit only
	docker compose up -d postgres redis mailpit

up:               ## Build and start the whole stack
	docker compose up -d --build

down:
	docker compose down

migrate:          ## Apply database migrations
	uv run alembic upgrade head

seed:             ## Load the synthetic demo catalogue (PRICE_PROVIDER=fake only)
	uv run python -m app.cli seed-demo --country $(COUNTRY)

api:              ## Run the API with auto-reload
	uv run uvicorn app.main:app_factory --factory --reload --port 8000

worker:           ## Run a Celery worker (add --pool=solo on Windows)
	uv run celery -A app.tasks.celery_app worker --loglevel=INFO

scheduler:        ## Run Celery Beat
	uv run celery -A app.tasks.celery_app beat --loglevel=INFO

test:
	uv run pytest

lint:
	uv run ruff check .
	uv run ruff format --check .

format:
	uv run ruff check --fix .
	uv run ruff format .

typecheck:
	uv run mypy app

check: lint typecheck test

backfill:         ## Backfill history for every watched game: make backfill COUNTRY=IN
	uv run python -m app.cli backfill --country $(COUNTRY) --watched

dataset:
	uv run python -m app.cli build-dataset

baseline:         ## Evaluate the deterministic baseline
	uv run python -m app.cli evaluate-baseline

train:            ## Train a candidate model (does not activate it)
	uv run python -m app.cli train

forecast:         ## make forecast APPID=1086940 COUNTRY=IN
	uv run python -m app.cli forecast --steam-app-id $(APPID) --country $(COUNTRY)

evaluate:         ## Evaluate matured forecasts against real outcomes
	uv run python -m app.cli evaluate-outcomes

vapid:
	uv run python -m app.cli generate-vapid-keys

openapi:
	uv run python -m app.cli export-openapi
