<#
.SYNOPSIS
  Windows equivalent of the Makefile targets (GNU make is not installed by default).

.EXAMPLE
  .\scripts\dev.ps1 setup
  .\scripts\dev.ps1 api
  .\scripts\dev.ps1 forecast -AppId 1086940 -Country IN
#>
param(
    [Parameter(Mandatory = $true, Position = 0)]
    [ValidateSet('setup', 'infra', 'up', 'down', 'migrate', 'seed', 'api', 'worker', 'scheduler',
        'test', 'lint', 'format', 'typecheck', 'check', 'backfill', 'dataset', 'baseline', 'train',
        'forecast', 'evaluate', 'vapid', 'openapi')]
    [string]$Task,
    [string]$Country = 'IN',
    [int]$AppId = 0
)

$ErrorActionPreference = 'Stop'

switch ($Task) {
    'setup'     { uv sync; if ($?) { uv run pre-commit install } }
    'infra'     { docker compose up -d postgres redis mailpit }
    'up'        { docker compose up -d --build }
    'down'      { docker compose down }
    'migrate'   { uv run alembic upgrade head }
    'seed'      { uv run python -m app.cli seed-demo --country $Country }
    'api'       { uv run uvicorn app.main:app_factory --factory --reload --port 8000 }
    # Celery's default prefork pool is not supported on Windows.
    'worker'    { uv run celery -A app.tasks.celery_app worker --loglevel=INFO --pool=solo }
    'scheduler' { uv run celery -A app.tasks.celery_app beat --loglevel=INFO }
    'test'      { uv run pytest }
    'lint'      { uv run ruff check .; if ($?) { uv run ruff format --check . } }
    'format'    { uv run ruff check --fix .; uv run ruff format . }
    'typecheck' { uv run mypy app }
    'check'     {
        uv run ruff check .
        if ($?) { uv run ruff format --check . }
        if ($?) { uv run mypy app }
        if ($?) { uv run pytest }
    }
    'backfill'  { uv run python -m app.cli backfill --country $Country --watched }
    'dataset'   { uv run python -m app.cli build-dataset }
    'baseline'  { uv run python -m app.cli evaluate-baseline }
    'train'     { uv run python -m app.cli train }
    'forecast'  {
        if ($AppId -le 0) { throw 'Pass -AppId <steam app id>.' }
        uv run python -m app.cli forecast --steam-app-id $AppId --country $Country
    }
    'evaluate'  { uv run python -m app.cli evaluate-outcomes }
    'vapid'     { uv run python -m app.cli generate-vapid-keys }
    'openapi'   { uv run python -m app.cli export-openapi }
}
