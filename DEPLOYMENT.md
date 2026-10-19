# Deploying Steam4Caster (free tier)

Four free services, no servers to look after:

```
  browser ──► Vercel project "steam4caster"       Next.js website (web/)
         └──► Vercel project "steam4caster-api"   FastAPI backend, one Python function
                         │            │
                 Neon Postgres   Upstash Redis   cache, locks, rate limits, job queue
                         ▲
  Cloudflare Worker "steam4caster-scheduler" (cron/) ──► POST /internal/jobs/*
```

## Why the scheduler is on Cloudflare

Vercel functions only run while answering a request and are stopped after 60 seconds,
so there is no Celery worker or Beat here. Instead:

- Background work (refresh a game's history, forecast it, check its alerts) is pushed
  onto a queue in Redis (`JOB_QUEUE=kv`).
- Every 10 minutes a Cloudflare Worker cron calls the backend: `/tick` claims the
  periodic jobs that are due (price refresh every 3 hours, backfill hourly, daily
  forecasts and scoring), `/run/{name}` runs each one, `/drain` works through the
  queue, and `/run/dispatch_outbox` delivers notifications last.
- Every call stops starting new work after 20 seconds (`JOB_TIME_BUDGET_SECONDS`).
  One job can itself take about 30 seconds (a game's first full history download), so
  20 + 30 + a cold start stays inside Vercel's 60. Unfinished work stays queued for
  the next call or the next trigger. Measured on the first live run: drain calls of
  41-52 s with a 40 s budget, which is why the budget is 20.
- The calls carry `Authorization: Bearer <CRON_SECRET>`. Without `CRON_SECRET` the
  routes do not exist.

The Worker does no work itself: Cloudflare Workers cannot run pandas, LightGBM or a
Postgres driver. It is only the clock.

**Not available on this setup:** weekly model retraining (it needs minutes and a
persistent disk; train locally and deploy the artifact if a model is ever activated)
and email (needs a verified sending domain; browser push works).

**Alert timing:** a price change is noticed within about 3 hours (the refresh
interval) and delivered within 10 minutes of that.

## One-time setup

### 1. Accounts (all free)

- **Neon** (neon.tech): create a project; copy the **direct** connection string, the
  one *without* `-pooler` in the host. Paste it into `.env.vercel` as `DATABASE_URL`
  exactly as given; the backend converts it for its driver.
- **Upstash** (upstash.com): create a Redis database; copy the `rediss://` URL into
  `.env.vercel` as `REDIS_URL`.
- **Vercel** and **Cloudflare**: sign in from the terminal (below).

`.env.vercel` is git-ignored and holds every production secret. It was generated with
fresh JWT, encryption and cron secrets; the ITAD and VAPID keys were copied from `.env`.

### 2. Create the database tables

Run from the project folder, against Neon:

```bash
uv run --env-file .env.vercel alembic upgrade head
```

Run it again after every deploy that adds a migration.

### 3. Backend (Vercel project "steam4caster-api", repository root)

```bash
npx vercel login
npx vercel link --yes --project steam4caster-api
```

Push every line of `.env.vercel` to the project's Production environment, then
deploy:

```bash
npx vercel deploy --prod
```

Check `https://<api-domain>/health/ready` returns `ok` for the database and Redis.

### 4. Website (Vercel project "steam4caster", root directory `web`)

```bash
npx vercel link --yes --project steam4caster --cwd web
npx vercel env add NEXT_PUBLIC_API_URL production --cwd web
npx vercel deploy --prod --cwd web
```

Then set `CORS_ALLOWED_ORIGINS` and `FRONTEND_BASE_URL` on the API project to the
website's address and redeploy the API.

### 5. Scheduler (Cloudflare Worker, `cron/`)

Set `API_URL` in `cron/wrangler.toml` to the API's address, then:

```bash
cd cron
npx wrangler login
npx wrangler secret put CRON_SECRET
npx wrangler deploy
```

`CRON_SECRET` must be the same value as in `.env.vercel`. Watch runs with
`npx wrangler tail`; each run logs which jobs were due and how much queued work was
drained.

## Region

`vercel.json` pins the backend to `sin1` (Singapore) because the Neon database is in
`ap-southeast-1`. Each page makes dozens of database round trips; from Vercel's
default Mumbai region a game page took about 10 s, from Singapore 0.8 s. If the
database ever moves, move the function region with it.

## Limits to keep in mind

| Limit | Value | Effect here |
| --- | --- | --- |
| Vercel function duration | 60 s | job calls start no new work after 20 s and resume next call |
| Vercel function bundle | 500 MB | the backend's dependencies are about 400 MB on Linux; adding a large library could break deploys |
| Cloudflare free Worker | 50 sub-requests per run | a run makes at most ~22 |
| Neon free compute | suspends when idle | the 10-minute cron leaves gaps for it to sleep |
| IsThereAnyDeal | rate limited | the deals list falls back to the last good copy for 24 h |
