# Steam4Caster — backend

Probabilistic Steam discount forecasting. Users search for games, keep a watchlist, see
regional price history, get a forecast of the next sale (probability within 7/30/90
days, likely discount tier, price interval), an explainable `BUY` / `WAIT` / `NEUTRAL`
recommendation, and deduplicated alerts by Web Push and email.

This repository holds the **backend** (HTTP API, data pipeline, forecasting, background
jobs, deployment configuration) and the **website** in `web/`, which talks to it through
the versioned API under `/api/v1` (OpenAPI at `/openapi.json`, snapshot in
[docs/openapi.json](docs/openapi.json), integration notes in [docs/api.md](docs/api.md)).

> **Before a public launch — IsThereAnyDeal approval.** Price data comes from the
> [IsThereAnyDeal API](https://docs.isthereanydeal.com/). Its terms require attribution,
> forbid implying affiliation, forbid altering supplied data, and forbid building a
> competitor to IsThereAnyDeal. Steam4Caster is a forecasting and decision-support tool,
> not a deal list, but **the project owner should contact `api@isthereanydeal.com` and
> obtain confirmation that this predictive use case is acceptable before launching
> publicly.** See [docs/provider-compliance.md](docs/provider-compliance.md).
>
> Steam4Caster is not affiliated with or endorsed by IsThereAnyDeal or Valve.

> **Forecasts are estimates.** Every probability, price interval and recommendation is a
> statistical estimate from past prices. Nothing here predicts an exact sale date or
> price, and nothing is financial advice.

## Quick start

Requirements: [uv](https://docs.astral.sh/uv/) and Docker (for PostgreSQL, Redis and
Mailpit). Python 3.12 is installed by uv if missing.

```bash
uv sync                                  # install dependencies into .venv
cp .env.example .env                     # then edit .env (see "Configuration")
docker compose up -d postgres redis mailpit
uv run alembic upgrade head              # create the schema
uv run uvicorn app.main:app_factory --factory --reload --port 8000
```

Open <http://localhost:8000/docs> for interactive API docs.

Minimum `.env` to start:

- `JWT_SECRET_KEY` — any long random string.
- Either `ITAD_API_KEY` (real data), **or** `PRICE_PROVIDER=fake` for an explicit,
  clearly synthetic demo catalogue. The fake provider is never chosen silently and is
  rejected in production.

To try everything without an API key:

```bash
# in .env: PRICE_PROVIDER=fake
uv run python -m app.cli seed-demo --country IN   # demo user, 12 synthetic games, forecasts
```

Or run the whole stack in containers (API, worker, scheduler, PostgreSQL, Redis, Mailpit):

```bash
docker compose up -d --build
```

Mailpit's inbox is at <http://localhost:8025>; no real email leaves your machine.

## Frontend

The website lives in [web/](web/) (Next.js, TypeScript, GSAP). It talks to this backend
over `/api/v1`; set `NEXT_PUBLIC_API_URL` in `web/.env.local` if the API is not on
`http://localhost:8000`, and list the site's origin in the backend's
`CORS_ALLOWED_ORIGINS`.

```bash
npm --prefix web install
```

```bash
npm --prefix web run dev
```

On Windows, `start.bat` starts the backend and the website together. The visual system
is documented in [DESIGN.md](DESIGN.md) and the product context in
[PRODUCT.md](PRODUCT.md).

## Commands

`make <target>` on macOS/Linux, or `.\scripts\dev.ps1 <target>` on Windows. Each is a
thin wrapper over the command shown.

| Task | Command |
| --- | --- |
| API | `uv run uvicorn app.main:app_factory --factory --port 8000` |
| Worker | `uv run celery -A app.tasks.celery_app worker --loglevel=INFO` (add `--pool=solo` on Windows) |
| Scheduler | `uv run celery -A app.tasks.celery_app beat --loglevel=INFO` |
| Migrations | `uv run alembic upgrade head` |
| Tests | `uv run pytest` |
| Lint / format check | `uv run ruff check .` and `uv run ruff format --check .` |
| Type check | `uv run mypy app` |
| History backfill | `uv run python -m app.cli backfill --country IN --watched` (or `--steam-app-id 1086940`) |
| Build feature dataset | `uv run python -m app.cli build-dataset` |
| Evaluate baseline | `uv run python -m app.cli evaluate-baseline` |
| Train a candidate model | `uv run python -m app.cli train` |
| Activate / roll back | `uv run python -m app.cli activate <version>` / `uv run python -m app.cli rollback` |
| Forecast one game | `uv run python -m app.cli forecast --steam-app-id 1086940 --country IN` |
| Evaluate matured forecasts | `uv run python -m app.cli evaluate-outcomes` |
| Generate VAPID keys | `uv run python -m app.cli generate-vapid-keys` |
| Export OpenAPI | `uv run python -m app.cli export-openapi` |

`uv run python -m app.cli --help` lists everything.

## Architecture in one paragraph

A modular monolith: one codebase and one container image, run as three processes (API,
Celery worker, Celery Beat). PostgreSQL is the source of truth; Redis is cache, broker,
locks and rate-limit counters only. Route handlers authenticate and validate, then call
services; services use repositories and provider interfaces; external payloads are
converted to internal schemas at the provider boundary. Forecasts are append-only and
later scored against what actually happened. Notifications go through a transactional
outbox with idempotency keys. Details: [docs/architecture.md](docs/architecture.md).

```text
app/
  api/            HTTP layer (/health, /api/v1/*)
  core/           settings, logging, errors, security, cache/locks, metrics
  db/             engine, sessions, portable types
  models/         SQLAlchemy tables
  schemas/        API request/response models
  repositories/   queries
  services/       domain logic
  providers/      pricing (IsThereAnyDeal, fake) and notifications (push, email, SMS)
  forecasting/    features, baseline, ML training/inference, evaluation, policy
  tasks/          Celery app, schedules, jobs
alembic/          migrations
tests/            unit, contract, API, task and end-to-end tests
```

## Money and regions

All money is **integer minor units plus an ISO 4217 currency code**, per country.
Regional prices are separate observations: an Indian price is whatever the provider
reports for `country=IN`, never a converted US price. Totals are grouped per currency
and never summed across currencies. A watchlist target price must be in the region's
currency.

## Forecasting

- **Baseline (always available):** deterministic rules over the game's own sale cadence,
  time since the last sale, typical and recent discount depths, estimated Steam seasonal
  sale windows, and hierarchical cold-start priors (publisher → tag cohort → global).
- **ML (optional):** LightGBM, one calibrated classifier per horizon plus a discount-tier
  classifier, trained offline with forward-chaining validation. A candidate is only
  activated if it passes promotion gates against the baseline. Inference falls back to
  the baseline when no model is active, the artifact is unusable, or a game has too
  little history.
- **Recommendation:** a separately versioned, explicit rule policy
  (`expected_future_price = P(sale)·E(price | sale) + (1 − P(sale))·current_price`).

Method, features, validation, gates and caveats: [docs/forecasting.md](docs/forecasting.md).

## Configuration

All settings are environment variables; see [.env.example](.env.example) for the full
annotated list. Only configuration needed by enabled features is required, so a missing
Twilio key never stops an email/Web Push deployment from starting.

| Feature | Needs | Without it |
| --- | --- | --- |
| Real price data | `ITAD_API_KEY` | Use `PRICE_PROVIDER=fake` (development only) |
| Web Push | `VAPID_PUBLIC_KEY`, `VAPID_PRIVATE_KEY`, `VAPID_SUBJECT` | Push channel reported as unavailable |
| Email (local) | Mailpit via Compose (`EMAIL_PROVIDER=smtp`) | — |
| Email (production) | `EMAIL_PROVIDER=resend` + `RESEND_API_KEY` and a verified domain, or real SMTP credentials | Email channel off (`EMAIL_PROVIDER=none`) |
| SMS (optional, paid) | `SMS_ENABLED=true` + Twilio settings | Disabled; nothing else is affected |
| ML forecasts | A trained and activated model | Deterministic baseline |

Production additionally requires a strong `JWT_SECRET_KEY`, `SECRETS_ENCRYPTION_KEY`,
`KV_BACKEND=redis`, and an explicit `CORS_ALLOWED_ORIGINS` allowlist.

## Deployment

- Deploy the **frontend** separately (for example on Vercel).
- Run the **API container** and persistent **worker** and **scheduler** processes on a
  container host such as Railway, Render, Fly.io or Cloud Run, all from this one image
  with different commands (see the `Dockerfile` footer and `docker-compose.yml`).
- Use **managed, persistent PostgreSQL and Redis**.
- Run `alembic upgrade head` as a release step before starting new API/worker versions.
- Do **not** run workers, schedules or model training as Vercel request functions: they
  are long-running and stateful.
- Run exactly one scheduler instance; workers can scale horizontally.

Operations, monitoring and the security checklist: [docs/operations.md](docs/operations.md).

## Testing

```bash
uv run pytest
```

Tests run against in-memory SQLite by default so they need no services. Set
`TEST_DATABASE_URL=postgresql+asyncpg://...` to run the same suite, including the
migration test, against PostgreSQL (CI does this). The PostgreSQL migration test also
starts a Testcontainers instance automatically when Docker is running.

## Limitations

- Steam sale windows are **estimated** from the historical pattern; Valve publishes no
  machine-readable schedule and has moved sales before.
- Only the Steam shop is forecast. Other shops are not ingested.
- History depth depends on what IsThereAnyDeal holds for a game and region.
- A trained model is only as good as the stored history. With a small catalogue the
  baseline will often beat it, and the promotion gates will (correctly) refuse it.
- First-party email/password authentication only; no password reset or social login yet.
- Web Push needs a frontend service worker; the backend only stores subscriptions and
  sends messages.

Current status and verification results: [docs/progress.md](docs/progress.md).
