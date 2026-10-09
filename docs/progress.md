# Progress

Status as of 2026-10-08. The backend was built in a single pass rather than phase by
phase, at the project owner's request. The sections below follow the brief's phases so
each exit criterion can be checked.

## Verification results

| Check | Command | Result |
| --- | --- | --- |
| Lint | `uv run ruff check .` | Pass |
| Formatting | `uv run ruff format --check .` | Pass |
| Types | `uv run mypy app` | Pass, 94 source files |
| Tests | `uv run pytest` | 174 passed, 1 skipped |
| Migrations (SQLite) | part of the test suite | Upgrade, model/migration drift check, downgrade, upgrade: pass |
| Compose file | `docker compose config -q` | Valid |
| Lock file | `uv lock --check` | In sync |
| Live server | uvicorn + HTTP requests (fake provider, SQLite) | Health, register, search, forecast, recommendation, metrics: working |
| CLI | migrate, seed-demo, evaluate-baseline, train, list-models, export-openapi | Working |

### Update: PostgreSQL verified

Docker was later brought up on the development machine. The migration applies to
PostgreSQL 16 and the full suite passes against it (181 passed, none skipped), run with
`TEST_DATABASE_URL` pointing at a separate test database. The first bullet below is
therefore resolved; the image build, Redis and a real Celery broker are still unverified.

### Update: first model trained on real data (2026-10-09)

History for 233 popular games was backfilled (US from 2012, India from December 2024):
52,016 training rows over 442 series. Candidate `lgbm-20261009135352` against the
baseline on held-out, later-in-time data:

| Horizon | Brier (model / baseline) | ROC-AUC (model / baseline) | Calibration error (model / baseline) |
| --- | --- | --- | --- |
| 7 days | 0.097 / 0.111 | 0.82 / 0.74 | 0.018 / 0.039 |
| 30 days | 0.167 / 0.186 | 0.84 / 0.79 | 0.056 / 0.060 |
| 90 days | 0.075 / 0.102 | 0.90 / 0.83 | 0.073 / 0.048 |

It beat the baseline for both US and India at every horizon. It failed two promotion
gates and was not activated: 90-day calibration (it is under-confident at the top end,
saying about 85% where sales happened 95% of the time), and discount depth (MAE 7.0
points against 6.7; the baseline's per-game history is the better depth predictor).

### Not verified

These could not be run on the development machine because Docker Desktop failed to start
(a Docker "secrets engine" socket error unrelated to this project):

- **The test suite and migrations against PostgreSQL.** The one skipped test is the
  PostgreSQL migration test. Everything ran on SQLite only. The models use portable
  types and the CI workflow runs the full suite on PostgreSQL, but that has not been
  executed yet. Run `TEST_DATABASE_URL=postgresql+asyncpg://... uv run pytest` once
  PostgreSQL is available.
- **Building the Docker image and starting the Compose stack.** The Compose file parses;
  the image has not been built.
- **Redis and a real Celery broker.** Cache, locks and rate limits were exercised with
  the in-memory store, and Celery tasks in eager mode.

Also not exercised, because they need credentials: real IsThereAnyDeal calls (the client
is covered by contract tests against mocked responses shaped from the live OpenAPI
document), real Web Push delivery, Resend, real SMTP and Twilio.

## Phase 0 — Assessment and design

- The repository held only `prompt.md`; there was no existing code to preserve.
- IsThereAnyDeal routes and payload shapes were confirmed against the live OpenAPI
  document before writing the client.
- Design, assumptions and deviations: `docs/architecture.md`.

## Phase 1 — Foundation

FastAPI app factory, settings with feature-scoped validation, JSON logging with
redaction and correlation ids, the error envelope, async SQLAlchemy, Redis abstraction
with locks and rate-limit counters, Alembic, health endpoints, Dockerfile, Compose,
Ruff, mypy, pre-commit, CI workflow, `.env.example`. First-party auth: Argon2id, JWT
access tokens, rotating refresh tokens with reuse detection, email verification.

Exit criteria: migrations apply; health and auth tests pass. **Met on SQLite; Compose
start-up not verified.**

## Phase 2 — Provider and catalogue

`PriceDataProvider` protocol, `IsThereAnyDealProvider`, deterministic `FakePriceProvider`,
read-through cache with per-kind TTLs, bounded retries, `429`/`Retry-After` handling,
batched price reads, Steam resolved from the shop list, search/lookup/metadata/price
APIs, `docs/provider-compliance.md`.

Exit criteria: games resolve without leaking credentials; error and `429` behaviour
tested. **Met.**

## Phase 3 — Watchlist and ingestion

Watchlist CRUD and preferences with owner scoping, restartable history backfill with
watermarks, incremental ingestion, sale-event derivation, price/history/sale-event APIs,
scheduled refresh de-duplicated across users, watchlist summary per currency.

Exit criteria: ingestion is restartable and idempotent; duplicate task execution creates
no duplicate observations. **Met.**

## Phase 4 — Deterministic baseline

Leakage-safe features, cadence and seasonal probabilities, discount tiers, hierarchical
cold-start priors, confidence scoring, templated explanations, immutable forecasts,
versioned recommendation policy, forecast and recommendation APIs.

Exit criteria: the end-to-end workflow runs with no trained model and recommendations
carry reason codes. **Met.**

## Phase 5 — Notifications

Preferences, encrypted push subscriptions, transactional outbox with idempotency keys,
delivery log, Web Push (VAPID), Resend, SMTP/Mailpit, Twilio adapter (off by default),
five alert triggers, cooldowns, per-user limits, quiet hours, digests, test notification.

Exit criteria: an alert is delivered once, retried safely and duplicates are suppressed.
**Met with fake providers; Mailpit path not run (needs Docker).**

## Phase 6 — ML

Dataset builder, LightGBM sale classifiers with isotonic calibration, discount-tier
model, forward-chaining validation with purging, artifact registry with checksums,
promotion gates, activation and rollback, inference with baseline fallback.

Exit criteria: candidate compared with baseline; activation and rollback work; inference
falls back if the artifact fails to load. **Met.**

Result on the synthetic demo catalogue (12 games, 1,476 rows): the candidate **failed**
the promotion gates. At 30 days its Brier score was 0.126 against the baseline's 0.121,
although it was better calibrated (ECE 0.033 against 0.064). It was therefore not
activated. This says nothing about real data; it does show the gates doing their job.

## Phase 7 — Accountability and hardening

Outcome evaluation job, performance/calibration/by-horizon endpoints, policy regret
report, Prometheus metrics, rate limits, indexes, operational documentation.

Exit criteria: forecasts stay immutable; matured forecasts are evaluated reproducibly;
tests, lint and types pass. **Met.**

## Bugs found and fixed by the tests

- A watchlist entry's last recommendation was not saved when an evaluation raised no
  alert, so a later change to `BUY` was never detected.
- A cooldown of 0 hours suppressed alerts instead of disabling the cooldown.
- `horizon_days` on the calibration endpoint rejected valid values.
- Background jobs did not retry when the provider failed while resolving the shop list.
- Metric and log route labels were missing the `/api/v1` prefix.

## Open items

1. Run the suite on PostgreSQL and bring the Compose stack up (blocked on Docker here).
2. Obtain IsThereAnyDeal's confirmation for this use case and a production API key.
3. Verify a sending domain for email; generate VAPID keys.
4. Frontend: service worker for Web Push, attribution display, the
   `/verify-email` page.
5. If SMS is ever enabled: sender registration and inbound STOP handling.
6. Backfill real history, then judge the baseline and any trained model on real data.
7. Not built: password reset, social login, account deletion.
