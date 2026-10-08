# Operations

## Processes

| Process | Command | Scale |
| --- | --- | --- |
| API | `uvicorn app.main:app_factory --factory --host 0.0.0.0 --port 8000` | Horizontally |
| Worker | `celery -A app.tasks.celery_app worker --loglevel=INFO` | Horizontally |
| Scheduler | `celery -A app.tasks.celery_app beat --loglevel=INFO --schedule /tmp/celerybeat-schedule` | **Exactly one** |
| Migrations | `alembic upgrade head` | Once per release, before new code starts |

All run from the same image. On Windows the worker needs `--pool=solo`.

Workers acknowledge tasks after completion and every task is idempotent, so a crashed
worker's task is simply run again. API and workers shut down gracefully on `SIGTERM`.

### Single-process mode

With `INLINE_JOBS=true` the API process runs the background jobs itself and no worker or
scheduler is needed: on start-up it brings every watched game up to date and evaluates
its alerts, then delivers notifications every minute and refreshes prices on the usual
interval. `start.bat` uses this when Docker is not running. It is meant for one process
on one machine; use the worker and scheduler for anything larger.

## Schedules

| Task | Default | Does |
| --- | --- | --- |
| `prices.refresh_watched` | every 180 minutes | One batched price read per country for watched games; follow-up work only where the price changed, staggered randomly |
| `prices.backfill_pending` | hourly | Queues history backfill for watched series that have none |
| `forecasts.generate_daily` | 05:10 UTC | Refreshes forecasts for watched series |
| `forecasts.evaluate_outcomes` | 06:00 UTC | Fills outcomes of matured forecasts |
| `catalog.reconcile_metadata` | 03:30 UTC | Refreshes stale game metadata and the shop list |
| `notifications.dispatch_outbox` | every minute | Delivers due notifications, retries transient failures |
| `notifications.send_digests` | hourly | Sends each digest user's batch at their local digest hour |
| `models.train_if_due` | Sunday 02:00 UTC | No-op unless `TRAINING_SCHEDULE_ENABLED=true` and enough new data; never activates |

Change the refresh interval with `SCHEDULE_PRICE_REFRESH_MINUTES` and the stagger with
`SCHEDULE_STAGGER_SECONDS`.

## Health and monitoring

- `GET /health/live` — the process is up.
- `GET /health/ready` — 200 when the database and required configuration are fine. A
  Redis outage reports `degraded` but stays 200: cache reads miss and rate limits fail
  open. 503 only when the database or configuration is unusable.
- `GET /metrics` — Prometheus. Do not expose it publicly; restrict it at the proxy or
  set `METRICS_ENABLED=false`.

Metrics worth alerting on:

| Metric | Watch for |
| --- | --- |
| `s4c_http_requests_total{status=~"5.."}` | Error rate |
| `s4c_http_request_duration_seconds` | Latency |
| `s4c_provider_requests_total{outcome!="ok"}`, `s4c_provider_rate_limited_total` | Provider trouble or rate limiting |
| `s4c_provider_cache_total` | Cache hit ratio dropping |
| `s4c_job_runs_total{outcome="failed"}` | Failing jobs |
| `s4c_job_lag_seconds{job="dispatch_outbox"}` | Notification backlog |
| `s4c_notification_deliveries_total{outcome="failed"}` | Delivery failures |
| `s4c_model_fallbacks_total` | A broken or incompatible model artifact |
| `s4c_alerts_suppressed_total` | Unexpected suppression volume |

Logs are one JSON object per line on stdout with a `correlation_id`. The API returns the
same id in `X-Request-ID` and in every error body, so a user report can be traced.

## Model lifecycle

```bash
uv run python -m app.cli backfill --country IN --watched   # get history first
uv run python -m app.cli evaluate-baseline                 # how good is the baseline?
uv run python -m app.cli train                             # prints metrics and gate results
uv run python -m app.cli list-models
uv run python -m app.cli activate lgbm-YYYYMMDDHHMMSS      # refused if gates failed
uv run python -m app.cli rollback                          # previous model, or baseline
```

Activation and rollback take effect without restarts. `MODEL_ARTIFACT_PATH` must be
shared storage (a volume or mounted bucket) readable by every API and worker instance.
If an instance cannot read or verify the active artifact it logs an error, increments
`s4c_model_fallbacks_total` and serves baseline forecasts.

## Notifications

### Web Push

1. `uv run python -m app.cli generate-vapid-keys`, then set the three `VAPID_*`
   variables. Keep the private key in your secret store. Changing the key pair
   invalidates existing subscriptions.
2. The frontend reads the public key from `GET /api/v1/meta`, subscribes through its
   service worker and posts `PushSubscription.toJSON()` to `/api/v1/push-subscriptions`.
3. Subscriptions that return 404 or 410 are revoked automatically.

Delivery depends on the browser and operating system; the service worker and permission
prompt are frontend work.

### Email

- Local: Mailpit through Compose (`EMAIL_PROVIDER=smtp`), inbox at <http://localhost:8025>.
- Production with Resend: `EMAIL_PROVIDER=resend`, `RESEND_API_KEY`, and `EMAIL_FROM` on
  a **domain you have verified with Resend**. Unverified domains will not deliver.
  Resend's free-tier quotas change; check their current limits rather than relying on
  numbers written here.
- Production with SMTP: set `SMTP_*` and `SMTP_STARTTLS=true`.

Email verification is a security email and is sent regardless of alert preferences. Set
`REQUIRE_VERIFIED_EMAIL_FOR_ALERTS=true` in production so alerts only go to confirmed
addresses.

### SMS (optional)

Off by default; the backend runs fully without Twilio credentials. Before enabling:

- SMS is never free. Twilio trial accounts only reach verified numbers; production
  traffic has per-message and carrier costs that vary by country.
- Many countries require sender registration (for example A2P 10DLC in the US, DLT in
  India). Register before sending.
- Consent: the backend requires a verified number and records explicit opt-in before
  any alert is sent, and messages end with "Reply STOP to opt out". **Inbound STOP
  handling is not implemented**: configure Twilio's built-in opt-out handling, or add a
  webhook that disables the user's SMS preference.
- Verification codes are limited to 3 requests per user per hour.

### Alert behaviour

| Setting | Default | Meaning |
| --- | --- | --- |
| `ALERT_COOLDOWN_HOURS` | 24 | Minimum gap between alerts of the same type for one watchlist entry and channel (0 disables) |
| `ALERT_MAX_PER_USER_PER_DAY` | 20 | Per-user cap across all alerts |
| `ALERT_WINDOW_LEAD_DAYS` | 3 | How far ahead "sale window approaching" fires |
| `ALERT_WINDOW_MIN_CONFIDENCE` | 0.7 | Minimum forecast confidence for that alert |
| `DIGEST_HOUR_LOCAL` | 9 | Local hour digests are sent |
| `OUTBOX_MAX_ATTEMPTS` | 6 | Delivery attempts before a row is marked failed |

Outbox states: `PENDING` → `SENDING` → `SENT` or `FAILED`; `SUPPRESSED` rows were
deliberately not sent (cooldown or daily limit) and record why. A row stuck in `SENDING`
for longer than `OUTBOX_SENDING_TIMEOUT_SECONDS` (a crashed worker) is retried; Resend
de-duplicates such a retry through the idempotency key.

## Rate limits

| Scope | Default | Keyed by |
| --- | --- | --- |
| Authentication | 10 per minute | Client IP |
| Search and lookup | 30 per minute | User |
| Test notification | 3 per hour | User |

Behind a proxy, make sure the application sees the real client address (for example run
uvicorn with `--proxy-headers` and a trusted `--forwarded-allow-ips`), otherwise every
client shares one authentication bucket.

## Security checklist for production

- [ ] `APP_ENV=production` (rejects the fake provider, default secrets and in-memory cache).
- [ ] Strong, unique `JWT_SECRET_KEY` and `SECRETS_ENCRYPTION_KEY` from a secret store.
      Rotating `SECRETS_ENCRYPTION_KEY` makes stored push subscriptions unreadable; users
      re-subscribe.
- [ ] `CORS_ALLOWED_ORIGINS` lists only the real frontend origins.
- [ ] TLS terminated in front of the API; `/metrics` not public.
- [ ] Database and Redis not reachable from the internet; Redis requires authentication.
- [ ] `REQUIRE_VERIFIED_EMAIL_FOR_ALERTS=true`.
- [ ] Database backups and a tested restore.
- [ ] IsThereAnyDeal confirmation obtained (see `provider-compliance.md`).

Logs never contain API keys, JWTs, refresh tokens, password hashes, full push endpoints,
VAPID or SMTP secrets, or phone numbers; email addresses are masked.

## Troubleshooting

| Symptom | Likely cause |
| --- | --- |
| Start-up fails with "Invalid configuration" | The message lists each missing setting for an enabled feature |
| `503 UPSTREAM_UNAVAILABLE` | Provider down or rate limiting; stored data is still served where it exists |
| Prices marked `is_stale` | Refresh is failing; check provider metrics and worker logs |
| No alerts arrive | Is the channel enabled and usable (`GET /notification-preferences`)? Is the worker running? Check `GET /notifications` for `SUPPRESSED` or `FAILED` rows and their `error_code` |
| Forecasts say `BASELINE` after activating a model | Artifact not readable on that instance, or the game has too little history |
| `train` reports too little data | Backfill more games; a handful of series is not enough |
