# IsThereAnyDeal API compliance

Steam4Caster's price data comes from the IsThereAnyDeal (ITAD) API. This checklist maps
the usage conditions published at <https://docs.isthereanydeal.com/> to how the backend
behaves, and lists what the project owner must still do by hand.

The conditions summarised below were read from the live documentation while building
this backend. They can change: re-read them before launch and whenever the API changes.

## Action required before public launch

- [ ] **Get confirmation from ITAD for this use case.** Email `api@isthereanydeal.com`,
      describe Steam4Caster (sale-timing forecasts and buy/wait decision support built
      on price history) and ask them to confirm it is acceptable and is not considered a
      competing product. Keep the reply.
- [ ] Register the app and obtain a production API key at
      <https://isthereanydeal.com/apps/my/>. Verify the account email (the documented
      default rate limit depends on it).
- [ ] Set `PROVIDER_USER_AGENT` to identify the deployment with a contact URL or email.
- [ ] Make the frontend show the attribution returned by `GET /api/v1/meta` wherever
      prices appear.
- [ ] Decide commercial terms. The documentation allows commercial use for publicly
      available apps; other private use needs ITAD's approval.

## Conditions and how they are met

| Condition | Status | How |
| --- | --- | --- |
| Attribute the API (link to IsThereAnyDeal or mention the API) | Backend done; **frontend must display it** | `ATTRIBUTION` in `app/providers/pricing/base.py` is returned by `GET /api/v1/meta` and embedded in price and history responses |
| Do not imply affiliation | Done | The attribution text and the API description state there is no affiliation with IsThereAnyDeal or Valve |
| Do not alter supplied data (prices, affiliate tags) | Done | Prices are stored as the provider's integer `amountInt`. Deal URLs are stored and returned byte-for-byte. A test asserts an affiliate URL survives unchanged |
| Do not build a competitor or help competitors | **Needs ITAD confirmation** | The product is forecasting and decision support for a user's own watchlist. There is no deal feed, no cross-store comparison, no browse-all-deals view; only the Steam shop is ingested |
| Cache responses; do not work around rate limits | Done | Read-through cache with long TTLs for stable metadata and short TTLs for prices; batched price reads; a single shared refresh per game and region regardless of how many users watch it |
| Respect rate limiting | Done | `429` and `Retry-After` are honoured; retries are bounded; a long `Retry-After` is handed back to the job queue instead of sleeping in a worker |
| Keep the API key private | Done | Read from `ITAD_API_KEY` on the server, sent as the `ITAD-API-Key` header, never in a URL, never returned by the API, redacted from logs |
| Identify the client | Done | Descriptive `User-Agent` from `PROVIDER_USER_AGENT` |

## Endpoints used

Confirmed against the live OpenAPI document (`https://docs.isthereanydeal.com/openapi.json`).

| Purpose | Endpoint | Notes |
| --- | --- | --- |
| Search | `GET /games/search/v1` | `title`, `results` |
| Lookup | `GET /games/lookup/v1` | `title` or `appid` (Steam app id) |
| Metadata | `GET /games/info/v2` | Steam app id, release date, publishers, tags |
| Current prices | `POST /games/prices/v3` | Body of up to 200 game ids; `country`, `shops` |
| Price history | `GET /games/history/v2` | Always called with an explicit `since`; the default covers only about three months |
| Shops | `GET /service/shops/v1` | Steam is identified by name from this list, not a hard-coded id |

Bundles and giveaways are not used.

## Data handling

- **Stored:** normalized games, the regional price change log, the latest regional
  price, and the provider URL for each deal.
- **Not stored:** raw provider payloads. Normalized records are the source of truth.
- **Derived (ours):** sale events, features, forecasts, recommendations and their
  outcomes.
- **Regions:** each country is fetched separately and kept in the provider's currency
  for that country. Prices are never converted.
- **Cache TTLs** (configurable): search 6 hours, metadata 7 days, shops 24 hours,
  current prices 30 minutes, history 3 hours.
- **Refresh cadence:** watched games every 3 hours by default
  (`SCHEDULE_PRICE_REFRESH_MINUTES`), metadata every 14 days.

## If access is revoked or limited

The provider sits behind the `PriceDataProvider` interface. With the provider
unavailable the API keeps serving stored prices (flagged `is_stale`), existing forecasts
and history; search falls back to the local catalogue; ingestion jobs retry with
backoff. No synthetic data is ever substituted for real data: the fake provider exists
only behind an explicit `PRICE_PROVIDER=fake` and refuses to start in production.
