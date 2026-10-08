@echo off
rem ============================================================================
rem  Steam4Caster - start the backend and the website with one double-click.
rem
rem  Full mode (Docker Desktop running):
rem      PostgreSQL + Redis + Mailpit in Docker, then the API, a Celery worker
rem      and the Celery scheduler, each in its own window.
rem  Lite mode (Docker not running):
rem      One API process on a local SQLite file with an in-memory cache. It
rem      also runs the background jobs itself: price refresh, forecasts, alerts
rem      and browser push. No email (there is no local mail server).
rem
rem  The website (Next.js, in web\) starts in both modes when Node.js is installed.
rem
rem  Close the opened windows to stop. Docker services keep running until you
rem  run:  docker compose down
rem  Set S4C_NO_BROWSER=1 to skip opening the site in a browser.
rem ============================================================================
setlocal
cd /d "%~dp0"
title Steam4Caster launcher

set "PORT=8000"
set "WEB_PORT=3000"

where uv >nul 2>&1
if errorlevel 1 (
    echo [ERROR] "uv" was not found on PATH.
    echo         Install it from https://docs.astral.sh/uv/ and run this again.
    goto :fail
)

echo [1/5] Installing dependencies...
uv sync --quiet
if errorlevel 1 goto :fail

echo [2/5] Checking configuration...
if not exist ".env" (
    uv run --quiet python -m app.cli init-env
    if errorlevel 1 goto :fail
)

rem Browser push needs a VAPID key pair; create one on first run.
uv run --quiet python -m app.cli init-push
if errorlevel 1 goto :fail

rem Without an IsThereAnyDeal key the real provider cannot start. Fall back to the
rem synthetic demo catalogue, and say so: this data is NOT real.
set "HAS_KEY="
for /f "usebackq tokens=1,* delims==" %%A in (".env") do (
    if /i "%%A"=="ITAD_API_KEY" if not "%%B"=="" set "HAS_KEY=1"
    if /i "%%A"=="PRICE_PROVIDER" if /i "%%B"=="fake" set "PRICE_PROVIDER=fake"
)
if not defined HAS_KEY set "PRICE_PROVIDER=fake"
if /i "%PRICE_PROVIDER%"=="fake" (
    echo.
    echo   *** DEMO DATA: using the synthetic fake price provider. ***
    echo   *** Set ITAD_API_KEY in .env to use real Steam prices.  ***
    echo.
)

set "MODE=full"
docker info >nul 2>&1
if errorlevel 1 set "MODE=lite"

if "%MODE%"=="full" goto :full
goto :lite

:full
echo [3/5] Starting PostgreSQL, Redis and Mailpit in Docker...
docker compose up -d --wait postgres redis mailpit
if errorlevel 1 (
    echo [WARN] Docker services failed to start. Falling back to lite mode.
    set "MODE=lite"
    goto :lite
)
goto :migrate

:lite
echo [3/5] Docker is not running: LITE MODE (SQLite, background jobs run inside the API).
set "DATABASE_URL=sqlite+aiosqlite:///./steam4caster-dev.db"
set "KV_BACKEND=memory"
set "INLINE_JOBS=true"
set "EMAIL_PROVIDER=none"

:migrate
echo [4/5] Applying database migrations...
uv run --quiet alembic upgrade head
if errorlevel 1 goto :fail

if /i "%PRICE_PROVIDER%"=="fake" (
    echo       Loading demo games ^(a generated demo password is shown once^)...
    uv run --quiet python -m app.cli seed-demo
    if errorlevel 1 goto :fail
)

echo [5/5] Starting processes...
start "Steam4Caster API" cmd /k "uv run uvicorn app.main:app_factory --factory --port %PORT%"
if "%MODE%"=="full" (
    start "Steam4Caster worker" cmd /k "uv run celery -A app.tasks.celery_app worker --loglevel=INFO --pool=solo"
    start "Steam4Caster scheduler" cmd /k "uv run celery -A app.tasks.celery_app beat --loglevel=INFO"
)

set "WEB="
where npm >nul 2>&1
if errorlevel 1 (
    echo [WARN] Node.js was not found, so the website will not start. Install it from https://nodejs.org/
) else (
    set "WEB=1"
    if not exist "web\node_modules" (
        echo       Installing website dependencies ^(first run only^)...
        call npm --prefix web install --no-fund --no-audit
        if errorlevel 1 goto :fail
    )
    start "Steam4Caster web" cmd /k "npm --prefix web run dev"
)

echo       Waiting for the API...
set /a TRIES=0
:wait
curl -s -o nul -m 2 http://127.0.0.1:%PORT%/health/live
if not errorlevel 1 goto :ready
set /a TRIES+=1
if %TRIES% geq 60 (
    echo [ERROR] The API did not come up. Check the "Steam4Caster API" window.
    goto :fail
)
ping -n 2 127.0.0.1 >nul
goto :wait

:ready
echo.
echo   Steam4Caster is running (%MODE% mode).
if defined WEB echo     Website  : http://localhost:%WEB_PORT%   ^(first load takes a few seconds^)
echo     API docs : http://localhost:%PORT%/docs
echo     Health   : http://localhost:%PORT%/health/ready
if "%MODE%"=="full" echo     Mailpit  : http://localhost:8025
echo.
echo   Close the opened windows to stop.
if "%MODE%"=="full" echo   Then run "docker compose down" to stop the Docker services.
set "OPEN_URL=http://localhost:%PORT%/docs"
if defined WEB set "OPEN_URL=http://localhost:%WEB_PORT%"
if not defined S4C_NO_BROWSER start "" "%OPEN_URL%"
echo.
pause
exit /b 0

:fail
echo.
echo   Start-up failed. See the messages above.
pause
exit /b 1
