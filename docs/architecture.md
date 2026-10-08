# Architecture

## Starting point

The repository contained only the implementation brief (`prompt.md`): no code, no
dependency files, no tests. Everything here was built from scratch on the brief's
default stack, so there was no existing work to preserve or integrate with.

## Shape

A modular monolith. One codebase and one container image run as three kinds of process:

| Process | Command | Role |
| --- | --- | --- |
| API | `uvicorn app.main:app_factory --factory` | HTTP, authentication, reads, on-demand refresh |
| Worker | `celery -A app.tasks.celery_app worker` | Ingestion, forecasting, alerts, delivery, training |
| Scheduler | `celery -A app.tasks.celery_app beat` | Emits the periodic tasks (run exactly one) |

```text
            ┌────────────┐      ┌──────────────────────────┐
 frontend ─▶│  FastAPI   │─────▶│ services                 │
            │  /api/v1   │      │  catalog · prices        │
            └────────────┘      │  forecasts · watchlist   │      ┌───────────────┐
                                │  alerts · dispatcher     │─────▶│ PostgreSQL    │
            ┌────────────┐      │  performance · models    │      │ source of     │
 beat ─────▶│  Celery    │─────▶│                          │      │ truth         │
            │  worker    │      └──────┬───────────┬───────┘      └───────────────┘
            └────────────┘             │           │
                                       ▼           ▼              ┌───────────────┐
                              price provider   notification       │ Redis         │
                              (ITAD / fake)    providers          │ cache, broker,│
                                               (push/email/SMS)   │ locks, limits │
                                                                  └───────────────┘
```

## Layering rules

- **Route handlers** (`app/api`) authenticate, validate and call one service. No domain
  logic and no SQL.
- **Services** (`app/services`) hold domain logic and transaction boundaries. They commit
  explicitly; the request-scoped session rolls back anything left uncommitted.
- **Repositories** (`app/repositories`) hold the queries shared between services
  (catalogue, prices, forecasts).
- **Providers** (`app/providers`) are the only code that knows an external wire format.
  `PriceDataProvider` and `NotificationProvider` are protocols with real and fake
  implementations; payloads become internal schemas at this boundary.
- **Forecasting** (`app/forecasting`) is pure Python over plain data classes. It never
  touches the database, which is what makes the leakage tests possible.
- **Celery tasks** (`app/tasks/tasks.py`) are thin wrappers; the work is in
  `app/tasks/jobs.py` as ordinary async functions.

The `Container` (`app/core/container.py`) wires settings, database, cache, providers,
the model store and the task dispatcher, and is the single seam tests replace.

## Data model

| Table | Purpose | Key constraint |
| --- | --- | --- |
| `users`, `refresh_tokens` | Accounts; refresh tokens stored as SHA-256 digests in rotation families | unique email, unique token hash |
| `games` | Normalized catalogue; ITAD id and Steam app id kept separately | each unique when present |
| `shops` | Provider shops; Steam is resolved by name from the provider's list | unique provider shop id |
| `regional_game_prices` | Latest price per game/shop/country, with the regional historical low | unique (game, shop, country) |
| `price_observations` | Append-only price change log | unique (game, shop, country, observed_at, price, regular) |
| `sale_events` | Sale periods derived from observations; rebuildable | unique (game, shop, country, started_at) |
| `ingestion_watermarks` | Per-series backfill/incremental progress | unique (game, shop, country) |
| `watchlist_entries` | A user's game with alert preferences | unique (user, game) |
| `notification_preferences` | Per-channel enablement, quiet hours, digest mode | unique (user, channel) |
| `push_subscriptions` | Web Push subscriptions; endpoint and keys encrypted | unique endpoint fingerprint |
| `forecasts` | Append-only forecasts plus outcome columns | checks on probability and price ordering |
| `recommendations` | Policy decision for a forecast (and optionally an entry) | — |
| `model_versions` | Registry: artifact path, checksum, metrics, lifecycle | unique version |
| `notification_events` / `_outbox` / `_deliveries` | Outbox pipeline | unique idempotency key |

Money columns are integers in minor units with a currency column beside them. Timestamps
are timezone-aware and stored in UTC. Column types are portable (JSONB on PostgreSQL,
JSON on SQLite) so the same models back fast local tests.

## Key flows

**Price refresh.** Beat triggers `prices.refresh_watched`. Watched (game, country) pairs
are de-duplicated across users and fetched in one batched provider call per country.
Only series whose price actually changed enqueue `prices.process_series`.

**Series processing** (`process_series`). Ensure history is ingested → make sure the
current price is fresh → return the latest forecast or generate a new one if the price
changed or it is stale → evaluate alert rules. Every step is idempotent, so the task is
safe under at-least-once delivery.

**History ingestion.** A distributed lock per series, then one provider read from the
stored watermark (full backfill the first time, a small overlap afterwards), inserted
with `ON CONFLICT DO NOTHING`. The watermark advances only after a successful commit, so
a failed backfill simply runs again. Sale events are re-derived from observations.

**Forecast.** Observations up to the cutoff → `build_state` (features) → ML if a
compatible model is active and the series has enough history, otherwise the baseline →
confidence, explanation factors, price interval → one new immutable `forecasts` row.

**Alerts.** Rules produce triggers, each with a stable identity (price and timestamp,
or forecast id, or window date). One transaction writes the event and one outbox row per
usable channel, keyed by `sha256(user | entry | trigger | identity | channel)`. Cooldown
and per-user limits write `SUPPRESSED` rows so an identity is never reconsidered; quiet
hours defer delivery. The dispatcher claims rows with a compare-and-set, sends, records
every attempt, and retries only transient failures with exponential backoff and jitter.

**Accountability.** A daily job fills outcome columns on forecasts whose horizons have
expired, computed only from stored observations and the forecast's own cutoff. The
performance endpoints aggregate those outcomes.

## Reliability and security

- Correlation id per request (`X-Request-ID`), included in logs and every error body.
- JSON logs with redaction of secrets, tokens, email addresses, phone numbers and push
  endpoints.
- Prometheus metrics at `/metrics` (API, provider, cache, jobs, inference, alerts,
  deliveries). `app/core/metrics.py` is the one place an OpenTelemetry exporter would
  hook in.
- Rate limits on authentication, search passthrough and test notifications.
- Argon2id passwords; 15-minute access tokens; rotating refresh tokens with reuse
  detection that revokes the whole family.
- Push subscription endpoint and keys encrypted at rest.
- Every watchlist and subscription query is scoped to the owner; foreign ids return 404.
- Explicit CORS allowlist; a wildcard origin is rejected at start-up.
- Readiness checks the database and required configuration; a Redis outage reports
  `degraded` and reads keep working.

## Assumptions and deviations from the brief

| Topic | Decision | Reason |
| --- | --- | --- |
| Delivery order | Built as one pass rather than phase by phase | Requested by the project owner |
| Boosted trees | LightGBM | Native missing-value handling (cold-start rows are sparse), small wheels on every platform, per-feature contributions for explanations |
| Survival analysis | Not used; one calibrated classifier per horizon | Explicitly allowed by the brief; simpler to calibrate and evaluate per horizon |
| Model registry | Files plus the `model_versions` table, not MLflow | MLflow would add a service for little gain at this size |
| Dataframes | Pandas only | Polars was not needed |
| Repositories | Present for catalogue, prices and forecasts; auth, watchlist and notification services query directly | Their queries are not shared, so a repository layer would only be indirection |
| Tests and PostgreSQL | Suite runs on SQLite by default, PostgreSQL via `TEST_DATABASE_URL` or Testcontainers | Fast, service-free local runs; CI covers PostgreSQL |
| Shops | Only Steam is supported; other `shop` values return 422 | The product forecasts Steam discounts |
| History chunking | A single `since` read per series, no pagination | The live history endpoint takes only `since` and returns the full log |
| Currency changes | If a region's currency changed, only observations in the current currency are used | Prices in different currencies are never compared |
| Extra endpoints | `/api/v1/meta`, `/games/lookup`, `/games/{id}/sale-events`, `/push-subscriptions` (GET), email verification, SMS verification, `PATCH /me` | Needed by a frontend or to make a required feature usable |
| Seasonal calendar | Estimated date rules in code | No official machine-readable schedule exists |
| Popularity/review features | Not used | Would need a documented, permitted source and collection method |
| Password reset, social login | Not built | Outside the brief; `users.external_subject` leaves room for an external identity provider |
