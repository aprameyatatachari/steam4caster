# API guide for the frontend

The authoritative contract is the OpenAPI document served at `/openapi.json` (interactive
at `/docs`, snapshot in [openapi.json](openapi.json)). This page covers the conventions
and flows that are easier to explain in prose.

## Conventions

- **Base path:** every public route is under `/api/v1`. Health checks are at `/health/*`.
- **Auth:** `Authorization: Bearer <access_token>` on everything except register, login,
  refresh, logout, email confirmation and `/api/v1/meta`.
- **IDs:** games, entries, forecasts and so on use internal UUIDs. Games also carry
  `itad_id` and `steam_app_id`.
- **Money:** always an object `{ "amount_minor": 149900, "currency": "INR", "amount":
  "1499.00" }`. Do arithmetic on `amount_minor`; show `amount`. Never add amounts in
  different currencies.
- **Region:** endpoints take `country` (ISO 3166-1 alpha-2). It defaults to the user's
  `default_country`. Prices for a country are that country's own prices.
- **Shop:** only `steam` is supported.
- **Timestamps:** ISO 8601 in UTC. Send timestamps with an explicit offset.
- **Correlation:** send `X-Request-ID` if you have one; the response always returns it.

## Errors

Every error has the same shape:

```json
{
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "Request validation failed.",
    "correlation_id": "0b7c2f0e-...",
    "details": [{ "loc": ["body", "email"], "msg": "...", "type": "..." }]
  }
}
```

| HTTP | `code` | When |
| --- | --- | --- |
| 401 | `UNAUTHENTICATED` | Missing, invalid or expired token |
| 403 | `FORBIDDEN` | Reserved |
| 404 | `NOT_FOUND` | Unknown id, or a resource owned by another user |
| 409 | `CONFLICT` | Duplicate email, game already on the watchlist |
| 422 | `VALIDATION_ERROR` | Bad input; `details` explains |
| 429 | `RATE_LIMITED` | Too many requests; see `Retry-After` |
| 503 | `UPSTREAM_UNAVAILABLE` | Price provider unavailable; may include `Retry-After` |
| 503 | `FEATURE_UNAVAILABLE` | A channel that is not configured on the server (for example SMS) |

## Authentication flow

1. `POST /auth/register` or `POST /auth/login` → `{ user, tokens }`.
2. Use `tokens.access_token` (15 minutes by default).
3. On a 401, call `POST /auth/refresh` with the refresh token. You get a **new pair**;
   the old refresh token is now invalid. Store the new one immediately.
4. If a refresh token is used twice, the whole session is revoked and the user must log
   in again. Make sure only one refresh request is in flight at a time.
5. `POST /auth/logout` with the refresh token ends the session.

Email verification: registration sends a link to
`{FRONTEND_BASE_URL}/verify-email?token=...`. That page should post the token to
`POST /auth/verify-email/confirm`. `POST /auth/verify-email/request` resends it.

## Start-up: `GET /api/v1/meta`

Unauthenticated. Returns what the client needs before login: the IsThereAnyDeal
attribution to display, the estimate disclaimer, forecast horizons, discount tier codes
and labels, which notification channels the server has configured, and the VAPID public
key for Web Push.

**Display the attribution wherever prices are shown.** It is a condition of the data
provider.

## Screens and the endpoints behind them

### Search and game page

| Need | Endpoint |
| --- | --- |
| Search by title | `GET /games/search?q=` |
| Open from a Steam app id | `GET /games/lookup?steam_app_id=` |
| Metadata | `GET /games/{id}` |
| Current price, discount, historical low | `GET /games/{id}/prices?country=` |
| Price chart | `GET /games/{id}/history?country=&from=&to=&cursor=&limit=` |
| Sale bands for the chart | `GET /games/{id}/sale-events?country=` |
| Forecast | `GET /games/{id}/forecast?country=` |
| Recommendation | `GET /games/{id}/recommendation?country=&max_wait_days=` |
| Past forecasts and outcomes | `GET /games/{id}/forecast-history?country=&cursor=` |

**Charting history.** Items are price *changes* in chronological order, not regular
samples. Draw a step line: each price holds until the next item. Page with `next_cursor`
until it is null.

**Reading a forecast.**

- `sale_probability.days_7 / days_30 / days_90` — chance the game is discounted at any
  point in the window. All three are 1.0 while a sale is running
  (`currently_on_sale: true`).
- `new_sale_probability` — chance a *new* sale starts in the window. Use this when a
  sale is already running.
- `discount_tier_probabilities` — full distribution over the tier codes from `/meta`,
  conditional on a sale. `most_likely_discount_tier` and `expected_discount_pct`
  summarise it.
- `predicted_sale_price` — `lower`, `median`, `upper` for the central 80%, conditional
  on a sale. Null when no regional price is known. Show the range, not just the median.
- `likely_window` — often null. Null means the evidence is too spread out to name a
  window; do not invent one.
- `confidence_score` (0–1) and `data_quality` (`GOOD`, `LIMITED`, `INSUFFICIENT`).
  Show low-confidence forecasts as such.
- `explanation_factors` — ordered by `importance`, each with a `direction` (`BUY`,
  `WAIT`, `NEUTRAL`) and ready-to-display `text`.
- `method` (`BASELINE` or `ML`) and `model_version` — useful for a "how this was made"
  detail.
- `outcome` — null until the horizons expire, then what actually happened.

**Wording.** Present these as estimates ("estimated 72% chance"), never as promises.
The `disclaimer` and each recommendation `summary` are already phrased that way.

**Recommendation.** `action` is `BUY`, `WAIT` or `NEUTRAL`. `summary` is display text.
`reason_codes` are stable machine codes for icons or filtering. `expected_savings` can
be zero or negative. `thresholds` and `ruleset_version` show exactly which policy
applied.

### Watchlist

| Need | Endpoint |
| --- | --- |
| List | `GET /watchlist` |
| Add | `POST /watchlist` |
| One entry | `GET /watchlist/{entry_id}` |
| Edit preferences | `PATCH /watchlist/{entry_id}` |
| Remove | `DELETE /watchlist/{entry_id}` |
| Portfolio summary | `GET /watchlist/summary` |
| Import a public Steam wishlist | `POST /watchlist/import/steam` with `{ "profile": "<link, custom URL name or Steam ID>" }` |

Each entry embeds the game, the current regional price, a forecast summary and the
recommendation. `current`, `forecast` and `recommendation` can be null for a few seconds
after adding a game while the background job runs; poll the entry or show a pending
state.

`PATCH` changes only the fields you send. Send an explicit `null` to clear
`target_price_minor`, `min_discount_pct` or `max_wait_days`.

`target_price_minor` is in the entry's `currency`, which is the region's currency.
Sending a different currency returns 422 with the expected one in `details`.

The summary has one `totals` block per currency: current total, total at recorded
historical lows, and modelled expected totals at 7/30/90 days. The expected figures are
estimates and are flagged `is_estimate`.

### Notifications

| Need | Endpoint |
| --- | --- |
| Read preferences | `GET /notification-preferences` |
| Update preferences | `PATCH /notification-preferences` |
| Register a push subscription | `POST /push-subscriptions` |
| List / revoke subscriptions | `GET /push-subscriptions`, `DELETE /push-subscriptions/{id}` |
| Send a test | `POST /notifications/test` |
| In-app history | `GET /notifications?cursor=` |

Web Push set-up:

1. Read `vapid_public_key` from `/meta`. If it is null, push is not configured.
2. Register a service worker and call
   `registration.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey })`.
3. `POST /push-subscriptions` with `subscription.toJSON()` (optionally add
   `device_label`).
4. The push payload the service worker receives is JSON:
   `{ "title": "...", "body": "...", "tag": "...", "data": { "url": "..." } }`.
   Show a notification and open `data.url` on click.

Each channel in the preferences response has `available` (configured on the server) and
`enabled` (the user's choice). A channel only receives alerts when it is both, and has a
destination: a verified email (if the server requires it), at least one push
subscription, or a verified phone number.

Quiet hours are `quiet_hours_start` / `quiet_hours_end` in the preference's `timezone`
(IANA name). Alerts raised during quiet hours are delivered when they end.
`delivery_mode: "DIGEST"` batches alerts into one message a day.

Notification `state` values: `PENDING`, `SENDING`, `SENT`, `FAILED`, `SUPPRESSED`.
`event_type` values: `TARGET_PRICE`, `MIN_DISCOUNT`, `HISTORICAL_LOW`, `BUY_TRANSITION`,
`SALE_WINDOW_APPROACHING`, `TEST`.

### Model performance

| Need | Endpoint |
| --- | --- |
| Headline accuracy | `GET /model-performance/summary` |
| Reliability diagram | `GET /model-performance/calibration?horizon_days=30&bins=10` |
| Per-horizon metrics | `GET /model-performance/by-horizon` |
| What is in use | `GET /model-versions/active` |

For a reliability diagram plot `mean_predicted` against `observed_rate` per bin, sized by
`count`; a well-calibrated model sits on the diagonal. These endpoints return empty
metrics (`n: 0`) until forecasts are old enough to have outcomes: at least 7 days.
`active: null` means the deterministic baseline is in use.

## Pagination

Cursor-based. Pass the previous response's `next_cursor` as `cursor`. `null` means the
last page. Cursors are opaque; do not build or modify them.

## CORS

The server only answers browsers from origins listed in `CORS_ALLOWED_ORIGINS`. Add the
frontend's origin (scheme, host and port, no trailing slash) there.
